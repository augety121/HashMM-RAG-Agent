"""v17 Phase 27 (B2 + B3) — observability (OTel GenAI conventions) + latency/cost SLO.

Industry has standardised LLM/RAG observability on the OpenTelemetry **GenAI
semantic conventions** (CNCF-backed `gen_ai.*` attributes; supported by Google /
AWS / Azure / Datadog). This module emits those conventions for the RAG pipeline
stages (retrieve → rerank → KG → generate): model, token usage, cost, finish
reason, and the RAG data source id (`gen_ai.data_source.id`).

Design for this environment:
- **Real OpenTelemetry is optional.** If `opentelemetry` is installed we emit real
  spans; if not, we fall back to an in-memory ring buffer. Either way nothing
  throws — observability must never break the request path.
- The same per-request record feeds the **SLO** layer (B3): p50/p95 latency per
  stage + per-query cost, compared against budgets, with breach flags.

Pure + dependency-light; fully testable offline.
"""
from __future__ import annotations
from hashmm.utils import get_logger as _get_logger, log_suppressed
_obs_logger = _get_logger(__name__)

import time
from collections import deque
from contextlib import contextmanager

# ── OTel GenAI semantic-convention attribute names (gen_ai.*) ──
GEN_AI_OPERATION = "gen_ai.operation.name"
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_FINISH = "gen_ai.response.finish_reasons"
GEN_AI_DATA_SOURCE_ID = "gen_ai.data_source.id"   # RAG grounding source
# hashmm extensions (namespaced, not part of the spec)
HASHMM_STAGE_LATENCY = "hashmm.stage.latency_ms"
HASHMM_RETRIEVAL_MODE = "hashmm.retrieval.mode"
HASHMM_N_SOURCES = "hashmm.retrieval.n_sources"
HASHMM_COST_USD = "hashmm.cost.usd"
HASHMM_RETRIEVAL_SCORE = "hashmm.retrieval.top_score"   # rerank top score (quality)
HASHMM_RETRIEVAL_STRATEGY = "hashmm.retrieval.strategy"  # grounded / insufficient / direct

# Per-1K-token USD prices (override as needed). Unknown models fall back to default.
_PRICES = {
    "deepseek-v4-pro": {"in": 0.00027, "out": 0.0011},
    "default": {"in": 0.0005, "out": 0.0015},
}

# Default SLO budgets (tune to your traffic). p95 latency in ms, cost in USD.
DEFAULT_SLO = {
    "total_latency_p95_ms": 8000,
    "retrieve_latency_p95_ms": 1500,
    "cost_per_query_p95_usd": 0.05,
}

_RING_MAX = 1000
_REQUESTS: deque = deque(maxlen=_RING_MAX)

# Tool-call telemetry: per-call name/latency/ok, summarised by tool_stats().
_TOOL_CALLS: deque = deque(maxlen=_RING_MAX)

# LLM routing telemetry: count of tasks served local vs cloud (cost/privacy).
_LLM_ROUTING: dict = {"local": 0, "cloud": 0, "by_task": {}}

# Scheduled-task telemetry: per-action run counts + last status.
_SCHEDULED: dict = {}

# S3-2 错误码计数：每个 error code 的发生次数 + 最近 trace_id（便于"哪类错误最多/最近一次链路"）。
_ERRORS: dict = {}

# v17 Phase 69: per-tenant cost attribution (ToB cost accounting). Keyed by
# tenant_id → {requests, input_tokens, output_tokens, cost_usd}.
_COST_BY_TENANT: dict = {}


def cost_by_tenant() -> dict:
    """Per-tenant request/token/cost totals (for ToB cost attribution)."""
    return {t: dict(v) for t, v in _COST_BY_TENANT.items()}


def record_scheduled_run(action: str, status: str) -> None:
    """Count a scheduled-task run. Never raises."""
    try:
        a = _SCHEDULED.setdefault(action, {"ok": 0, "error": 0})
        a[status] = a.get(status, 0) + 1
    except Exception:
        pass  # nosem: observability-fallback


def scheduled_stats() -> dict:
    return {"actions": dict(_SCHEDULED),
            "total_runs": sum(v.get("ok", 0) + v.get("error", 0) for v in _SCHEDULED.values())}


def record_llm_routing(task: str, backend: str) -> None:
    """Count a local/cloud routing decision. Never raises."""
    try:
        if backend in ("local", "cloud"):
            _LLM_ROUTING[backend] = _LLM_ROUTING.get(backend, 0) + 1
            bt = _LLM_ROUTING["by_task"].setdefault(task, {"local": 0, "cloud": 0})
            bt[backend] = bt.get(backend, 0) + 1
    except Exception:
        pass  # nosem: observability-fallback


def llm_routing_stats() -> dict:
    """Local vs cloud split + estimated local-served fraction (proxy for savings)."""
    local = _LLM_ROUTING.get("local", 0)
    cloud = _LLM_ROUTING.get("cloud", 0)
    total = local + cloud
    return {
        "local": local,
        "cloud": cloud,
        "local_fraction": round(local / total, 4) if total else 0.0,
        "by_task": dict(_LLM_ROUTING.get("by_task", {})),
    }


def record_tool_call(name: str, latency_ms: int, ok: bool) -> None:
    """Record one tool invocation (name, latency, success). Never raises."""
    try:
        _TOOL_CALLS.append({"name": name, "latency_ms": int(latency_ms),
                            "ok": bool(ok), "_ts": time.time()})
    except Exception:  # nosem: observability-fallback
        pass


def record_error(code: str, *, trace_id: str = "") -> None:
    """S3-2：记录一次错误码发生（计数 + 最近 trace_id）。永不抛错。

    与 error_codes.to_error_response 配合：每次对外返回错误体时计一次，
    便于运维面板看"哪类错误最多、最近一次的 trace_id（可串联日志）"。
    """
    try:
        e = _ERRORS.setdefault(code, {"count": 0, "last_trace_id": ""})
        e["count"] += 1
        if trace_id:
            e["last_trace_id"] = trace_id
    except Exception:  # nosem: observability-fallback
        pass


def error_stats() -> dict:
    """错误码计数概览：{code: {count, last_trace_id}} + 总错误数 + 最高频 code。"""
    try:
        codes = dict(_ERRORS)
        total = sum(v.get("count", 0) for v in codes.values())
        top = max(codes.items(), key=lambda kv: kv[1].get("count", 0))[0] if codes else None
        return {"by_code": codes, "total": total, "top_code": top}
    except Exception:
        return {"by_code": {}, "total": 0, "top_code": None}


def tool_stats() -> dict:
    """Aggregate tool-call telemetry: per-tool count / error-rate / p95 latency."""
    calls = list(_TOOL_CALLS)
    by: dict = {}
    for c in calls:
        b = by.setdefault(c["name"], {"count": 0, "errors": 0, "_lat": []})
        b["count"] += 1
        if not c["ok"]:
            b["errors"] += 1
        b["_lat"].append(c["latency_ms"])
    out = {}
    for name, b in by.items():
        lat = b.pop("_lat")
        out[name] = {
            "count": b["count"],
            "error_rate": round(b["errors"] / b["count"], 4) if b["count"] else 0.0,
            "latency_p95_ms": _pct(lat, 95),
        }
    return {"tools": out, "total_calls": len(calls)}


def estimate_cost(model: str | None, in_tokens: int, out_tokens: int) -> float:
    p = _PRICES.get((model or "").lower(), _PRICES["default"])
    return round((in_tokens / 1000.0) * p["in"] + (out_tokens / 1000.0) * p["out"], 6)


def _otel_tracer():
    """Return a real OTel tracer if available, else None (in-memory only)."""
    try:
        from opentelemetry import trace  # type: ignore
        return trace.get_tracer("hashmm.rag")
    except Exception:
        return None


@contextmanager
def trace_stage(operation: str, model: str | None = None, **attrs):
    """Time a pipeline stage and emit an OTel GenAI span (if OTel is present).

    `operation` is a gen_ai.operation.name value, e.g. 'retrieve' / 'rerank' /
    'kg_retrieve' / 'generate'. Always safe: never raises out of the block.
    """
    t0 = time.time()
    span_attrs = {GEN_AI_OPERATION: operation, GEN_AI_SYSTEM: "hashmm"}
    if model:
        span_attrs[GEN_AI_REQUEST_MODEL] = model
    span_attrs.update(attrs)
    tracer = _otel_tracer()
    cm = tracer.start_as_current_span(f"gen_ai.{operation}") if tracer else None
    span = cm.__enter__() if cm else None
    try:
        yield span_attrs
    finally:
        dur = round((time.time() - t0) * 1000)
        span_attrs[HASHMM_STAGE_LATENCY] = dur
        try:
            if span is not None:
                for k, v in span_attrs.items():
                    span.set_attribute(k, v)
        except Exception as _e:
            log_suppressed(_obs_logger, _e)
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception as _e:
                log_suppressed(_obs_logger, _e)


def record_rag_request(*, model: str | None, input_tokens: int, output_tokens: int,
                       total_latency_ms: int, n_sources: int = 0,
                       retrieval_mode: str = "", finish_reason: str = "stop",
                       data_source_ids: list | None = None,
                       stage_latency_ms: dict | None = None,
                       top_score: float | None = None,
                       retrieval_strategy: str = "",
                       tenant_id: str = "") -> dict:
    """Record one completed RAG request as an OTel GenAI-attributed span + ring entry.

    Returns the span-attribute dict. Never raises (observability must not break the
    request). The same record feeds slo_report(). When tenant_id is given, the
    request/token/cost are also attributed to that tenant (v17 Phase 69).
    """
    try:
        cost = estimate_cost(model, input_tokens, output_tokens)
        # Per-tenant cost attribution (best-effort, never raises).
        if tenant_id:
            try:
                t = _COST_BY_TENANT.setdefault(
                    tenant_id, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
                t["requests"] += 1
                t["input_tokens"] += int(input_tokens)
                t["output_tokens"] += int(output_tokens)
                t["cost_usd"] = round(t["cost_usd"] + cost, 6)
            except Exception:
                pass  # nosem: observability-fallback
        rec = {
            GEN_AI_OPERATION: "chat",
            GEN_AI_SYSTEM: "hashmm",
            GEN_AI_REQUEST_MODEL: model or "unknown",
            GEN_AI_USAGE_INPUT_TOKENS: int(input_tokens),
            GEN_AI_USAGE_OUTPUT_TOKENS: int(output_tokens),
            GEN_AI_RESPONSE_FINISH: [finish_reason],
            GEN_AI_DATA_SOURCE_ID: (data_source_ids or [])[:10],
            HASHMM_RETRIEVAL_MODE: retrieval_mode,
            HASHMM_N_SOURCES: int(n_sources),
            HASHMM_COST_USD: cost,
            HASHMM_STAGE_LATENCY: int(total_latency_ms),
            HASHMM_RETRIEVAL_STRATEGY: retrieval_strategy or "",
            "_stages": stage_latency_ms or {},
            "_ts": time.time(),
        }
        if top_score is not None:
            rec[HASHMM_RETRIEVAL_SCORE] = float(top_score)
        _REQUESTS.append(rec)
        tracer = _otel_tracer()
        if tracer:
            with tracer.start_as_current_span("gen_ai.chat") as sp:
                for k, v in rec.items():
                    if not k.startswith("_"):
                        try:
                            sp.set_attribute(k, v)
                        except Exception as _e:
                            log_suppressed(_obs_logger, _e)
        return rec
    except Exception:
        return {}


def _pct(values: list, p: int):
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


def slo_report(targets: dict | None = None) -> dict:
    """p50/p95 latency + cost over recorded requests, with SLO breach flags."""
    targets = targets or DEFAULT_SLO
    reqs = list(_REQUESTS)
    n = len(reqs)
    total_lat = [r.get(HASHMM_STAGE_LATENCY, 0) for r in reqs]
    costs = [r.get(HASHMM_COST_USD, 0.0) for r in reqs]
    retr_lat = [r.get("_stages", {}).get("retrieve", 0) for r in reqs
                if r.get("_stages", {}).get("retrieve") is not None]
    # Retrieval quality: average top rerank score, and the rate of requests that
    # fell back to "insufficient" (retrieval didn't find enough to answer).
    scores = [r[HASHMM_RETRIEVAL_SCORE] for r in reqs if HASHMM_RETRIEVAL_SCORE in r]
    insufficient = [r for r in reqs
                    if r.get(HASHMM_RETRIEVAL_STRATEGY) == "insufficient"]
    measured = {
        "n": n,
        "total_latency_p50_ms": _pct(total_lat, 50),
        "total_latency_p95_ms": _pct(total_lat, 95),
        "retrieve_latency_p95_ms": _pct(retr_lat, 95) if retr_lat else None,
        "cost_per_query_p50_usd": round(_pct(costs, 50), 6),
        "cost_per_query_p95_usd": round(_pct(costs, 95), 6),
        "cost_total_usd": round(sum(costs), 6),
        "retrieval_top_score_avg": round(sum(scores) / len(scores), 4) if scores else None,
        "insufficient_rate": round(len(insufficient) / n, 4) if n else 0.0,
    }
    breaches = []
    for key, target in targets.items():
        val = measured.get(key)
        if val is not None and val > target:
            breaches.append({"slo": key, "target": target, "actual": val})
    return {**measured, "targets": targets, "breaches": breaches,
            "ok": len(breaches) == 0}


def reset():
    """Clear the in-memory ring (tests / fresh windows)."""
    _REQUESTS.clear()
    _TOOL_CALLS.clear()
    _LLM_ROUTING.clear()
    _LLM_ROUTING.update({"local": 0, "cloud": 0, "by_task": {}})
    _SCHEDULED.clear()
    _COST_BY_TENANT.clear()
    _ERRORS.clear()


def export_otlp_traces(limit: int = 200) -> dict:
    """把内存里的 RAG 请求记录导出成 OTLP/JSON 兼容的 trace（resourceSpans 结构）。

    标准化 trace 导出（BENCHMARK_AND_ROADMAP_2026 §三-5）：你已有 trace_stage / 
    record_rag_request 在 OTel 在场时发 GenAI span；本函数补上**不依赖 OTel 的标准导出** ——
    即使没装 OTel，任何标准后端（Jaeger/Tempo/Grafana 的 OTLP/JSON 接收端）也能拿到 trace。
    纯 stdlib，零新依赖。永不抛错（失败返回空 resourceSpans）。

    返回结构遵循 OTLP/JSON：{resourceSpans:[{resource, scopeSpans:[{scope, spans:[...]}]}]}。
    每个 RAG 请求是一个 span，每个内部阶段（retrieve/rerank/generate…）是其子 span。
    """
    try:
        reqs = list(_REQUESTS)[-int(limit):]
    except Exception:
        reqs = []

    def _attr(k, v):
        # OTLP attribute: {key, value:{stringValue|intValue|doubleValue|boolValue}}
        if isinstance(v, bool):
            val = {"boolValue": v}
        elif isinstance(v, int):
            val = {"intValue": str(v)}
        elif isinstance(v, float):
            val = {"doubleValue": v}
        elif isinstance(v, (list, tuple)):
            val = {"stringValue": ",".join(str(x) for x in v)}
        else:
            val = {"stringValue": str(v)}
        return {"key": str(k), "value": val}

    spans = []
    for i, rec in enumerate(reqs):
        try:
            ts = rec.get("_ts", time.time())
            end_ns = int(ts * 1e9)
            dur_ms = int(rec.get(HASHMM_STAGE_LATENCY, 0) or 0)
            start_ns = end_ns - dur_ms * 1_000_000
            trace_id = f"{i:032x}"
            span_id = f"{i:016x}"
            attrs = [_attr(k, v) for k, v in rec.items() if not k.startswith("_")]
            span = {
                "traceId": trace_id, "spanId": span_id,
                "name": f"gen_ai.{rec.get(GEN_AI_OPERATION, 'chat')}",
                "kind": 1,  # SPAN_KIND_INTERNAL
                "startTimeUnixNano": str(start_ns),
                "endTimeUnixNano": str(end_ns),
                "attributes": attrs,
            }
            spans.append(span)
            # 阶段子 span
            stages = rec.get("_stages") or {}
            if isinstance(stages, dict):
                child_end = end_ns
                for j, (stage, lat) in enumerate(stages.items()):
                    try:
                        lat_ms = int(lat)
                    except Exception:
                        continue
                    spans.append({
                        "traceId": trace_id, "spanId": f"{i:012x}{j:04x}",
                        "parentSpanId": span_id,
                        "name": f"gen_ai.{stage}", "kind": 1,
                        "startTimeUnixNano": str(child_end - lat_ms * 1_000_000),
                        "endTimeUnixNano": str(child_end),
                        "attributes": [_attr("gen_ai.operation.name", stage),
                                       _attr("hashmm.stage.latency_ms", lat_ms)],
                    })
        except Exception:
            continue

    return {
        "resourceSpans": [{
            "resource": {"attributes": [_attr("service.name", "hashmm"),
                                        _attr("service.version", "17.0")]},
            "scopeSpans": [{
                "scope": {"name": "hashmm.rag"},
                "spans": spans,
            }],
        }],
    }


def dashboard_snapshot(targets: dict | None = None) -> dict:
    """运维 dashboard 一次性快照 —— 把散落的可观测数据聚成一个面板友好的结构。

    给前端运维面板（Grafana 风）一次拿全：延迟 P50/P95、token/成本、检索质量、
    SLO 违约、云/本地路由占比（F11 成本节省）、工具调用、定时任务。

    这是**收口**而非重造：底层数据全部来自已有的 slo_report / llm_routing_stats /
    tool_stats / scheduled_stats / cost_by_tenant。永不抛错：任一子项失败用空值占位，
    不影响其它面板。前端可据此画延迟/成本/命中率/错误率面板。
    """
    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    slo = _safe(lambda: slo_report(targets), {})
    routing = _safe(llm_routing_stats, {"local": 0, "cloud": 0, "by_task": {}})
    tools = _safe(tool_stats, {})
    scheduled = _safe(scheduled_stats, {})
    tenant_cost = _safe(cost_by_tenant, {})

    # F11 路由节省视角：本地调用占比（越高 = 省得越多）。
    rl = routing.get("local", 0) or 0
    rc = routing.get("cloud", 0) or 0
    total_routed = rl + rc
    local_ratio = round(rl / total_routed, 4) if total_routed else 0.0

    return {
        "latency": {
            "p50_ms": slo.get("total_latency_p50_ms"),
            "p95_ms": slo.get("total_latency_p95_ms"),
            "retrieve_p95_ms": slo.get("retrieve_latency_p95_ms"),
            "n_requests": slo.get("n", 0),
        },
        "cost": {
            "per_query_p50_usd": slo.get("cost_per_query_p50_usd"),
            "per_query_p95_usd": slo.get("cost_per_query_p95_usd"),
            "total_usd": slo.get("cost_total_usd"),
            "by_tenant": tenant_cost,
        },
        "retrieval_quality": {
            "top_score_avg": slo.get("retrieval_top_score_avg"),
            "insufficient_rate": slo.get("insufficient_rate"),
        },
        "slo": {
            "ok": slo.get("ok", True),
            "breaches": slo.get("breaches", []),
            "targets": slo.get("targets", {}),
        },
        "llm_routing": {
            "local": rl, "cloud": rc,
            "local_ratio": local_ratio,
            "by_task": routing.get("by_task", {}),
        },
        "tools": tools,
        "scheduled": scheduled,
        "errors": _safe(error_stats, {"by_code": {}, "total": 0, "top_code": None}),
    }
