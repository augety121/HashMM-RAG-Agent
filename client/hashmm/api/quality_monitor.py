"""Online quality monitoring (V16 Phase 14).

Offline eval (Phase 8/13) tests a fixed golden set. But the real question is:
"what quality are ACTUAL users getting?" This module samples real conversation
turns, records lightweight quality signals (groundedness ratio, citation count,
source count, latency), and aggregates them into a live dashboard.

Sampling is rate-limited (every Nth turn) so it adds negligible overhead.
Storage is a rolling table; this is monitoring, not an audit log.
"""
from __future__ import annotations

import os
import json
import time
import uuid

from hashmm.utils import get_logger

logger = get_logger("hashmm.quality_monitor")

# Sample 1 in N turns (configurable). 1 = every turn.
_SAMPLE_RATE = int(os.environ.get("HASHMM_QUALITY_SAMPLE_RATE", "5"))
_counter = 0


def _ensure_table():
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS quality_samples (
                id            TEXT PRIMARY KEY,
                user_id       TEXT DEFAULT '',
                query         TEXT DEFAULT '',
                task_type     TEXT DEFAULT '',
                num_sources   INTEGER DEFAULT 0,
                num_citations INTEGER DEFAULT 0,
                grounded_ratio DOUBLE PRECISION DEFAULT 1.0,
                answer_len    INTEGER DEFAULT 0,
                elapsed_ms    INTEGER DEFAULT 0,
                ts            DOUBLE PRECISION DEFAULT (strftime('%s','now'))
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_qs_ts ON quality_samples(ts DESC)")


def should_sample() -> bool:
    global _counter
    _counter += 1
    return (_counter % max(_SAMPLE_RATE, 1)) == 0


def record_sample(user_id: str, query: str, answer: str, sources: list,
                  task_type: str = "", elapsed_ms: int = 0) -> None:
    """Record a lightweight quality sample (best-effort, never raises)."""
    try:
        _ensure_table()
        from hashmm.generation.groundedness import citation_overlap_check
        from hashmm.api.core.citation_validator import extract_cited_indices
        g = citation_overlap_check(answer or "", sources or [])
        n_cite = len(extract_cited_indices(answer or ""))
        # V308：三态落库。ratio 为 None（not_evaluable，无引用可核验）时存 NULL，
        # 让 SQL 的 AVG 自动跳过它，而非把"无从判断"当 1.0 满分拉高均值。
        _ratio = g.get("ratio")   # 可能是 None
        from hashmm.api import database as db
        with db._conn() as c:
            c.execute(
                """INSERT INTO quality_samples
                   (id,user_id,query,task_type,num_sources,num_citations,
                    grounded_ratio,answer_len,elapsed_ms)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex[:12], user_id, (query or "")[:300], task_type,
                 len(sources or []), n_cite, _ratio,
                 len(answer or ""), elapsed_ms),
            )
    except Exception as e:
        logger.debug(f"quality sample skipped: {e}")


def runtime_tool_metrics(manifests, *, wrong_tool_feedback: int = 0,
                         truncated: bool = False) -> dict:
    """Aggregate deterministic tool execution checks from run manifests.

    This answers whether a tool process returned success, not whether the model
    selected the semantically correct tool.  Selection/argument correctness is
    measured by the strict offline tool-call evaluator and explicit reviewed
    ``wrong_tool`` feedback, so the dashboard cannot turn HTTP 200 into a fake
    correctness claim.
    """
    manifest_count = evaluable = passed = failed = total_calls = failed_calls = 0
    for raw in manifests or []:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                continue
        if not isinstance(raw, dict) or not isinstance(raw.get("verification"), dict):
            continue
        manifest_count += 1
        checks = raw["verification"].get("checks") or []
        tool_check = next((c for c in checks if isinstance(c, dict) and c.get("id") == "tool_execution"), None)
        if not tool_check:
            continue
        status = str(tool_check.get("status") or "").lower()
        evidence = tool_check.get("evidence") if isinstance(tool_check.get("evidence"), dict) else {}
        if status in {"passed", "failed"}:
            evaluable += 1
            passed += 1 if status == "passed" else 0
            failed += 1 if status == "failed" else 0
            total_calls += max(0, int(evidence.get("total") or 0))
            failed_calls += max(0, int(evidence.get("failed") or 0))
    return {
        "run_manifests": manifest_count,
        "evaluable_runs": evaluable,
        "not_evaluable_runs": max(0, manifest_count - evaluable),
        "passed_runs": passed,
        "failed_runs": failed,
        "tool_calls": total_calls,
        "failed_tool_calls": failed_calls,
        "execution_success_rate": (round((total_calls - failed_calls) / total_calls, 3)
                                   if total_calls else None),
        "wrong_tool_feedback": max(0, int(wrong_tool_feedback or 0)),
        "measures_correct_selection": False,
        "scope": ("这里只衡量运行时返回状态；工具名、参数值、调用顺序和不调用反例"
                  "由严格离线评测与人工复核反馈判定。"),
        "truncated": bool(truncated),
    }


def runtime_graph_metrics(manifests, *, truncated: bool = False) -> dict:
    """Aggregate task-evidence graphs without treating graph size as quality.

    The useful signals are trace coverage and unresolved blockers.  Node or
    edge counts are observability only: a dense graph is never promoted into a
    correctness score.
    """
    manifest_count = graph_runs = blocked_runs = total_nodes = total_edges = total_blockers = 0
    coverage_values: list[float] = []
    integrity_violations = 0
    for raw in manifests or []:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                continue
        if not isinstance(raw, dict):
            continue
        manifest_count += 1
        graph = raw.get("evidence_graph")
        if not isinstance(graph, dict) or graph.get("schema") != "hashmm.task-evidence-graph.v1":
            continue
        graph_runs += 1
        summary = graph.get("summary") if isinstance(graph.get("summary"), dict) else {}
        total_nodes += max(0, int(summary.get("nodes") or 0))
        total_edges += max(0, int(summary.get("edges") or 0))
        blockers = max(0, int(summary.get("blockers") or 0))
        total_blockers += blockers
        blocked_runs += 1 if blockers or graph.get("status") == "blocked" else 0
        coverage = summary.get("claim_evidence_coverage")
        if isinstance(coverage, (int, float)) and 0 <= float(coverage) <= 1:
            coverage_values.append(float(coverage))
        integrity = graph.get("integrity") if isinstance(graph.get("integrity"), dict) else {}
        if (integrity.get("construction") != "deterministic_runtime_facts"
                or int(integrity.get("model_inferred_edges") or 0) != 0
                or integrity.get("owner_data_included") is not False):
            integrity_violations += 1
    return {
        "run_manifests": manifest_count,
        "graph_runs": graph_runs,
        "missing_graph_runs": max(0, manifest_count - graph_runs),
        "trace_coverage": round(graph_runs / manifest_count, 3) if manifest_count else None,
        "blocked_runs": blocked_runs,
        "open_blockers": total_blockers,
        "avg_nodes": round(total_nodes / graph_runs, 1) if graph_runs else None,
        "avg_edges": round(total_edges / graph_runs, 1) if graph_runs else None,
        "claim_evidence_coverage": (round(sum(coverage_values) / len(coverage_values), 3)
                                    if coverage_values else None),
        "claim_evaluable_runs": len(coverage_values),
        "integrity_violations": integrity_violations,
        "graph_density_is_quality_score": False,
        "scope": ("任务图只汇总运行事实、证据连接和未闭环项；节点/边数量不代表答案正确，"
                  "事实正确性仍由来源账本、确定性检查和人工复核判定。"),
        "truncated": bool(truncated),
    }


def runtime_frontier_metrics(manifests, *, truncated: bool = False) -> dict:
    """Aggregate route readiness without presenting it as answer quality.

    A ready route only proves that a capability exists inside the persisted
    execution scope.  It is neither tool execution evidence nor approval.
    """
    manifest_count = frontier_runs = converged_runs = actionable_runs = 0
    unresolved = ready_routes = scope_blocked = waiting_input = 0
    integrity_violations = 0
    for raw in manifests or []:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                continue
        if not isinstance(raw, dict):
            continue
        manifest_count += 1
        frontier = raw.get("execution_frontier")
        if not isinstance(frontier, dict) or frontier.get("schema") != "hashmm.execution-frontier.v1":
            continue
        frontier_runs += 1
        status = str(frontier.get("status") or "")
        converged_runs += 1 if status == "converged" else 0
        actionable_runs += 1 if status == "actionable" else 0
        summary = frontier.get("summary") if isinstance(frontier.get("summary"), dict) else {}
        unresolved += max(0, int(summary.get("unresolved") or 0))
        ready_routes += max(0, int(summary.get("ready_routes") or 0))
        scope_blocked += max(0, int(summary.get("scope_blocked") or 0))
        waiting_input += max(0, int(summary.get("waiting_input") or 0))
        integrity = frontier.get("integrity") if isinstance(frontier.get("integrity"), dict) else {}
        if (integrity.get("construction") != "deterministic_graph_and_capabilities"
                or integrity.get("auto_executes") is not False
                or integrity.get("widens_scope") is not False
                or int(integrity.get("model_selected_routes") or 0) != 0):
            integrity_violations += 1
    return {
        "run_manifests": manifest_count,
        "frontier_runs": frontier_runs,
        "missing_frontier_runs": max(0, manifest_count - frontier_runs),
        "trace_coverage": round(frontier_runs / manifest_count, 3) if manifest_count else None,
        "converged_runs": converged_runs,
        "actionable_runs": actionable_runs,
        "unresolved_items": unresolved,
        "ready_routes": ready_routes,
        "scope_blocked_items": scope_blocked,
        "waiting_input_items": waiting_input,
        "route_coverage": round(ready_routes / unresolved, 3) if unresolved else None,
        "integrity_violations": integrity_violations,
        "ready_route_is_execution_or_approval": False,
        "scope": "执行前沿只衡量阻塞项是否存在当前范围内的最小路线；不衡量答案正确性，也不会自动执行或扩大权限。",
        "truncated": bool(truncated),
    }


def runtime_completion_gate_metrics(manifests, *, truncated: bool = False) -> dict:
    """Aggregate evidence-gated outcomes and reject unsafe completion claims."""
    manifest_count = gate_runs = unsafe_claims = integrity_violations = 0
    statuses = {name: 0 for name in (
        "verified", "delivered_with_limits", "incomplete", "blocked", "unavailable",
    )}
    required = passed = failed = review = missing = 0
    failure_modes: dict[str, int] = {}
    repeated_call_runs = 0
    for raw in manifests or []:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                continue
        if not isinstance(raw, dict):
            continue
        manifest_count += 1
        gate = raw.get("completion_gate")
        if not isinstance(gate, dict) or gate.get("schema") != "hashmm.completion-gate.v1":
            continue
        gate_runs += 1
        status = str(gate.get("status") or "unavailable")
        statuses[status if status in statuses else "unavailable"] += 1
        summary = gate.get("summary") if isinstance(gate.get("summary"), dict) else {}
        run_required = max(0, int(summary.get("required") or 0))
        run_passed = max(0, int(summary.get("passed") or 0))
        run_failed = max(0, int(summary.get("failed") or 0))
        run_review = max(0, int(summary.get("review") or 0))
        run_missing = max(0, int(summary.get("missing") or 0))
        required += run_required
        passed += run_passed
        failed += run_failed
        review += run_review
        missing += run_missing
        modes = gate.get("failure_modes") if isinstance(gate.get("failure_modes"), list) else []
        for item in modes:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "unknown")[:80]
            failure_modes[code] = failure_modes.get(code, 0) + 1
        trajectory = gate.get("trajectory") if isinstance(gate.get("trajectory"), dict) else {}
        repeated_call_runs += 1 if int(trajectory.get("max_identical_repeats") or 0) >= 3 else 0
        claims_complete = gate.get("can_claim_complete") is True
        if claims_complete and (status != "verified" or run_failed or run_review or run_missing):
            unsafe_claims += 1
        integrity = gate.get("integrity") if isinstance(gate.get("integrity"), dict) else {}
        if (integrity.get("model_self_report_is_evidence") is not False
                or integrity.get("model_judge_can_accept_for_user") is not False
                or integrity.get("auto_executes") is not False
                or integrity.get("widens_scope") is not False):
            integrity_violations += 1
    return {
        "run_manifests": manifest_count,
        "gate_runs": gate_runs,
        "missing_gate_runs": max(0, manifest_count - gate_runs),
        "trace_coverage": round(gate_runs / manifest_count, 3) if manifest_count else None,
        "verified_runs": statuses["verified"],
        "delivered_with_limits_runs": statuses["delivered_with_limits"],
        "incomplete_runs": statuses["incomplete"],
        "blocked_runs": statuses["blocked"],
        "unavailable_runs": statuses["unavailable"],
        "verified_rate": round(statuses["verified"] / gate_runs, 3) if gate_runs else None,
        "required_criteria": required,
        "passed_criteria": passed,
        "failed_criteria": failed,
        "review_criteria": review,
        "missing_checks": missing,
        "repeated_tool_call_runs": repeated_call_runs,
        "failure_modes": dict(sorted(failure_modes.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "unsafe_completion_claims": unsafe_claims,
        "integrity_violations": integrity_violations,
        "scope": "完成门只衡量可观察契约与轨迹是否闭环；已验证不等于现实世界结论必然正确。",
        "truncated": bool(truncated),
    }


def dashboard(days: int = 7) -> dict:
    """Aggregate online quality over the last N days for the live dashboard."""
    _ensure_table()
    since = time.time() - days * 86400
    from hashmm.api import database as db
    with db._conn() as c:
        agg = c.execute(
            "SELECT COUNT(*) n, "
            # V308：AVG(grounded_ratio) 只在【可评估】样本（grounded_ratio 非 NULL）上求均值——
            # SQL 的 AVG 天然跳过 NULL，故 not_evaluable 样本自动被排除出通过率分母。
            "COALESCE(AVG(grounded_ratio),0) gr, "
            "COALESCE(SUM(CASE WHEN grounded_ratio IS NOT NULL THEN 1 ELSE 0 END),0) evaluable, "
            "COALESCE(SUM(CASE WHEN grounded_ratio IS NULL THEN 1 ELSE 0 END),0) not_evaluable, "
            "COALESCE(AVG(num_sources),0) src, COALESCE(AVG(num_citations),0) cit, "
            "COALESCE(AVG(elapsed_ms),0) lat, "
            "COALESCE(SUM(CASE WHEN num_sources=0 THEN 1 ELSE 0 END),0) no_src, "
            "COALESCE(SUM(CASE WHEN grounded_ratio<0.5 THEN 1 ELSE 0 END),0) weak "
            "FROM quality_samples WHERE ts >= ?", (since,)).fetchone()
        # daily trend（同样只对可评估样本求均值）
        daily = c.execute(
            "SELECT CAST((ts/86400) AS INTEGER) day, COUNT(*) n, "
            "COALESCE(AVG(grounded_ratio),0) gr FROM quality_samples "
            "WHERE ts >= ? GROUP BY day ORDER BY day", (since,)).fetchall()
        manifest_rows = c.execute(
            "SELECT run_manifest FROM messages WHERE role='assistant' AND created_at>=? "
            "AND run_manifest NOT IN ('', '{}', 'null') ORDER BY created_at DESC LIMIT 10001",
            (since,),
        ).fetchall()
        wrong_tool_feedback = 0
        try:
            wrong_tool_feedback = int(c.execute(
                "SELECT COUNT(*) n FROM message_feedback_cases WHERE created_at>=? "
                "AND rating='down' AND reason_code='wrong_tool' "
                "AND status IN ('pending','reviewing','approved')",
                (since,),
            ).fetchone()["n"] or 0)
        except Exception:
            # Older databases are migrated at startup; keep diagnostics usable
            # during a rolling upgrade before the feedback table is visible.
            wrong_tool_feedback = 0

    a = dict(agg)
    n = a["n"] or 0
    evaluable = a["evaluable"] or 0
    manifests = [row["run_manifest"] for row in manifest_rows[:10000]]
    return {
        "days": days,
        "samples": n,
        # 通过率类指标只基于可评估样本，并显式报告可评估/不可评估拆分
        "evaluable_samples": evaluable,
        "not_evaluable_samples": a["not_evaluable"],
        "avg_grounded_ratio": round(a["gr"], 3) if evaluable else None,
        # 证据覆盖率 = 有检索来源的样本占比（单独报告，不与接地率混算）
        "evidence_coverage": round((n - a["no_src"]) / n, 3) if n else None,
        "avg_sources": round(a["src"], 2),
        "avg_citations": round(a["cit"], 2),
        "avg_latency_ms": round(a["lat"]),
        "answers_without_sources": a["no_src"],
        "weakly_grounded": a["weak"],
        # weak_rate 也只对可评估样本计（分母用 evaluable，不是全部 n）
        "weak_rate": round(a["weak"] / evaluable, 3) if evaluable else 0.0,
        "daily": [{"day": int(d["day"]), "samples": d["n"],
                   "grounded_ratio": round(d["gr"], 3)} for d in daily],
        "sample_rate": _SAMPLE_RATE,
        "tool_quality": runtime_tool_metrics(
            manifests,
            wrong_tool_feedback=wrong_tool_feedback,
            truncated=len(manifest_rows) > 10000,
        ),
        "task_graph_quality": runtime_graph_metrics(
            manifests,
            truncated=len(manifest_rows) > 10000,
        ),
        "execution_frontier_quality": runtime_frontier_metrics(
            manifests,
            truncated=len(manifest_rows) > 10000,
        ),
        "completion_gate_quality": runtime_completion_gate_metrics(
            manifests,
            truncated=len(manifest_rows) > 10000,
        ),
    }
