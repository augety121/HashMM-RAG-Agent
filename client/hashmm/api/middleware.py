"""Middleware v15 — structured logging, request tracing, rate limiting.

Replaces print() with structured log entries.
Adds trace_id to every request for debugging.
Rate limits per IP.
"""
from __future__ import annotations
from hashmm.utils import log_suppressed
import time, uuid, logging
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse


# ═══════════════════════════════════════════════════════════════════
# Structured Logger
# ═══════════════════════════════════════════════════════════════════

logger = logging.getLogger("hashmm")

# 高频低价值请求路径（远程中继推/拉帧、心跳、远程状态轮询）——日志里滤掉，避免刷屏。
_NOISY_PATH_SUBSTRINGS = ("/api/remote/relay/", "/api/health", "/api/remote/status")

def _is_noisy_path(path: str) -> bool:
    return any(s in path for s in _NOISY_PATH_SUBSTRINGS)

class _NoisyAccessFilter(logging.Filter):
    """丢弃 uvicorn access 日志里的高频中继/心跳请求行。"""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return not _is_noisy_path(record.getMessage())
        except Exception:
            return True

def setup_logging(level: str = "INFO"):
    """Configure structured logging."""
    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S"
    )
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)

    root = logging.getLogger("hashmm")
    root.setLevel(getattr(logging, level))
    # Clear existing handlers to avoid duplicates
    root.handlers.clear()
    root.addHandler(handler)

    # Suppress noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # 远程中继推/拉帧、心跳是每秒数次的高频请求，从 uvicorn access 日志里滤掉，避免刷屏淹没真正有用的日志
    _ua = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _NoisyAccessFilter) for f in _ua.filters):
        _ua.addFilter(_NoisyAccessFilter())


def log_request(trace_id: str, conv_id: str, query: str, task_type: str):
    from hashmm.api.privacy import redact_for_log
    logger.info(f"[{trace_id}] conv={conv_id[:8]} task={task_type} query={redact_for_log(query, 60)}")

def log_tool(trace_id: str, tool: str, duration_ms: int, status: str):
    logger.info(f"[{trace_id}] tool={tool} dur={duration_ms}ms status={status}")

def log_llm(trace_id: str, tokens_in: int, tokens_out: int, first_token_ms: int = 0):
    logger.info(f"[{trace_id}] llm tokens_in={tokens_in} out={tokens_out} first_token={first_token_ms}ms")

def log_error(trace_id: str, error: str, component: str = ""):
    from hashmm.api.privacy import redact_for_log
    logger.error(f"[{trace_id}] {component}: {redact_for_log(error, 200)}")


# ═══════════════════════════════════════════════════════════════════
# Request Tracing Middleware
# ═══════════════════════════════════════════════════════════════════

class TraceMiddleware(BaseHTTPMiddleware):
    """v15 Phase 6: request-level observability.

    - Assigns a request_id to every request (honors inbound X-Request-ID).
    - Logs API requests at INFO with method/path/status/duration.
    - Warns on slow requests (> SLOW_MS).
    - Feeds latency/error counters into the global metrics registry.
    - Returns X-Request-ID so clients/users can quote it when reporting issues.
    """

    SLOW_MS = 5000

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        request.state.trace_id = request_id  # backward-compat alias

        # S1-2: 把 request_id 接入 trace_context（contextvars），让业务代码任意处
        # current_trace_id() 都能拿到同一个 id，实现跨模块链路追踪。
        try:
            from hashmm.trace_context import set_trace_id
            set_trace_id(request_id)
        except Exception:
            pass

        path = request.url.path
        is_static = path.startswith("/_next/") or path.endswith((".js", ".css", ".ico", ".png", ".svg", ".woff", ".woff2"))
        is_api = path.startswith("/api/")

        t0 = time.time()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            # Let the global handler format it; still record metrics + re-raise
            _record_metric(path, 500, round((time.time() - t0) * 1000))
            raise

        dur = round((time.time() - t0) * 1000)

        if is_api and not is_static:
            _record_metric(path, status, dur)
            level_slow = dur >= self.SLOW_MS
            msg = f"[{request_id}] {request.method} {path} → {status} ({dur}ms)"
            if level_slow:
                logger.warning("SLOW " + msg)
            elif status >= 500:
                logger.error(msg)
            elif not _is_noisy_path(path):   # 中继推/拉帧、心跳等高频请求不刷屏（仅慢/错时记）
                logger.info(msg)

        response.headers["X-Request-ID"] = request_id
        return response


# ═══════════════════════════════════════════════════════════════════
# Lightweight in-process metrics (Prometheus-friendly)
# ═══════════════════════════════════════════════════════════════════

_METRICS = {
    "requests_total": 0,
    "errors_total": 0,
    "latency_ms": [],          # rolling window of recent latencies
    "by_status": defaultdict(int),
}
_LATENCY_WINDOW = 1000


def _record_metric(path: str, status: int, dur_ms: int):
    _METRICS["requests_total"] += 1
    _METRICS["by_status"][str(status)] += 1
    if status >= 500:
        _METRICS["errors_total"] += 1
    lat = _METRICS["latency_ms"]
    lat.append(dur_ms)
    if len(lat) > _LATENCY_WINDOW:
        del lat[: len(lat) - _LATENCY_WINDOW]


def get_metrics_snapshot() -> dict:
    lat = sorted(_METRICS["latency_ms"])
    n = len(lat)

    def pct(p):
        if not lat:
            return 0
        idx = min(n - 1, int(p / 100 * n))
        return lat[idx]

    total = _METRICS["requests_total"]
    errors = _METRICS["errors_total"]
    # v17 Phase 27 (B2/B3): attach GenAI/RAG SLO report (no-op safe)
    try:
        from hashmm import observability as _obs
        genai = _obs.slo_report()
    except Exception:
        genai = {}
    return {
        "requests_total": total,
        "errors_total": errors,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "latency_p50_ms": pct(50),
        "latency_p95_ms": pct(95),
        "latency_p99_ms": pct(99),
        "by_status": dict(_METRICS["by_status"]),
        "window": n,
        "genai": genai,
    }


def render_prometheus() -> str:
    """Render metrics in Prometheus text exposition format."""
    s = get_metrics_snapshot()
    lines = [
        "# HELP hashmm_requests_total Total API requests",
        "# TYPE hashmm_requests_total counter",
        f"hashmm_requests_total {s['requests_total']}",
        "# HELP hashmm_errors_total Total 5xx errors",
        "# TYPE hashmm_errors_total counter",
        f"hashmm_errors_total {s['errors_total']}",
        "# HELP hashmm_request_latency_ms Request latency percentiles",
        "# TYPE hashmm_request_latency_ms gauge",
        f'hashmm_request_latency_ms{{quantile="0.5"}} {s["latency_p50_ms"]}',
        f'hashmm_request_latency_ms{{quantile="0.95"}} {s["latency_p95_ms"]}',
        f'hashmm_request_latency_ms{{quantile="0.99"}} {s["latency_p99_ms"]}',
    ]
    for code, cnt in s["by_status"].items():
        lines.append(f'hashmm_requests_by_status{{status="{code}"}} {cnt}')
    # v17 Phase 27: GenAI/RAG latency + cost (OTel GenAI-aligned)
    g = s.get("genai") or {}
    if g.get("n"):
        lines += [
            "# HELP hashmm_genai_latency_ms RAG request latency percentiles",
            "# TYPE hashmm_genai_latency_ms gauge",
            f'hashmm_genai_latency_ms{{quantile="0.5"}} {g.get("total_latency_p50_ms", 0)}',
            f'hashmm_genai_latency_ms{{quantile="0.95"}} {g.get("total_latency_p95_ms", 0)}',
            "# HELP hashmm_genai_cost_usd Per-query cost (USD) percentiles",
            "# TYPE hashmm_genai_cost_usd gauge",
            f'hashmm_genai_cost_usd{{quantile="0.95"}} {g.get("cost_per_query_p95_usd", 0)}',
            f"hashmm_genai_slo_breaches {len(g.get('breaches', []))}",
        ]
        # Retrieval quality: insufficient-fallback rate + avg top rerank score.
        if g.get("retrieval_top_score_avg") is not None:
            lines += [
                "# HELP hashmm_retrieval_top_score_avg Average top rerank score",
                "# TYPE hashmm_retrieval_top_score_avg gauge",
                f'hashmm_retrieval_top_score_avg {g.get("retrieval_top_score_avg", 0)}',
            ]
        lines += [
            "# HELP hashmm_retrieval_insufficient_rate Fraction of queries with insufficient retrieval",
            "# TYPE hashmm_retrieval_insufficient_rate gauge",
            f'hashmm_retrieval_insufficient_rate {g.get("insufficient_rate", 0)}',
        ]

    # v17 Phase 69: expose tool-call, LLM-routing, and per-tenant cost metrics
    # (already collected in observability, previously not scraped).
    def _esc(v: str) -> str:
        return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    try:
        from hashmm import observability as _obs
        ts = _obs.tool_stats()
        if ts.get("total_calls"):
            lines += ["# HELP hashmm_tool_calls_total Tool invocations by tool",
                      "# TYPE hashmm_tool_calls_total counter"]
            for name, st in ts.get("tools", {}).items():
                lab = _esc(name)
                lines.append(f'hashmm_tool_calls_total{{tool="{lab}"}} {st["count"]}')
                lines.append(f'hashmm_tool_error_rate{{tool="{lab}"}} {st["error_rate"]}')
                lines.append(f'hashmm_tool_latency_p95_ms{{tool="{lab}"}} {st["latency_p95_ms"]}')
        rt = _obs.llm_routing_stats()
        if (rt.get("local", 0) + rt.get("cloud", 0)) > 0:
            lines += [
                "# HELP hashmm_llm_routing_total LLM requests routed by backend",
                "# TYPE hashmm_llm_routing_total counter",
                f'hashmm_llm_routing_total{{backend="local"}} {rt.get("local", 0)}',
                f'hashmm_llm_routing_total{{backend="cloud"}} {rt.get("cloud", 0)}',
                "# HELP hashmm_llm_local_fraction Fraction of LLM requests served locally",
                "# TYPE hashmm_llm_local_fraction gauge",
                f'hashmm_llm_local_fraction {rt.get("local_fraction", 0)}',
            ]
        ct = _obs.cost_by_tenant()
        if ct:
            lines += ["# HELP hashmm_tenant_cost_usd Cumulative cost (USD) by tenant",
                      "# TYPE hashmm_tenant_cost_usd counter",
                      "# HELP hashmm_tenant_requests_total RAG requests by tenant",
                      "# TYPE hashmm_tenant_requests_total counter"]
            for tid, st in ct.items():
                lab = _esc(tid)
                lines.append(f'hashmm_tenant_cost_usd{{tenant="{lab}"}} {st.get("cost_usd", 0)}')
                lines.append(f'hashmm_tenant_requests_total{{tenant="{lab}"}} {st.get("requests", 0)}')
                lines.append(f'hashmm_tenant_tokens_total{{tenant="{lab}",direction="in"}} {st.get("input_tokens", 0)}')
                lines.append(f'hashmm_tenant_tokens_total{{tenant="{lab}",direction="out"}} {st.get("output_tokens", 0)}')
    except Exception:
        pass  # nosem: observability-fallback
    # v17 Phase 79: pipeline (reasoning) cache + agent-governance metrics —
    # wires Phases 71 / 77 into the Phase 69 Prometheus surface.
    try:
        from hashmm import pipeline_cache as _pc
        cs = _pc.stats()
        if (cs.get("hits", 0) + cs.get("misses", 0)) > 0 or cs.get("entries", 0) > 0:
            lines += [
                "# HELP hashmm_pipeline_cache_hit_rate Reasoning-cache hit rate",
                "# TYPE hashmm_pipeline_cache_hit_rate gauge",
                f'hashmm_pipeline_cache_hit_rate {cs.get("hit_rate", 0)}',
                "# HELP hashmm_pipeline_cache_entries Reasoning-cache entry count",
                "# TYPE hashmm_pipeline_cache_entries gauge",
                f'hashmm_pipeline_cache_entries {cs.get("entries", 0)}',
                "# HELP hashmm_pipeline_cache_hits_total Reasoning-cache hits",
                "# TYPE hashmm_pipeline_cache_hits_total counter",
                f'hashmm_pipeline_cache_hits_total {cs.get("hits", 0)}',
                f'hashmm_pipeline_cache_misses_total {cs.get("misses", 0)}',
            ]
    except Exception:
        pass  # nosem: observability-fallback
    try:
        from hashmm.agent import tool_governance as _tg
        gm = _tg.governance_metrics()
        lines += [
            "# HELP hashmm_tool_approval_denied_total High-risk tool calls denied pending approval",
            "# TYPE hashmm_tool_approval_denied_total counter",
            f'hashmm_tool_approval_denied_total {gm.get("approval_denied", 0)}',
            "# HELP hashmm_tool_approval_granted_total High-risk tool calls approved",
            "# TYPE hashmm_tool_approval_granted_total counter",
            f'hashmm_tool_approval_granted_total {gm.get("approval_granted", 0)}',
            "# HELP hashmm_tool_high_risk_total High-risk tool calls audited",
            "# TYPE hashmm_tool_high_risk_total counter",
            f'hashmm_tool_high_risk_total {gm.get("high_risk", 0)}',
        ]
    except Exception:
        pass  # nosem: observability-fallback
    # v17 Phase 83: background-job backlog + worker-pool capacity.
    try:
        from hashmm.api import jobs as _jobs
        sc = _jobs.status_counts()
        if sum(sc.values()) > 0:
            lines += ["# HELP hashmm_jobs_total Tracked background jobs by status",
                      "# TYPE hashmm_jobs_total gauge"]
            for st, n in sc.items():
                lines.append(f'hashmm_jobs_total{{status="{st}"}} {n}')
        from hashmm.api import job_queue as _jq
        qs = _jq.current_stats()
        if qs:
            lines += [
                "# HELP hashmm_job_queue_active Currently running jobs",
                "# TYPE hashmm_job_queue_active gauge",
                f'hashmm_job_queue_active {qs.get("active", 0)}',
                "# HELP hashmm_job_queue_outstanding Submitted jobs not yet finished",
                "# TYPE hashmm_job_queue_outstanding gauge",
                f'hashmm_job_queue_outstanding {qs.get("outstanding", 0)}',
                "# HELP hashmm_job_queue_concurrency Worker-pool concurrency limit",
                "# TYPE hashmm_job_queue_concurrency gauge",
                f'hashmm_job_queue_concurrency {qs.get("concurrency", 0)}',
                "# HELP hashmm_job_queue_peak_active Peak concurrent jobs observed",
                "# TYPE hashmm_job_queue_peak_active gauge",
                f'hashmm_job_queue_peak_active {qs.get("peak_active", 0)}',
                "# HELP hashmm_job_queue_done_total Completed jobs",
                "# TYPE hashmm_job_queue_done_total counter",
                f'hashmm_job_queue_done_total {qs.get("done", 0)}',
                "# HELP hashmm_job_queue_errors_total Failed jobs (after retries)",
                "# TYPE hashmm_job_queue_errors_total counter",
                f'hashmm_job_queue_errors_total {qs.get("errors", 0)}',
                "# HELP hashmm_job_queue_retries_total Job retry attempts",
                "# TYPE hashmm_job_queue_retries_total counter",
                f'hashmm_job_queue_retries_total {qs.get("retries", 0)}',
            ]
    except Exception:
        pass  # nosem: observability-fallback
    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════
# Rate Limiting Middleware
# ═══════════════════════════════════════════════════════════════════

class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject over-large request bodies BEFORE they are read/parsed.

    Without this, a single large POST (e.g. a 500MB JSON body) is buffered and
    parsed into memory before any handler runs — a trivial DoS. We check the
    declared Content-Length up front and return 413 immediately when it exceeds
    the limit. File-upload endpoints that legitimately receive large multipart
    bodies are exempted (they enforce their own per-file size caps downstream).

    Limit is operator-tunable via HASHMM_MAX_BODY_MB (default 25MB), which is
    ample for chat/JSON APIs while bounding worst-case memory per request.
    """

    def __init__(self, app, max_mb: int | None = None):
        super().__init__(app)
        import os
        try:
            self.max_bytes = int(float(os.environ.get("HASHMM_MAX_BODY_MB", max_mb or 25))) * 1024 * 1024
        except (TypeError, ValueError):
            self.max_bytes = 25 * 1024 * 1024
        # Endpoints that legitimately accept large bodies (multipart uploads /
        # zip imports); they cap size themselves further downstream.
        self._exempt_prefixes = ("/api/admin/upload", "/api/admin/docs",
                                  "/api/conversations/")  # chat file uploads

    def _is_exempt(self, path: str) -> bool:
        # Only exempt actual upload sub-paths, not every conversations route.
        if path.startswith("/api/admin/upload") or path.startswith("/api/admin/docs"):
            return True
        if path.startswith("/api/conversations/") and path.endswith("/files"):
            return True
        return False

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH") and not self._is_exempt(request.url.path):
            cl = request.headers.get("content-length")
            if cl:
                try:
                    if int(cl) > self.max_bytes:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": f"请求体过大（最大 {self.max_bytes // 1024 // 1024}MB）"},
                        )
                except (TypeError, ValueError) as _e:
                    log_suppressed(logger, _e)
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter per IP."""

    def __init__(self, app, max_per_minute: int = 30, max_per_hour: int = 300):
        super().__init__(app)
        self.max_per_minute = max_per_minute
        self.max_per_hour = max_per_hour
        self.minute_counts: dict[str, list[float]] = defaultdict(list)
        self._redis = None
        self._redis_tried = False

    def _get_redis(self):
        # Reuse the cache layer's Redis connection if it's up.
        if self._redis_tried:
            return self._redis
        self._redis_tried = True
        try:
            from hashmm.agent.cache import get_cache
            self._redis = getattr(get_cache(), "_redis", None)
        except Exception:
            self._redis = None
        return self._redis

    def _identity(self, request: Request) -> str:
        """Prefer the authenticated user; fall back to client IP."""
        try:
            from hashmm.api.auth import get_current_user
            u = get_current_user(request)
            if u and u.get("uid"):
                return f"u:{u['uid']}"
        except Exception as _e:
            log_suppressed(logger, _e)
        ip = request.client.host if request.client else "unknown"
        return f"ip:{ip}"

    async def dispatch(self, request: Request, call_next):
        # Only rate limit API calls (not static files)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        # V103.49: 豁免本地回环。桌面端在本机起 sidecar（127.0.0.1:17680）、
        # 评测/健康检查也走回环——这些是受信任的本地调用，不该被按公网 IP 限流
        # （之前 eval 串行打 112 条被误限流成 429→空答→假基线）。可用
        # HASHMM_RATELIMIT_TRUST_LOCAL=0 关闭这个豁免。
        import os as _os
        if _os.environ.get("HASHMM_RATELIMIT_TRUST_LOCAL", "1") == "1":
            _ip = request.client.host if request.client else ""
            if _ip in ("127.0.0.1", "::1", "localhost"):
                return await call_next(request)
        # 评测进程显式标记（gate 带的 header）也豁免。
        if request.headers.get("X-HashMM-Eval") == "1":
            return await call_next(request)

        # Skip health/metrics + admin GET (read-only, no abuse risk)
        if request.url.path in ("/api/health", "/api/metrics", "/api/metrics/prometheus"):
            return await call_next(request)
        if request.url.path.startswith("/api/admin/") and request.method == "GET":
            return await call_next(request)
        if request.url.path.startswith("/api/evolution/") and request.method == "GET":
            return await call_next(request)

        key = self._identity(request)
        now = time.time()

        # ── Redis sliding window (preferred: shared across workers, survives restart) ──
        r = self._get_redis()
        if r is not None:
            try:
                rk = f"ratelimit:min:{key}"
                cnt = r.incr(rk)
                if cnt == 1:
                    r.expire(rk, 60)
                if cnt > self.max_per_minute:
                    return JSONResponse(
                        {"ok": False, "error": {"code": "RATE_LIMITED",
                         "message": "请求过于频繁，请稍后再试"}},
                        status_code=429,
                    )
                return await call_next(request)
            except Exception:
                pass  # fall back to in-memory

        # ── In-memory fallback ──
        self.minute_counts[key] = [t for t in self.minute_counts[key] if now - t < 60]
        if len(self.minute_counts[key]) >= self.max_per_minute:
            return JSONResponse(
                {"ok": False, "error": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"}},
                status_code=429,
            )
        self.minute_counts[key].append(now)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """v17 Phase 66: baseline browser security headers.

    Clickjacking, MIME sniffing, referrer leakage, and (in production) HTTPS
    downgrade protection. CSP is emitted in Report-Only mode first so it can be
    tuned against the Next.js bundle before being enforced — flip the header
    name to 'Content-Security-Policy' once you've confirmed no violations.
    """
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        import os
        if os.environ.get("HASHMM_ENV", "dev").lower() in ("prod", "production"):
            resp.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        # CSP — configurable (v17 Phase 68):
        #   HASHMM_CSP          override the policy string ('off'/'none' disables)
        #   HASHMM_CSP_ENFORCE  '1' → enforced (Content-Security-Policy);
        #                       default → Report-Only (observe, don't block)
        csp = os.environ.get(
            "HASHMM_CSP",
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'none'")
        if csp.strip().lower() not in ("off", "none", "disabled", ""):
            enforce = os.environ.get("HASHMM_CSP_ENFORCE", "").lower() in ("1", "true", "yes", "on")
            header = "Content-Security-Policy" if enforce else "Content-Security-Policy-Report-Only"
            resp.headers.setdefault(header, csp)
        return resp
