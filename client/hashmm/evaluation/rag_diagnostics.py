"""Evidence-backed RAG failure-pattern diagnosis.

The pattern catalogue is adapted to HashMM's real run evidence.  A pattern is
only reported as observed when the manifest contains a measurable signal;
causes such as embedding mismatch or tenant interference remain explicitly
not-evaluable instead of being guessed from a poor answer.
"""
from __future__ import annotations

from typing import Any, Iterable


SCHEMA = "hashmm.rag-diagnostics.v1"


def diagnose_run(*, sources: Iterable[dict] | None, groundings: dict | None,
                 corpus: dict | None, iterations: int = 0,
                 tool_steps: Iterable[dict] | None = None) -> dict[str, Any]:
    sources = [item for item in (sources or []) if isinstance(item, dict)]
    ledger = dict(groundings or {})
    corpus = dict(corpus or {})
    tool_steps = [item for item in (tool_steps or []) if isinstance(item, dict)]
    observed: list[dict[str, str]] = []

    total = int(ledger.get("total_factual_claims") or 0)
    supported = int(ledger.get("supported_claims") or 0)
    coverage = ledger.get("coverage_ratio")
    if sources and total and (ledger.get("review_required") or
                              (isinstance(coverage, (int, float)) and coverage < 0.65)):
        observed.append({
            "id": "P01_grounding_drift", "severity": "high",
            "evidence": f"{supported}/{total} 个事实主张有可解析证据锚点",
            "action": "回到未支持主张，补检索或明确删除；不得用模型自评分替代来源。",
        })

    lengths = [len(str(item.get("text") or "").strip()) for item in sources]
    short = sum(1 for length in lengths if 0 < length < 120)
    if len(lengths) >= 3 and short * 2 >= len(lengths):
        observed.append({
            "id": "P02_chunk_boundary", "severity": "medium",
            "evidence": f"{short}/{len(lengths)} 个证据片段短于 120 字",
            "action": "检查父子分块与相邻块扩展；保留原文定位后再合并上下文。",
        })

    if iterations >= 10 and sources and ledger.get("review_required"):
        observed.append({
            "id": "P06_long_chain_drift", "severity": "high",
            "evidence": f"运行经过 {iterations} 次迭代后仍需事实复核",
            "action": "按子目标固定证据快照，并在每个阶段重新执行完成门。",
        })

    actual_tools = [item for item in tool_steps if item.get("tool") or item.get("name")]
    failed = [item for item in actual_tools if str(item.get("status") or "").lower()
              in {"error", "failed", "fail", "denied", "blocked", "stopped"}]
    if actual_tools and failed:
        observed.append({
            "id": "P07_tool_reliability", "severity": "high" if len(failed) * 2 >= len(actual_tools) else "medium",
            "evidence": f"{len(failed)}/{len(actual_tools)} 个工具执行失败或被拒绝",
            "action": "先修复工具参数、权限或依赖，再运行未完成步骤；不要把失败输出当作结果。",
        })

    if sources and not ledger.get("semantic_entailment_verified", False):
        observed.append({
            "id": "P09_eval_blind_spot", "severity": "medium",
            "evidence": "本轮只验证了引用锚点，没有独立语义蕴含证据",
            "action": "对高风险结论做独立来源或人工复核；界面继续标为待复核。",
        })

    if sources and corpus.get("status") != "pinned":
        observed.append({
            "id": "P11_config_reproducibility", "severity": "medium",
            "evidence": "本轮只有证据集合指纹，没有固定索引构建快照",
            "action": "重建索引时写入模型、维度、语料与构建时间指纹。",
        })

    not_evaluable = [
        {"id": "P03_embedding_mismatch", "reason": "单轮来源不能证明索引与查询编码器不匹配"},
        {"id": "P04_stale_index", "reason": "未提供源文档版本与索引版本对照"},
        {"id": "P08_memory_leak", "reason": "单轮清单不包含跨账号记忆污染审计"},
        {"id": "P12_multi_tenant_interference", "reason": "需由 owner 边界测试与服务审计证明"},
    ]
    return {
        "schema": SCHEMA,
        "status": "attention" if observed else ("clear" if sources else "not_evaluable"),
        "observed": observed,
        "not_evaluable": not_evaluable,
        "integrity": {"model_self_report_used": False, "causes_inferred_without_signal": False},
    }


__all__ = ["SCHEMA", "diagnose_run"]
