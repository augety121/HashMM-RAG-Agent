"""检索侧信息检索（IR）评测（V211 差距一）——独立于答案质量的**检索质量**度量。

区别于 `eval_retrieval_policy.py`（端到端 grounding 代理：答案 token 是否在证据里），
本模块做的是经典 IR 指标：给一个 query 和"该命中的 doc_id 金标准"，衡量检索器把
相关文档排到多靠前——Recall@k / Precision@k / nDCG@k / MRR。这是检索器好坏的**硬信号**，
调检索（换切分、加 rerank、改权重）时用它证明"排序确实变好了 X%"，而不是靠感觉。

设计（与项目其它评测一致）：
  · 纯函数、零第三方依赖（数学自己算，装不装 numpy 都能跑）；
  · retrieve_fn 依赖注入——CI/沙箱用 mock，真机传检索管线；
  · 评测集格式：[{"query": "...", "relevant": ["doc_a", "doc_b"], "graded": {"doc_a": 3, "doc_b": 1}}]
    relevant = 相关 doc_id 集合（二元相关性，算 recall/precision/mrr）；
    graded  = 可选的分级相关性（0~N，算 nDCG，缺省时用 relevant 里的都当 1）。

用法：
    from hashmm.evaluation.ir_eval import run_ir_eval
    report = run_ir_eval(cases, retrieve_fn, ks=(1, 3, 5, 10))
    print(report["summary"])   # {"recall@5": 0.78, "ndcg@5": 0.71, "mrr": 0.66, ...}
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, Iterable, Sequence

from hashmm.utils import get_logger

logger = get_logger("hashmm.evaluation.ir_eval")


# ── 单条指标（输入：检索返回的有序 doc_id 列表 + 金标准）──────────────────
def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """前 k 个里命中的相关文档 / 全部相关文档。relevant 为空返回 nan（该条不计）。"""
    if not relevant:
        return float("nan")
    topk = set(ranked[:k])
    return len(topk & relevant) / len(relevant)


def precision_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """前 k 个里相关的比例。k<=0 返回 nan。"""
    if k <= 0:
        return float("nan")
    topk = ranked[:k]
    if not topk:
        return 0.0
    return sum(1 for d in topk if d in relevant) / len(topk)


def mrr(ranked: Sequence[str], relevant: set[str]) -> float:
    """第一个相关文档的倒数排名（1/rank）。没命中返回 0。relevant 为空返回 nan。"""
    if not relevant:
        return float("nan")
    for i, d in enumerate(ranked, start=1):
        if d in relevant:
            return 1.0 / i
    return 0.0


def _dcg(gains: Iterable[float]) -> float:
    return sum(g / math.log2(i + 1) for i, g in enumerate(gains, start=1))


def ndcg_at_k(ranked: Sequence[str], graded: dict[str, float], k: int) -> float:
    """归一化折损累计增益。graded: doc_id→相关性分（0~N）。graded 全空返回 nan。"""
    if not graded:
        return float("nan")
    gains = [float(graded.get(d, 0.0)) for d in ranked[:k]]
    dcg = _dcg(gains)
    ideal = sorted((float(v) for v in graded.values()), reverse=True)[:k]
    idcg = _dcg(ideal)
    if idcg <= 0:
        return 0.0
    return dcg / idcg


# ── 数据加载 ────────────────────────────────────────────────────────────
def load_ir_cases(paths: list[str | Path] | None = None) -> list[dict]:
    """加载 IR 评测集。默认读 hashmm/evaluation/ir_cases.json。"""
    if paths is None:
        paths = [Path(__file__).parent / "ir_cases.json"]
    cases: list[dict] = []
    for p in paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            if isinstance(data, list):
                cases.extend(data)
            elif isinstance(data, dict) and isinstance(data.get("cases"), list):
                cases.extend(data["cases"])
        except Exception as e:
            logger.debug(f"ir_eval load skip {p}: {e}")
    return cases


def _relevant_of(case: dict) -> set[str]:
    v = case.get("relevant") or case.get("relevant_docs") or []
    if isinstance(v, str):
        v = [v]
    return {str(x).strip() for x in v if str(x).strip()}


def _graded_of(case: dict, relevant: set[str]) -> dict[str, float]:
    g = case.get("graded") or case.get("grades")
    if isinstance(g, dict) and g:
        return {str(k).strip(): float(v) for k, v in g.items()}
    # 无分级 → 相关的都当 1（二元退化为分级）
    return {d: 1.0 for d in relevant}


def _extract_doc_ids(results: object) -> list[str]:
    """把 retrieve_fn 的返回归一化成有序 doc_id 列表。兼容多种返回形态：
    - list[str]                         → 直接就是 doc_id 列表
    - list[dict]（带 doc_id/id/source/filename/doc/file）→ 逐个取
    - 带 .doc_id / .source / .id 属性的对象列表（SearchResult 等）
    """
    out: list[str] = []
    if not isinstance(results, (list, tuple)):
        return out
    for r in results:
        if isinstance(r, str):
            out.append(r.strip())
            continue
        if isinstance(r, dict):
            for k in ("doc_id", "id", "source", "filename", "doc", "file", "source_file"):
                v = r.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
                    break
            continue
        for k in ("doc_id", "source", "id", "filename"):
            v = getattr(r, k, None)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
                break
    return out


# ── 评测主入口 ──────────────────────────────────────────────────────────
def run_ir_eval(cases: list[dict], retrieve_fn: Callable[[str, int], object],
                ks: tuple[int, ...] = (1, 3, 5, 10)) -> dict:
    """跑 IR 评测。retrieve_fn(query, top_k) → 结果列表（任意上述形态）。

    返回 {"n": 条数, "summary": {各指标宏平均}, "per_case": [...]}。
    宏平均 = 对每条 case 算指标后取均值（nan 的不计入该指标）。
    """
    maxk = max(ks) if ks else 10
    per_case: list[dict] = []
    acc: dict[str, list[float]] = {}

    def _add(metric: str, val: float):
        if val == val:  # 过滤 nan
            acc.setdefault(metric, []).append(val)

    for c in cases:
        q = str(c.get("query", "")).strip()
        if not q:
            continue
        relevant = _relevant_of(c)
        graded = _graded_of(c, relevant)
        try:
            raw = retrieve_fn(q, maxk)
        except TypeError:
            raw = retrieve_fn(q)   # 兼容只收 query 的 retrieve_fn
        except Exception as e:
            logger.debug(f"ir_eval retrieve failed for {q[:20]}: {e}")
            raw = []
        ranked = _extract_doc_ids(raw)
        row: dict = {"query": q, "n_relevant": len(relevant), "n_retrieved": len(ranked)}
        for k in ks:
            r = recall_at_k(ranked, relevant, k)
            p = precision_at_k(ranked, relevant, k)
            nd = ndcg_at_k(ranked, graded, k)
            row[f"recall@{k}"] = None if r != r else round(r, 4)
            row[f"precision@{k}"] = None if p != p else round(p, 4)
            row[f"ndcg@{k}"] = None if nd != nd else round(nd, 4)
            _add(f"recall@{k}", r)
            _add(f"precision@{k}", p)
            _add(f"ndcg@{k}", nd)
        m = mrr(ranked, relevant)
        row["mrr"] = None if m != m else round(m, 4)
        _add("mrr", m)
        per_case.append(row)

    summary = {metric: round(sum(vals) / len(vals), 4) for metric, vals in acc.items() if vals}
    return {"n": len(per_case), "summary": summary, "per_case": per_case}


def format_ir_markdown(report: dict, baseline: dict | None = None) -> str:
    """IR 报告 → Markdown（可对比基线，和答案 gate 的 diff 表同风格）。"""
    summ = report.get("summary", {})
    base = (baseline or {}).get("summary", {}) if baseline else {}

    def arrow(cur, b):
        if b is None or not isinstance(b, (int, float)) or not isinstance(cur, (int, float)):
            return "—"
        d = cur - b
        if d > 0.0005:
            return f"▲ +{d:.4f}"
        if d < -0.0005:
            return f"▼ {d:.4f}"
        return "= 0"

    keys = sorted(summ.keys(), key=lambda k: (k.split("@")[0], int(k.split("@")[1]) if "@" in k else 0))
    lines = [f"检索 IR 评测（n={report.get('n', 0)}）", "", "| 指标 | 分数 | vs 基线 |", "|---|---|---|"]
    for k in keys:
        cur = summ[k]
        lines.append(f"| {k} | {cur:.4f} | {arrow(cur, base.get(k))} |")
    return "\n".join(lines)


# ── CLI（与 gate 同风格：一条命令跑检索 IR 评测）──────────────────────────
if __name__ == "__main__":
    import argparse
    import sys as _sys

    ap = argparse.ArgumentParser(
        description="HashMM 检索 IR 评测：对 query→相关 doc_id 金标准跑 Recall@k / nDCG / MRR。")
    ap.add_argument("--cases", nargs="*", default=None, help="IR 评测集 JSON（默认 ir_cases.json）")
    ap.add_argument("--ks", default="1,3,5,10", help="逗号分隔的 k 值（默认 1,3,5,10）")
    ap.add_argument("--baseline", default=None, help="基线 JSON（用于分数对比）")
    ap.add_argument("--update-baseline", action="store_true", help="把本次 summary 写成基线")
    ap.add_argument("--stub", action="store_true", help="用桩检索器自检 harness（CI 用，无需索引）")
    args = ap.parse_args()

    ks = tuple(int(x) for x in str(args.ks).split(",") if x.strip())
    cases = load_ir_cases(args.cases)
    if not cases:
        print("ir-eval: 加载不到评测集")
        _sys.exit(2)

    if args.stub:
        # 桩：把每条金标准原样返回 → 验证 harness 接线（不据分数判定）
        def _stub(q, k=10):
            for c in cases:
                if c.get("query") == q:
                    return list((c.get("graded") or {c.get("relevant", [None])[0]: 1}).keys())[:k]
            return []
        rep = run_ir_eval(cases, _stub, ks=ks)
        print(f"ir-eval selftest: harness OK（{rep['n']} 条，接线正常）")
        print(format_ir_markdown(rep))
        _sys.exit(0)

    # 真机：接检索管线
    try:
        from hashmm.retrieval_pipeline import build_pipeline  # type: ignore
        pipe = build_pipeline()
        def _retrieve(q, k=10):
            return pipe.search(q, top_k=k)
    except Exception as e:
        print(f"ir-eval: 无法构建检索管线（{e}）。用 --stub 自检，或在能加载索引的环境运行。")
        _sys.exit(2)

    baseline = None
    if args.baseline and not args.update_baseline:
        try:
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        except Exception:
            baseline = None
    rep = run_ir_eval(cases, _retrieve, ks=ks)
    print(json.dumps({"n": rep["n"], "summary": rep["summary"]}, ensure_ascii=False, indent=2))
    print("\n" + format_ir_markdown(rep, baseline))
    if args.update_baseline and args.baseline:
        Path(args.baseline).write_text(json.dumps({"summary": rep["summary"]},
                                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nir-eval: 基线已写入 {args.baseline}")
