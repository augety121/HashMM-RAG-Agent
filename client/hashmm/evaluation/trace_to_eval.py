"""Trace → 评测集（V205 P2-10，图2-⑤的第二价值）。

把真实流量沉淀成回归评测集：从会话库取 (用户问题, 助手回答) 对，
按 conv_id 联上 trace JSONL 里最近的 gen_ai_request（检索模式/来源数/
时延/finish_reason），导出一行一样本的 JSONL——每次改 prompt / 检索权重，
`evaluation/gate.py` 就有真实分布的基线可回归，而不是手造十条测例。

CLI：
    python -m hashmm.evaluation.trace_to_eval --limit 200 -o data/eval/from_trace.jsonl

样本行：
    {"conv_id", "query", "answer", "ts",
     "trace": {"retrieval_mode", "n_sources", "latency_ms", "finish_reason", "model"}}

依赖注入友好：`build_samples(fetch_convs, fetch_msgs, traces)` 纯函数，
离线可测；CLI 入口才绑真实 db/observability。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from hashmm.utils import get_logger

logger = get_logger("hashmm.evaluation.trace_to_eval")


def _index_traces(traces: list[dict]) -> dict[str, dict]:
    """conv_id → 最近一条 gen_ai_request 的关键指标。"""
    idx: dict[str, dict] = {}
    for r in traces:
        if r.get("kind") != "gen_ai_request":
            continue
        cid = str(r.get("conv_id", "") or "")
        if not cid:
            continue
        idx[cid] = {
            "retrieval_mode": r.get("hashmm.retrieval.mode", r.get("retrieval_mode", "")),
            "n_sources": r.get("hashmm.retrieval.n_sources", r.get("n_sources", 0)),
            "latency_ms": r.get("hashmm.latency.total_ms", r.get("total_latency_ms", 0)),
            "finish_reason": (r.get("gen_ai.response.finish_reasons") or [""])[0]
                             if isinstance(r.get("gen_ai.response.finish_reasons"), list)
                             else r.get("finish_reason", ""),
            "model": r.get("gen_ai.request.model", r.get("model", "")),
        }
    return idx


def build_samples(convs: list[dict], fetch_msgs, traces: list[dict],
                  per_conv: int = 3, min_q: int = 4, min_a: int = 20) -> list[dict]:
    """纯函数：会话列表 + 消息取函数 + trace 列表 → 评测样本列表。

    per_conv：每会话最多取最近几对问答；min_q/min_a：问题/回答最短长度过滤
    （拦掉"嗯""继续"这类无评测价值的样本）。
    """
    tindex = _index_traces(traces)
    samples: list[dict] = []
    for conv in convs:
        cid = str(conv.get("id", "") or conv.get("conv_id", ""))
        if not cid:
            continue
        try:
            msgs = fetch_msgs(cid) or []
        except Exception:
            continue
        pairs: list[tuple[dict, dict]] = []
        pending_q: dict | None = None
        for m in msgs:
            role = m.get("role", "")
            if role == "user":
                pending_q = m
            elif role == "assistant" and pending_q is not None:
                if m.get("status") not in ("error",):
                    pairs.append((pending_q, m))
                pending_q = None
        for q, a in pairs[-per_conv:]:
            qt = str(q.get("content", "")).strip()
            at = str(a.get("content", "")).strip()
            if len(qt) < min_q or len(at) < min_a:
                continue
            samples.append({
                "conv_id": cid,
                "query": qt[:2000],
                "answer": at[:6000],
                "ts": a.get("created_at") or a.get("ts") or 0,
                "trace": tindex.get(cid, {}),
            })
    return samples


def export(path: str | Path, limit: int = 200, per_conv: int = 3) -> dict:
    """绑定真实 db/observability 导出。返回 {count, path}。"""
    from hashmm.api import database as db
    from hashmm import observability as ob
    convs: list[dict] = []
    try:
        # 尽力取全量会话（跨用户，评测视角）；接口不支持则退化为空
        with db.get_conn() as c:  # type: ignore[attr-defined]
            rows = c.execute("SELECT id FROM conversations ORDER BY updated_at DESC LIMIT ?",
                             (int(limit),)).fetchall()
            convs = [{"id": r[0]} for r in rows]
    except Exception:
        try:
            convs = db.list_conversations("anonymous", limit=limit)  # 保底
        except Exception:
            convs = []
    traces = ob.trace_tail(2000, kinds=("gen_ai_request",))
    samples = build_samples(convs, lambda cid: db.get_messages(cid, limit=40), traces,
                            per_conv=per_conv)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
    logger.info(f"trace_to_eval: {len(samples)} samples → {out}")
    return {"count": len(samples), "path": str(out)}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="把真实会话+trace 导出为评测集 JSONL")
    ap.add_argument("-o", "--out", default=f"data/eval/from_trace_{time.strftime('%Y%m%d')}.jsonl")
    ap.add_argument("--limit", type=int, default=200, help="最多扫描多少个会话")
    ap.add_argument("--per-conv", type=int, default=3, help="每会话最多取几对问答")
    args = ap.parse_args()
    r = export(args.out, limit=args.limit, per_conv=args.per_conv)
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
