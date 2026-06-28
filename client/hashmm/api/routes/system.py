"""System routes — health / metrics / usage / memory / models / plugins / debug."""
from __future__ import annotations
from hashmm.utils import get_logger as _get_logger, log_suppressed
_obs_logger = _get_logger(__name__)
import subprocess
from fastapi import APIRouter, Request, HTTPException, Response

from hashmm.api import database as db
from hashmm.api import app_state
from hashmm.api.auth import get_current_user

router = APIRouter(prefix="/api", tags=["system"])


def _metrics_authorized(request: Request) -> bool:
    """v17 Phase 69: gate the metrics endpoints (they were public → leaked
    internal traffic/cost/error/model info to anyone).

    Allowed when ANY of:
      - HASHMM_METRICS_PUBLIC is truthy (explicit opt-out), or
      - a configured HASHMM_METRICS_TOKEN matches the request's bearer /
        ?token= / X-Metrics-Token (so a Prometheus scraper can authenticate), or
      - the caller is an authenticated admin.
    """
    import os
    if os.environ.get("HASHMM_METRICS_PUBLIC", "").lower() in ("1", "true", "yes", "on"):
        return True
    token_cfg = os.environ.get("HASHMM_METRICS_TOKEN", "")
    if token_cfg:
        auth = request.headers.get("Authorization", "")
        bearer = auth[7:] if auth.startswith("Bearer ") else ""
        supplied = bearer or request.query_params.get("token", "") or request.headers.get("X-Metrics-Token", "")
        import hmac as _hmac
        if supplied and _hmac.compare_digest(supplied, token_cfg):
            return True
    user = get_current_user(request)
    return bool(user and user.get("role") == "admin")


@router.get("/health")
async def health_check(response: Response):
    """v12: Health check with loading status for frontend progress bar."""
    response.headers["Cache-Control"] = "no-cache"

    from hashmm.api.core.services import ServiceRegistry
    status = ServiceRegistry.status
    detail = ServiceRegistry.status_detail

    # Quick response during loading — don't query heavy services
    if status != "ready":
        return {
            "status": status,
            "detail": detail,
            "ready": False,
            "components": {},
        }

    components: dict[str, str] = {}

    # Database
    try:
        conv_count = db.get_audit_count()
        components["database"] = f"ok ({conv_count} audit entries)"
    except Exception:
        components["database"] = "error"

    # FAISS index — check via retriever bridge
    state = app_state.state
    try:
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        if pipe and hasattr(pipe, "vector_index") and pipe.vector_index:
            n = pipe.vector_index.total_vectors
            components["faiss_index"] = f"ok ({n} vectors)"
        elif state.get("faiss_index"):
            fi = state["faiss_index"]
            components["faiss_index"] = f"ok ({fi.ntotal} vectors)"
        elif state.get("metadata"):
            components["faiss_index"] = f"partial ({len(state['metadata'])} chunks)"
        else:
            components["faiss_index"] = "loading"
    except Exception:
        components["faiss_index"] = "not loaded"

    # Knowledge Graph
    try:
        from hashmm.kg.storage import KGStorage
        kg_stats = KGStorage().get_stats()
        if kg_stats.get("entities", 0) > 0:
            components["knowledge_graph"] = (
                f"ok ({kg_stats['entities']} entities, "
                f"{kg_stats['relations']} relations, "
                f"{kg_stats.get('communities', 0)} communities)"
            )
        else:
            components["knowledge_graph"] = "empty"
    except Exception:
        components["knowledge_graph"] = "not available"

    # Encoder
    if state.get("text_enc"):
        components["encoder"] = f"ok (BGE-M3)"
    else:
        components["encoder"] = "not loaded"

    # LLM
    if app_state.llm_fn:
        components["llm"] = "ok"
    else:
        components["llm"] = "not configured"

    all_ok = all("ok" in v or "partial" in v for v in components.values())

    # D4: GPU resource info
    gpu_info = {}
    try:
        import torch
        if torch.cuda.is_available():
            gpu_info["device"] = torch.cuda.get_device_name(0)
            gpu_info["vram_total_gb"] = round(torch.cuda.get_device_properties(0).total_mem / 1e9, 1)
            gpu_info["vram_used_gb"] = round(torch.cuda.memory_allocated(0) / 1e9, 2)
            gpu_info["vram_cached_gb"] = round(torch.cuda.memory_reserved(0) / 1e9, 2)
    except Exception as _e:
        log_suppressed(_obs_logger, _e)

    # Cache stats (v13)
    try:
        from hashmm.agent.cache import get_cache
        cache_stats = get_cache().stats
    except Exception:
        cache_stats = None

    # V103: report the active feature preset + which advanced flags are ON, so the
    # frontend (后端连接页) and the user can verify the project's depth is actually
    # enabled rather than silently running the basic path. Guarded; never fatal.
    feature_info: dict = {}
    try:
        import os as _os
        from hashmm.feature_presets import FLAG_DOCS, resolve_preset_name
        _on = sorted(
            f for f in FLAG_DOCS
            if _os.environ.get(f, "0").strip().lower() in ("1", "true", "yes", "on")
        )
        feature_info = {
            "preset": resolve_preset_name(_os.environ.get("HASHMM_PRESET")),
            "advanced_on": _on,
            "advanced_on_count": len(_on),
            "advanced_total": len(FLAG_DOCS),
        }
    except Exception:
        feature_info = {}

    return {
        "status": "ok" if all_ok else "degraded",
        "ready": True,
        "version": "13.0.0",
        "components": components,
        "gpu": gpu_info if gpu_info else None,
        "cache": cache_stats,
        "features": feature_info,
    }


@router.get("/health/ready",
            summary="就绪探针",
            description="Kubernetes readiness probe — 检查所有依赖是否就绪")
async def readiness_check(response: Response):
    """Deep dependency check for readiness probe."""
    try:
        from hashmm.api.core.services import ServiceRegistry
        checks = ServiceRegistry.health_check()
    except Exception as e:
        return Response(
            content=f'{{"status": "error", "error": "{str(e)[:200]}"}}',
            status_code=503,
            media_type="application/json",
        )

    all_ok = all(c.get("ok", False) for c in checks.values()
                 if isinstance(c, dict) and "ok" in c)
    status_code = 200 if all_ok else 503
    response.status_code = status_code
    return {
        "status": "ready" if all_ok else "degraded",
        "checks": checks,
    }


@router.get("/metrics")
async def get_metrics(request: Request):
    if not _metrics_authorized(request):
        raise HTTPException(403, "metrics 需要管理员或 HASHMM_METRICS_TOKEN（或设 HASHMM_METRICS_PUBLIC=1 公开）")
    out = {}
    if app_state.metrics:
        out = app_state.metrics.to_dict()
    # v15 Phase 6: request-level observability
    try:
        from hashmm.api.middleware import get_metrics_snapshot
        out["http"] = get_metrics_snapshot()
    except Exception as _e:
        log_suppressed(_obs_logger, _e)
    return out


@router.get("/metrics/prometheus")
async def get_metrics_prometheus(request: Request):
    """Prometheus text exposition format (point Grafana/Prometheus here).

    Authenticate the scraper with HASHMM_METRICS_TOKEN (Bearer/?token=/
    X-Metrics-Token), or set HASHMM_METRICS_PUBLIC=1, or call as an admin.
    """
    if not _metrics_authorized(request):
        raise HTTPException(403, "metrics 需要管理员或 HASHMM_METRICS_TOKEN（或设 HASHMM_METRICS_PUBLIC=1 公开）")
    from fastapi.responses import PlainTextResponse
    from hashmm.api.middleware import render_prometheus
    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


@router.get("/metrics/dashboard")
async def get_metrics_dashboard(request: Request):
    """运维 dashboard 聚合快照（前端面板一次拿全：延迟/成本/检索质量/SLO/路由/工具）。

    收口已有 observability 数据，不重造。鉴权与 /metrics 一致。
    """
    if not _metrics_authorized(request):
        raise HTTPException(403, "metrics 需要管理员或 HASHMM_METRICS_TOKEN（或设 HASHMM_METRICS_PUBLIC=1 公开）")
    try:
        from hashmm import observability as _obs
        return _obs.dashboard_snapshot()
    except Exception as _e:
        log_suppressed(_obs_logger, _e)
        return {"error": "dashboard snapshot 暂不可用"}


@router.get("/agent-usage")
async def get_agent_usage(request: Request):
    """外部 coding agent（Claude Code / Codex）token 用量仪表盘。

    适配自 fanbox：解析本机 ~/.claude 与 ~/.codex 会话日志。HashMM 桌面端内嵌终端
    跑这些 agent，这个端点让 UI 能显示"今天用 Claude Code / Codex 烧了多少 token"。
    只读、未装对应 agent 返回 null。鉴权与 /metrics 一致。
    """
    if not _metrics_authorized(request):
        raise HTTPException(403, "需要管理员或 HASHMM_METRICS_TOKEN（或设 HASHMM_METRICS_PUBLIC=1）")
    try:
        from hashmm.agent.agent_usage import all_agent_usage
        return all_agent_usage()
    except Exception as _e:
        log_suppressed(_obs_logger, _e)
        return {"claude_code": None, "codex": None}


@router.get("/metrics/traces")
async def get_metrics_traces(request: Request, limit: int = 200):
    """标准化 trace 导出（OTLP/JSON）。任何 OTLP 兼容后端（Jaeger/Tempo/Grafana）可拉取。

    不依赖 OTel 安装与否；鉴权与 /metrics 一致。
    """
    if not _metrics_authorized(request):
        raise HTTPException(403, "metrics 需要管理员或 HASHMM_METRICS_TOKEN（或设 HASHMM_METRICS_PUBLIC=1 公开）")
    try:
        from hashmm import observability as _obs
        return _obs.export_otlp_traces(limit=limit)
    except Exception as _e:
        log_suppressed(_obs_logger, _e)
        return {"resourceSpans": []}


@router.get("/debug")
async def debug_check():
    """System diagnostic — check DB, models, LLM, index."""
    checks: dict = {}

    # 1. Database
    try:
        models = db.list_models()
        users = db.list_users()
        checks["db"] = {"ok": True, "models": len(models), "users": len(users)}
        checks["models_detail"] = [
            {"name": m.get("name"), "provider": m.get("provider"),
             "model_name": m.get("model_name"), "is_default": m.get("is_default"),
             "base_url": m.get("base_url", "")[:50]}
            for m in models
        ]
    except Exception as e:
        checks["db"] = {"ok": False, "error": str(e)}

    # 2. Default model from DB
    try:
        dm = db.get_default_model()
        if dm:
            checks["default_model"] = {
                "name": dm.get("model_name"),
                "provider": dm.get("provider"),
                "has_api_key": bool(dm.get("api_key")),
            }
        else:
            checks["default_model"] = None
    except Exception as e:
        checks["default_model"] = {"error": str(e)}

    # 3. LLM + index status
    checks["llm_fn_loaded"] = app_state.llm_fn is not None
    checks["index_loaded"] = app_state.state.get("loaded", False)
    if app_state.state.get("loaded"):
        fi = app_state.state.get("faiss_index")
        checks["index"] = {
            "chunks": fi.ntotal if fi and hasattr(fi, "ntotal") else 0,
            "bits": app_state.state.get("bits", 0),
        }

    # 4. pdftotext
    try:
        r = subprocess.run(["which", "pdftotext"], capture_output=True, text=True, timeout=5)
        checks["pdftotext"] = r.stdout.strip() or "not found"
    except Exception:
        checks["pdftotext"] = "not available"

    return checks


@router.get("/usage")
async def get_usage(request: Request):
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    today = db.get_usage_today(user_id)
    month = db.get_usage_month(user_id)
    return {"today": today, "month": month}


@router.get("/experience")
async def get_experience():
    mem = app_state.memory
    if not mem:
        return {"total": 0, "recent": [], "profiles": {}}
    return {
        "total": len(mem.episodes),
        "recent": mem.episodes[-20:],
        "profiles": mem.profiles,
    }


# ── Memory ──

@router.get("/memory")
async def get_memory(request: Request):
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    memories = db.get_user_memories(user_id)
    return {"memories": memories}


@router.post("/memory")
async def add_memory(request: Request):
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    body = await request.json()
    mid = db.save_user_memory(
        user_id, body.get("category", ""),
        body.get("key", ""), body.get("value", ""),
    )
    return {"id": mid, "ok": True}


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str):
    db.delete_user_memory(memory_id)
    return {"ok": True}


# ── Models ──

@router.get("/models")
async def list_models():
    from hashmm.api.model_router import get_registry
    reg = get_registry()
    return {"models": reg.list_models(), "default": reg.default_model}


@router.post("/models/default")
async def set_default_model(request: Request):
    body = await request.json()
    from hashmm.api.model_router import get_registry
    reg = get_registry()
    name = body.get("model", "")
    if name in reg.models:
        reg.default_model = name
        return {"ok": True, "default": name}
    raise HTTPException(400, f"Model '{name}' not found")


# ── Plugins ──

@router.get("/plugins")
async def list_plugins():
    from hashmm.api.plugins import get_plugin_manager
    pm = get_plugin_manager()
    return {"plugins": pm.list_plugins()}


@router.post("/plugins/{name}/load")
async def load_plugin(name: str):
    from hashmm.api.plugins import get_plugin_manager
    import asyncio
    pm = get_plugin_manager()
    # V103.3: 插件加载（可能含导入/初始化重活）放线程，不冻结事件循环。
    ok = await asyncio.to_thread(pm.load, name)
    return {"ok": ok}


@router.get("/plugins/tools")
async def list_plugin_tools():
    from hashmm.api.plugins import get_plugin_manager
    pm = get_plugin_manager()
    return {"tools": pm.get_tool_definitions()}


# ═══════════════════════════════════════════════════════════════════
# v10.0: Performance Benchmark
# ═══════════════════════════════════════════════════════════════════

@router.get("/benchmark")
async def performance_benchmark(request: Request):
    """Run a quick performance benchmark — measures key system latencies.

    Returns timing for: embedding encode, FAISS search, BM25 search,
    reranker, LLM first-token, full response.
    """
    from hashmm.api.auth import require_admin
    require_admin(request)

    import time
    results: dict[str, dict] = {}
    test_query = "小米2024年营收"

    # 1. Embedding encode latency
    try:
        from hashmm.encoder_pool import EncoderPool
        t0 = time.perf_counter()
        EncoderPool.encode_query(test_query)
        results["embedding_encode"] = {
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "status": "ok",
            "cached": EncoderPool.cache_stats().get("hits", 0) > 0,
        }
    except Exception as e:
        results["embedding_encode"] = {"status": "error", "error": str(e)[:100]}

    # 2. Retrieval pipeline (FAISS + BM25 + Reranker)
    try:
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        if pipe:
            t0 = time.perf_counter()
            res = pipe.search(test_query, top_k=5)
            total_ms = round((time.perf_counter() - t0) * 1000, 1)
            results["retrieval_pipeline"] = {
                "latency_ms": total_ms,
                "status": "ok",
                "results": len(res),
                "top_score": round(res[0].score, 3) if res else 0,
            }
        else:
            results["retrieval_pipeline"] = {"status": "not_loaded"}
    except Exception as e:
        results["retrieval_pipeline"] = {"status": "error", "error": str(e)[:100]}

    # 3. LLM latency (time to first token)
    try:
        llm = app_state.llm_fn
        if llm and hasattr(llm, 'quick_call'):
            t0 = time.perf_counter()
            resp = llm.quick_call("回复OK", "test", max_tok=5)
            results["llm_first_token"] = {
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "status": "ok",
                "response": resp[:20],
            }
        else:
            results["llm_first_token"] = {"status": "not_configured"}
    except Exception as e:
        results["llm_first_token"] = {"status": "error", "error": str(e)[:100]}

    # 4. GPU info
    try:
        import torch
        if torch.cuda.is_available():
            results["gpu"] = {
                "name": torch.cuda.get_device_name(0),
                "memory_used_gb": round(torch.cuda.memory_allocated() / 1024**3, 2),
                "memory_total_gb": round(torch.cuda.get_device_properties(0).total_mem / 1024**3, 2),
            }
        else:
            results["gpu"] = {"status": "cpu_only"}
    except Exception:
        results["gpu"] = {"status": "unknown"}

    # 5. Target comparison
    targets = {
        "embedding_encode": 50,      # ms
        "retrieval_pipeline": 200,    # ms
        "llm_first_token": 1500,      # ms
    }
    for key, target in targets.items():
        if key in results and results[key].get("latency_ms"):
            actual = results[key]["latency_ms"]
            results[key]["target_ms"] = target
            results[key]["meets_target"] = actual <= target

    return {"benchmark": results, "timestamp": time.time()}
