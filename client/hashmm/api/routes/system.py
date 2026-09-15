"""System routes — health / metrics / usage / memory / models / plugins / debug."""
from __future__ import annotations
from hashmm.utils import get_logger as _get_logger, log_suppressed
_obs_logger = _get_logger(__name__)
import hashlib
import os
import subprocess
import time
from urllib.parse import urlparse
from fastapi import APIRouter, Request, HTTPException, Response

from hashmm.api import database as db
from hashmm.api import app_state
from hashmm.api.auth import get_current_user, require_auth
from hashmm.release import API_VERSION, PROTOCOLS, public_release_info

router = APIRouter(prefix="/api", tags=["system"])


def _identity_project_ref() -> str:
    """Return only the public Supabase project reference, never credentials."""
    try:
        from hashmm.api.supabase_auth import supabase_url
        host = (urlparse(supabase_url()).hostname or "").strip().lower()
        suffix = ".supabase.co"
        return host[:-len(suffix)] if host.endswith(suffix) else host
    except Exception:
        return ""


def _identity_fingerprint(user: dict) -> str:
    """Stable diagnostic proof without exposing the account UUID."""
    owner = str(user.get("uid") or "").removeprefix("sb_")
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:12] if owner else ""


@router.get("/client/bootstrap", summary="跨端客户端启动契约")
async def client_bootstrap(request: Request, response: Response):
    """Authenticated, secret-free contract used before cross-device sync.

    A successful response proves transport reachability *and* that the access
    token belongs to this HashMM deployment.  Clients must not infer this from
    an e-mail address or from the anonymous health endpoint.
    """
    user = require_auth(request)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    public_origin = os.environ.get("HASHMM_PUBLIC_URL", "").strip().rstrip("/")
    request_id = str(getattr(request.state, "request_id", "") or "")
    return {
        "server_version": API_VERSION,
        "release": public_release_info()["release"],
        "sync_protocol": PROTOCOLS["sync"],
        "release_manifest": public_release_info(),
        "authenticated": True,
        "user_sub_fingerprint": _identity_fingerprint(user),
        "supabase_project_ref": _identity_project_ref(),
        "canonical_origin": public_origin,
        "server_time": int(time.time()),
        "request_id": request_id,
        "features": {
            "projects": True,
            "conversation_sync": True,
            "realtime_wakeup": True,
            "direct_llm": True,
            "account_scoped_cache": True,
            "remote_fabric": "hashmm.remote.v4",
        },
    }


@router.get("/livez", include_in_schema=False)
async def liveness_check(response: Response):
    """Process liveness only; never touches models, indexes or Supabase."""
    response.headers["Cache-Control"] = "no-store"
    return {"status": "alive"}


@router.get("/readyz", include_in_schema=False)
async def fast_readiness_check(response: Response):
    """Fast admission readiness used by clients and reverse proxies."""
    from hashmm.api.core.services import ServiceRegistry
    ready = ServiceRegistry.status == "ready"
    response.status_code = 200 if ready else 503
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "ready" if ready else str(ServiceRegistry.status or "starting"),
        "ready": ready,
    }


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
async def health_check(request: Request, response: Response):
    """v12: Health check with loading status for frontend progress bar.

    V306 修 BACK-P0-01：只对【已鉴权】调用返回详细组件计数/模型名/GPU/缓存/特性开关等
    可用于系统画像的信息；匿名调用只返回最小状态（status/ready/version），避免公网探测。"""
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

    from hashmm import RELEASE as _rel
    # V306：匿名调用只给最小状态；鉴权后才返回可用于系统画像的详细信息。
    _authed = False
    try:
        _authed = get_current_user(request) is not None
    except Exception:
        _authed = False
    if not _authed:
        # 组件只给粗粒度状态词（去掉"(42 vectors)/(BGE-M3)"等数字与模型名），不泄露规模/型号。
        coarse = {k: str(v).split("(")[0].strip() for k, v in components.items()}
        return {
            "status": "ok" if all_ok else "degraded",
            "ready": True,
            "version": API_VERSION,
            "release": _rel,
            "sync_protocol": PROTOCOLS["sync"],
            "components": coarse,
        }
    return {
        "status": "ok" if all_ok else "degraded",
        "ready": True,
        "version": API_VERSION,
        "release": _rel,   # V218: 客户端据此判断远端后端是否旧代码（dispatch 404 排障）
        "sync_protocol": PROTOCOLS["sync"],
        "release_manifest": public_release_info(),
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
@router.get("/system/plugins", include_in_schema=False)
async def list_plugins(request: Request):
    from hashmm.api.auth import require_auth
    user = require_auth(request)
    from hashmm.api.plugins import get_plugin_manager
    pm = get_plugin_manager()
    return {"plugins": pm.list_plugins(), "can_manage": user.get("role") == "admin"}


@router.post("/plugins/{name}/load")
@router.post("/system/plugins/{name}/load", include_in_schema=False)
async def load_plugin(name: str, request: Request):
    from hashmm.api.auth import require_admin
    user = require_admin(request)
    from hashmm.api.plugins import get_plugin_manager
    import asyncio
    pm = get_plugin_manager()
    ok = await asyncio.to_thread(pm.load, name)
    item = next((p for p in pm.list_plugins() if p.get("name") == name), None)
    from hashmm.api import database as db
    db.audit(str(user.get("uid") or ""), str(user.get("sub") or ""), "plugin.load", f"name={name}; ok={ok}")
    if not ok:
        raise HTTPException(status_code=409, detail=(item or {}).get("error") or "插件未满足加载条件")
    return {"ok": ok, "plugin": item}


@router.post("/plugins/{name}/activate")
async def activate_manifest_plugin(name: str, request: Request):
    """Activate a declarative manifest; no executable-code trust is granted."""
    from hashmm.api.auth import require_admin
    user = require_admin(request)
    body = await request.json()
    from hashmm.api.plugins import PluginValidationError, get_plugin_manager
    try:
        info = get_plugin_manager().set_manifest_active(
            name,
            str(body.get("expected_sha256") or ""),
            active=True,
            actor=str(user.get("uid") or user.get("id") or "admin"),
        )
    except PluginValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.audit(str(user.get("uid") or ""), str(user.get("sub") or ""), "plugin.manifest.activate", f"name={name}; sha256={info.sha256}")
    return {"ok": True, "plugin": next(item for item in get_plugin_manager().list_plugins() if item.get("name") == name)}


@router.post("/plugins/{name}/deactivate")
async def deactivate_manifest_plugin(name: str, request: Request):
    from hashmm.api.auth import require_admin
    user = require_admin(request)
    body = await request.json()
    from hashmm.api.plugins import PluginValidationError, get_plugin_manager
    try:
        info = get_plugin_manager().set_manifest_active(
            name,
            str(body.get("expected_sha256") or ""),
            active=False,
            actor=str(user.get("uid") or user.get("id") or "admin"),
        )
    except PluginValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.audit(str(user.get("uid") or ""), str(user.get("sub") or ""), "plugin.manifest.deactivate", f"name={name}; sha256={info.sha256}")
    return {"ok": True, "plugin": next(item for item in get_plugin_manager().list_plugins() if item.get("name") == name)}


@router.post("/plugins/{name}/quarantine")
async def quarantine_plugin(name: str, request: Request):
    from hashmm.api.auth import require_admin
    user = require_admin(request)
    from hashmm.api.plugins import PluginValidationError, get_plugin_manager
    try:
        result = get_plugin_manager().quarantine(
            name, actor=str(user.get("uid") or user.get("id") or "admin"),
        )
    except PluginValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.audit(str(user.get("uid") or ""), str(user.get("sub") or ""), "plugin.quarantine", f"name={name}; quarantine_id={result.get('quarantine_id','')}")
    return result


@router.post("/plugins/{name}/trust")
@router.post("/system/plugins/{name}/trust", include_in_schema=False)
async def trust_plugin(name: str, request: Request):
    from hashmm.api.auth import require_admin
    user = require_admin(request)
    from hashmm.api.plugins import PluginValidationError, get_plugin_manager
    body = await request.json()
    try:
        info = get_plugin_manager().trust(
            name,
            str(body.get("expected_sha256") or ""),
            trusted_by=str(user.get("uid") or user.get("id") or "admin"),
        )
    except PluginValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from hashmm.api import database as db
    db.audit(str(user.get("uid") or ""), str(user.get("sub") or ""), "plugin.trust", f"name={name}; sha256={info.sha256}")
    return {
        "ok": True,
        "status": "trusted_pending_load",
        "plugin": next(
            item for item in get_plugin_manager().list_plugins()
            if item.get("name") == info.name
        ),
    }


@router.post("/plugins/{name}/revoke")
@router.post("/system/plugins/{name}/revoke", include_in_schema=False)
async def revoke_plugin(name: str, request: Request):
    from hashmm.api.auth import require_admin
    user = require_admin(request)
    from hashmm.api.plugins import get_plugin_manager
    ok = get_plugin_manager().revoke(name)
    from hashmm.api import database as db
    db.audit(str(user.get("uid") or ""), str(user.get("sub") or ""), "plugin.revoke", f"name={name}; ok={ok}")
    if not ok:
        raise HTTPException(status_code=404, detail="插件没有可撤销的信任收据")
    return {"ok": True}


@router.post("/plugins/install")
async def install_plugin(request: Request, replace: bool = False):
    """Stage an administrator-uploaded ZIP; never trust or execute it."""
    from hashmm.api.auth import require_admin
    user = require_admin(request)
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type not in {"application/zip", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="请上传 application/zip 文件")
    package = await request.body()
    from hashmm.api.plugins import PluginValidationError, get_plugin_manager
    import asyncio
    try:
        result = await asyncio.to_thread(
            get_plugin_manager().install_zip,
            package,
            expected_archive_sha256=request.headers.get("x-plugin-sha256", ""),
            replace=bool(replace),
            installed_by=str(user.get("uid") or ""),
        )
    except PluginValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from hashmm.api import database as db
    plugin = result.get("plugin") or {}
    db.audit(
        str(user.get("uid") or ""), str(user.get("sub") or ""), "plugin.install",
        f"name={plugin.get('name','')}; action={result.get('action','')}; sha256={result.get('package_sha256','')}",
    )
    return result


@router.get("/plugins/{name}/diagnostics")
async def plugin_diagnostics(name: str, request: Request):
    from hashmm.api.auth import require_admin
    require_admin(request)
    from hashmm.api.plugins import PluginValidationError, get_plugin_manager
    try:
        return get_plugin_manager().diagnostics(name)
    except PluginValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/plugins/tools")
@router.get("/system/plugins/tools", include_in_schema=False)
async def list_plugin_tools(request: Request):
    from hashmm.api.auth import require_auth
    require_auth(request)
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


# ── 路线图阶段 A：能力模块状态与开关 ──

@router.get("/modules", summary="能力模块状态（RAG/本机/联网/记忆/派活/图片）")
async def list_modules(request: Request):
    """任何登录用户可查看；开关需管理员。返回每个模块的启用/健康/工具集。"""
    from hashmm.agent import modules as _mod
    return {"modules": _mod.status(),
            "note": "模块可整体启停（RAG 等）；关闭后其工具从 Agent 工具列表消失，主循环不受影响"}


@router.post("/modules/{key}/toggle", summary="启停一个能力模块（管理员，进程级）")
async def toggle_module(key: str, request: Request):
    """通过设置进程环境变量 HASHMM_MODULE_<KEY> 实现即时启停（下一轮对话生效）。
    core 模块不可关。持久化请写进 start-hashmm.sh。"""
    from hashmm.api.auth import require_admin
    admin = require_admin(request)
    from hashmm.agent import modules as _mod
    import os as _os
    m = _mod.get_module(key)
    if not m:
        raise HTTPException(404, f"未知模块: {key}")
    if m.core:
        raise HTTPException(400, "核心模块不可关闭")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    enable = bool(body.get("enable", not m.enabled()))
    _os.environ[f"HASHMM_MODULE_{key.upper()}"] = "1" if enable else "0"
    db.audit(admin["uid"], admin["sub"], "module_toggle", f"{key}={'on' if enable else 'off'}")
    return {"ok": True, "key": key, "enabled": enable,
            "hint": "进程级生效（下一轮对话）；永久生效请写入 start-hashmm.sh"}
