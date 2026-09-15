"""v17 Phase 93 — knowledge-graph quality metrics & health report ("立尺子").

Why: after Phase 92 added recall switches (gleanings / rich few-shot / json_repair)
we need a *ruler* to see whether a rebuild actually got better — more entities,
denser graph, fewer fragments, less anaphoric noise ("本集团", "本公司董事"). This
module computes a structural + quality report straight from a ``KnowledgeGraph``
(NetworkX-backed), with **zero new dependencies** (only networkx, already used).

It never raises (returns a partial report on any error) and changes no existing
behaviour — it only *reads* a graph. Use it via:
  * ``kg_health_report(kg)`` → dict of metrics
  * ``compare_reports(before, after)`` → deltas
  * ``format_report(report)`` → Chinese text
  * CLI: ``python -m hashmm.kg.kg_metrics`` (loads the saved KG and prints)
  * route: ``GET /api/kg/health`` (admin)
"""
from __future__ import annotations

import re
from typing import Any

# ── Noise / fragment heuristics ──────────────────────────────────────────────
# Anaphoric / generic references that should have been resolved to a real entity.
_ANAPHORIC = re.compile(r"^(本公司|本集团|该公司|该集团|本行|本基金|本报告|公司|集团|本企业|该行|我司|本机构)$")
# Generic role/term fragments that aren't a specific named entity on their own.
_GENERIC = re.compile(
    r"^(董事|董事会|监事|监事会|股东|股东大会|高管|管理层|员工|客户|用户|供应商|"
    r"相关服务|相关业务|有关方面|其他|上述|本公司董事|独立董事|执行董事|非执行董事|"
    r"委员会|审核委员会|提名委员会|薪酬委员会)$"
)
# Sentence-fragment tails — a real entity name rarely ends in these.
_FRAGMENT_TAIL = ("的", "了", "在", "是", "和", "及", "与", "或", "为", "对", "把", "被", "等")
# Pure number / date / percent / money that leaked in as an entity.
_NUMERIC_LEAK = re.compile(r"^[0-9０-９.,%％\-—年月日季度第Q万亿元％\s]+$")

_MAX_NAME_LEN = 20  # names longer than this are almost always sentence fragments


def _name_of(data: dict) -> str:
    return str(data.get("name", "")).strip()


def classify_noise(name: str) -> str | None:
    """Return a noise category for an entity name, or None if it looks clean."""
    if not name:
        return "empty"
    if _ANAPHORIC.match(name):
        return "anaphoric"
    if _GENERIC.match(name):
        return "generic"
    if _NUMERIC_LEAK.match(name):
        return "numeric_leak"
    if len(name) == 1:
        return "single_char"
    if len(name) > _MAX_NAME_LEN:
        return "too_long"
    if name.endswith(_FRAGMENT_TAIL):
        return "fragment_tail"
    return None


def kg_health_report(kg: Any) -> dict:
    """Compute a structural + quality report from a KnowledgeGraph. Never raises."""
    report: dict = {"ok": True}
    try:
        g = kg.graph
        n = g.number_of_nodes()
        m = g.number_of_edges()
        report["entities"] = n
        report["relations"] = m
        report["avg_degree"] = round((2.0 * m / n), 3) if n else 0.0
        # density on a directed graph: m / (n*(n-1))
        report["density"] = round(m / (n * (n - 1)), 6) if n > 1 else 0.0

        # connected components (treat as undirected for reachability)
        try:
            import networkx as nx
            ug = g.to_undirected()
            comps = list(nx.connected_components(ug))
            report["components"] = len(comps)
            report["largest_component"] = max((len(c) for c in comps), default=0)
            report["isolated"] = sum(1 for _, d in g.degree() if d == 0)
        except Exception:
            report["components"] = None
            report["largest_component"] = None
            report["isolated"] = None

        # type distribution + noise scan
        type_counts: dict[str, int] = {}
        noise_counts: dict[str, int] = {}
        noisy_names: list[str] = []
        degrees = []
        for node, data in g.nodes(data=True):
            t = str(data.get("type", "?")) or "?"
            type_counts[t] = type_counts.get(t, 0) + 1
            name = _name_of(data) or str(node)
            cat = classify_noise(name)
            if cat:
                noise_counts[cat] = noise_counts.get(cat, 0) + 1
                if len(noisy_names) < 30:
                    noisy_names.append(f"{name}［{cat}］")
            try:
                degrees.append((g.degree(node), name, t))
            except Exception:
                pass

        report["type_distribution"] = dict(
            sorted(type_counts.items(), key=lambda kv: -kv[1])
        )
        noisy_total = sum(noise_counts.values())
        report["noise"] = {
            "total": noisy_total,
            "rate": round(noisy_total / n, 4) if n else 0.0,
            "by_category": dict(sorted(noise_counts.items(), key=lambda kv: -kv[1])),
            "examples": noisy_names,
        }
        degrees.sort(key=lambda x: -x[0])
        report["top_entities"] = [
            {"name": nm, "degree": d, "type": t} for d, nm, t in degrees[:15]
        ]
    except Exception as e:  # never raise from a read-only report
        report["ok"] = False
        report["error"] = str(e)
    return report


def compare_reports(before: dict, after: dict) -> dict:
    """Delta between two health reports (after − before) for the headline numbers."""
    def d(key, sub=None):
        try:
            a = before[key][sub] if sub else before[key]
            b = after[key][sub] if sub else after[key]
            return round(b - a, 4)
        except Exception:
            return None
    return {
        "entities": d("entities"),
        "relations": d("relations"),
        "avg_degree": d("avg_degree"),
        "density": d("density"),
        "components": d("components"),
        "noise_total": d("noise", "total"),
        "noise_rate": d("noise", "rate"),
    }


def format_report(r: dict) -> str:
    """Human-readable Chinese summary of a health report."""
    if not r.get("ok", False):
        return f"KG 健康报告生成失败：{r.get('error', '未知')}"
    lines = [
        "── KG 健康报告 ──",
        f"实体 {r.get('entities')} ｜ 关系 {r.get('relations')} ｜ 平均度 {r.get('avg_degree')} ｜ 密度 {r.get('density')}",
        f"连通分量 {r.get('components')}（最大 {r.get('largest_component')}）｜ 孤立节点 {r.get('isolated')}",
        f"类型分布：{r.get('type_distribution')}",
    ]
    noise = r.get("noise", {})
    lines.append(
        f"噪声实体 {noise.get('total')}（{round(100 * (noise.get('rate') or 0), 1)}%）"
        f" 分类：{noise.get('by_category')}"
    )
    if noise.get("examples"):
        lines.append("噪声示例：" + "、".join(noise["examples"][:12]))
    top = r.get("top_entities", [])
    if top:
        lines.append("高连接实体：" + "、".join(f"{e['name']}({e['degree']})" for e in top[:10]))
    return "\n".join(lines)


def _load_saved_kg():
    from hashmm.kg.storage import KGStorage
    kg, _ = KGStorage().load()
    return kg


if __name__ == "__main__":  # CLI: print the report for the currently saved KG
    try:
        _kg = _load_saved_kg()
        print(format_report(kg_health_report(_kg)))
    except Exception as e:  # pragma: no cover
        print(f"无法加载已保存的 KG：{e}")
