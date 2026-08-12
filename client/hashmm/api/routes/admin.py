"""Admin routes — users / models / kbs / docs / skills / logs."""
from __future__ import annotations
import json, re
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from starlette.concurrency import run_in_threadpool

from hashmm.api import database as db
from hashmm.api import model_manager
from hashmm.api import app_state
from hashmm.api.admin_contract import admin_user_wire
from hashmm.api.auth import require_admin, require_auth, require_conv_access
from hashmm.model_providers import (
    ProviderConfigError,
    list_provider_specs,
    normalize_model_config,
    parse_model_options,
)
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.admin")
from hashmm.api.schemas import (
    RegisterRequest, UserUpdateRequest, PasswordChangeRequest,
    ModelCreateRequest, ModelUpdateRequest, KBRequest,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ═══════════════════════════════════════════════════════════════════
# v12: Aggregated overview — one request for all admin data
# ═══════════════════════════════════════════════════════════════════

@router.get("/overview")
async def admin_overview(request: Request):
    """Truthful control-plane summary with source-level availability.

    A failed source is represented as ``None`` plus an issue. It is never
    converted to a convincing zero or empty collection.
    """
    require_admin(request)
    import time
    result: dict = {"schema": "hashmm.admin-overview.v2", "generated_at": time.time(), "sources": {}}

    def read_source(name: str, fn):
        started = time.perf_counter()
        try:
            value = fn()
            result["sources"][name] = {
                "status": "ok", "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
            }
            return value
        except Exception as exc:
            log_suppressed(logger, exc)
            result["sources"][name] = {
                "status": "unavailable", "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
                "issue": "数据源暂时无法验证",
            }
            return None

    result["models"] = read_source("models", db.list_models)
    result["users"] = read_source("users", db.list_users)
    result["knowledge_bases"] = read_source("knowledge_bases", db.list_kbs)
    try:
        from hashmm.evolution.skill_manager import get_skill_manager
        result["evolution_skills"] = read_source(
            "evolution_skills", lambda: len(get_skill_manager().list_skills(include_all=True)),
        )
    except Exception as exc:
        log_suppressed(logger, exc)
        result["evolution_skills"] = None
        result["sources"]["evolution_skills"] = {"status": "unavailable", "issue": "数据源暂时无法验证"}
    from hashmm.api.core.services import ServiceRegistry
    result["services"] = read_source("services", lambda: {
        "status": ServiceRegistry.status,
        "detail": ServiceRegistry.status_detail,
    })
    result["recent_logs"] = read_source("audit", db.get_audit_count)
    try:
        from hashmm.api import jobs as _jobs
        result["jobs"] = read_source("jobs", _jobs.status_counts)
    except Exception as exc:
        log_suppressed(logger, exc)
        result["jobs"] = None
        result["sources"]["jobs"] = {"status": "unavailable", "issue": "任务数据源暂时无法验证"}
    return result


# ── Governance / audit (v17 Phase 84) ──

@router.get("/audit/tools")
async def admin_audit_tools(request: Request, limit: int = 50):
    """Recent agent tool-action audit entries (Phase 77 audit stream).

    Returns [] when auditing isn't enabled (HASHMM_AUDIT_TOOLS) or nothing has
    been recorded. Admin only."""
    require_admin(request)
    try:
        from hashmm.agent.tool_governance import read_recent, audit_enabled
        limit = max(1, min(int(limit or 50), 500))
        return {"enabled": audit_enabled(), "entries": read_recent(limit)}
    except Exception as _e:
        log_suppressed(logger, _e)   # V308：原写法引用了根本不存在的 _obs_logger，
                                     # 靠 `in globals()` 守卫兜住 → 异常从未被记录。用真 logger。
        return {"enabled": False, "entries": []}


@router.get("/governance")
async def admin_governance(request: Request):
    """Consolidated governance/ops snapshot: approval & high-risk counters,
    job backlog by status, and worker-pool stats. Admin only."""
    require_admin(request)
    out: dict = {}
    try:
        from hashmm.agent.tool_governance import governance_metrics, approval_enabled, audit_enabled
        out["governance"] = governance_metrics()
        out["approval_enabled"] = approval_enabled()
        out["audit_enabled"] = audit_enabled()
    except Exception:
        out["governance"] = {}
    try:
        from hashmm.api import jobs as _jobs
        out["jobs"] = _jobs.status_counts()
    except Exception:
        out["jobs"] = {}
    try:
        from hashmm.api import job_queue as _jq
        out["job_queue"] = _jq.current_stats()
    except Exception:
        out["job_queue"] = None
    try:
        from hashmm.api import supabase_sync as _supabase_sync
        out["sync_mirror_queue"] = _supabase_sync.queue_stats()
    except Exception:
        out["sync_mirror_queue"] = None
    return out


# ── Users ──

@router.get("/users")
async def list_users(request: Request):
    require_admin(request)
    local = db.list_users()
    merged: dict[str, dict] = {str(u.get("id")): dict(u) for u in local}
    try:
        from hashmm.api import supabase_auth, supabase_sync
        authorization = request.headers.get("authorization", "")
        caller_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        # Match the desktop user directory first: the caller-scoped RPC exposes
        # auth email/last-login fields without requiring a service-role secret
        # on this backend.  Older deployments fall back to service-role profiles.
        profiles = await run_in_threadpool(supabase_auth.list_all_profiles, caller_token)
        if profiles is None:
            profiles = await run_in_threadpool(supabase_sync.list_profiles)
        for profile in profiles or []:
            raw_id = str(profile.get("id") or "")
            if not raw_id:
                continue
            uid = f"sb_{raw_id}"
            current = merged.get(uid, {})
            merged[uid] = {
                **current,
                "id": uid,
                "username": profile.get("username") or current.get("username") or raw_id,
                "display_name": profile.get("display_name") or current.get("display_name") or "",
                "email": profile.get("email") or current.get("email") or "",
                "role": "admin" if profile.get("is_admin") else current.get("role", "user"),
                "created_at": profile.get("created_at") or current.get("created_at") or "",
                "last_sign_in_at": profile.get("last_sign_in_at") or current.get("last_sign_in_at") or "",
                "identity_source": "supabase",
            }
    except Exception as e:
        log_suppressed(logger, e)
    rows = [admin_user_wire(u) for u in merged.values()]
    return sorted(rows, key=lambda u: u["created_at"], reverse=True)


@router.post("/users")
async def create_user(req: RegisterRequest, request: Request):
    admin = require_admin(request)
    user = db.create_user(req.username, req.password, req.display_name)
    if not user:
        raise HTTPException(409, "用户名已存在")
    db.audit(admin["uid"], admin["sub"], "create_user", req.username)
    return user


@router.put("/users/{user_id}")
async def update_user(user_id: str, req: UserUpdateRequest, request: Request):
    admin = require_admin(request)
    kw = {k: v for k, v in req.model_dump().items() if v is not None}
    db.update_user(user_id, **kw)
    db.audit(admin["uid"], admin["sub"], "update_user", f"{user_id}: {kw}")
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, request: Request):
    admin = require_admin(request)
    ok = db.delete_user(user_id)
    if not ok:
        raise HTTPException(400, "无法删除（可能是管理员账户）")
    db.audit(admin["uid"], admin["sub"], "delete_user", user_id)
    return {"ok": True}


@router.post("/users/{user_id}/password")
async def reset_password(user_id: str, req: PasswordChangeRequest, request: Request):
    admin = require_admin(request)
    db.change_password(user_id, req.new_password)
    # v17 Phase 67: resetting a password must also revoke that user's existing
    # sessions (otherwise old tokens keep working after a forced reset).
    from hashmm.api import auth as _auth
    _auth.bump_and_revoke(user_id)
    db.audit(admin["uid"], admin["sub"], "reset_password", user_id)
    return {"ok": True}


@router.post("/users/{user_id}/force-logout")
async def force_logout(user_id: str, request: Request):
    """v17 Phase 67: admin force-logout — revoke ALL of a user's tokens
    (access + refresh) by bumping their token_version. Effective within the
    auth-layer cache window (~10s)."""
    admin = require_admin(request)
    if not db.get_user(user_id):
        raise HTTPException(404, "用户不存在")
    from hashmm.api import auth as _auth
    new_version = _auth.bump_and_revoke(user_id)
    db.audit(admin["uid"], admin["sub"], "force_logout", user_id)
    return {"ok": True, "user_id": user_id, "token_version": new_version}


# ── Models ──

@router.get("/models")
async def list_models(request: Request):
    require_admin(request)
    models = db.list_models()
    for m in models:
        if "api_key_enc" in m:
            del m["api_key_enc"]
    return models


@router.get("/provider-balance", summary="查询当前默认模型所属提供商的 API 余额", tags=["admin"])
async def provider_balance(request: Request):
    """V103.90 查 API 余额。目前支持 DeepSeek（GET /user/balance）。
    密钥留在服务端不外泄；其它提供商返回 supported=False，前端据此提示。"""
    require_admin(request)
    model = db.get_default_model()
    if not model or not model.get("api_key"):
        return {"ok": False, "supported": False, "reason": "未配置默认模型或缺少 API Key"}
    api_key = model.get("api_key") or ""
    base_url = (model.get("base_url") or "").lower()
    provider = (model.get("provider") or "").lower()
    model_name = model.get("name") or model.get("model_name") or ""
    is_deepseek = ("deepseek" in base_url) or ("deepseek" in provider)
    if not is_deepseek:
        return {"ok": True, "supported": False, "provider": model.get("provider") or "",
                "model": model_name,
                "reason": "当前提供商暂不支持余额查询（目前仅支持 DeepSeek）"}
    import requests
    try:
        r = requests.get(
            "https://api.deepseek.com/user/balance",
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=15)
        if r.status_code != 200:
            return {"ok": False, "supported": True, "provider": "deepseek", "model": model_name,
                    "reason": f"查询失败 HTTP {r.status_code}"}
        data = r.json()
        infos = data.get("balance_infos") or []
        info = infos[0] if infos else {}
        return {"ok": True, "supported": True, "provider": "deepseek", "model": model_name,
                "is_available": bool(data.get("is_available")),
                "currency": info.get("currency", ""),
                "total_balance": info.get("total_balance", ""),
                "granted_balance": info.get("granted_balance", ""),
                "topped_up_balance": info.get("topped_up_balance", "")}
    except Exception as e:
        log_suppressed(logger, e)
        return {"ok": False, "supported": True, "provider": "deepseek", "model": model_name,
                "reason": f"查询出错：{e}"}


@router.post("/models")
async def create_model(req: ModelCreateRequest, request: Request):
    admin = require_admin(request)
    provider_config = dict(req.config or {})
    if req.wire_api:
        provider_config["wire_api"] = req.wire_api
    try:
        normalized = normalize_model_config({**req.model_dump(), "config": provider_config})
    except ProviderConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    model = db.create_model(
        req.name, normalized["provider"], normalized["base_url"], req.api_key,
        req.model_name.strip(), created_by=admin["sub"],
        temperature=req.temperature, max_tokens=req.max_tokens,
        config={**provider_config, "wire_api": normalized["wire_api"]},
    )
    current_default = db.get_default_model()
    should_promote = not current_default or not current_default.get("api_key")
    if should_promote and model.get("id") and req.api_key:
        db.set_default_model(model["id"])
    app_state.reload_llm()
    model_manager.invalidate_model_catalog_cache()
    db.audit(admin["uid"], admin["sub"], "create_model", req.name)
    return model


@router.put("/models/{model_id}")
async def update_model(model_id: str, req: ModelUpdateRequest, request: Request):
    admin = require_admin(request)
    current = db.get_model(model_id)
    if not current:
        raise HTTPException(404, "模型配置不存在")
    changes = {k: v for k, v in req.model_dump().items() if v is not None}
    provider_config = parse_model_options(current)
    if req.config is not None:
        provider_config.update(req.config)
    if req.wire_api is not None:
        provider_config["wire_api"] = req.wire_api
    merged = {**current, **changes, "config": provider_config}
    try:
        normalized = normalize_model_config(merged)
    except ProviderConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    kw = {
        "provider": normalized["provider"],
        "base_url": normalized["base_url"],
        "model_name": str(merged.get("model_name") or "").strip(),
        "temperature": merged.get("temperature", 0.1),
        "max_tokens": merged.get("max_tokens", 4096),
        "config": {**provider_config, "wire_api": normalized["wire_api"]},
    }
    if req.name is not None:
        kw["name"] = req.name
    if req.api_key is not None:
        kw["api_key"] = req.api_key
    db.update_model(model_id, **kw)
    app_state.reload_llm()
    model_manager.invalidate_model_catalog_cache()
    db.audit(admin["uid"], admin["sub"], "update_model", f"{model_id}: {list(kw.keys())}")
    return {"ok": True}


@router.delete("/models/{model_id}")
async def delete_model(model_id: str, request: Request):
    admin = require_admin(request)
    ok = db.delete_model(model_id)
    if not ok:
        raise HTTPException(400, "无法删除默认模型")
    model_manager.invalidate_model_catalog_cache()
    db.audit(admin["uid"], admin["sub"], "delete_model", model_id)
    return {"ok": True}


@router.post("/models/{model_id}/default")
async def set_default_model(model_id: str, request: Request):
    admin = require_admin(request)
    db.set_default_model(model_id)
    app_state.reload_llm()
    model_manager.invalidate_model_catalog_cache()
    db.audit(admin["uid"], admin["sub"], "set_default_model", model_id)
    # V252：切换默认模型即刷新 Supabase 上报（App 直连兜底跟随最新默认模型）
    try:
        from hashmm.api import supabase_sync as _ss
        if _ss.enabled():
            _m = db.get_default_model()
            if _m:
                _ss.push_direct_llm(_m)
    except Exception:
        pass
    return {"ok": True}


@router.post("/models/test")
async def test_model(req: ModelCreateRequest, request: Request):
    require_admin(request)
    cfg = {
        "api_key": req.api_key, "base_url": req.base_url,
        "model_name": req.model_name, "provider": req.provider,
        "temperature": req.temperature, "max_tokens": req.max_tokens,
        "wire_api": req.wire_api, "config": req.config,
    }
    return model_manager.test_model_connection(cfg)


@router.post("/models/discover")
async def discover_models(req: ModelCreateRequest, request: Request):
    """Discover exact account-visible ids without persisting the credential."""
    require_admin(request)
    cfg = {
        "api_key": req.api_key, "base_url": req.base_url,
        "model_name": req.model_name or "_discovery_placeholder_",
        "provider": req.provider, "wire_api": req.wire_api,
        "config": req.config,
    }
    return model_manager.discover_provider_models(cfg)


@router.get("/models/presets")
async def model_presets(request: Request):
    require_admin(request)
    return model_manager.PROVIDER_PRESETS


@router.get("/models/providers")
async def list_providers(request: Request):
    """Return provider protocol/capability metadata from the runtime registry."""
    require_admin(request)
    return {"providers": list_provider_specs()}


# ── Knowledge Bases ──

@router.get("/kbs")
async def list_kbs(request: Request):
    require_admin(request)
    return db.list_kbs()


@router.post("/kbs")
async def create_kb(req: KBRequest, request: Request):
    admin = require_admin(request)
    kb = db.create_kb(req.name, req.description, req.allowed_roles, admin["sub"])
    db.audit(admin["uid"], admin["sub"], "create_kb", req.name)
    return kb


@router.put("/kbs/{kb_id}")
async def update_kb(kb_id: str, req: KBRequest, request: Request):
    admin = require_admin(request)
    db.update_kb(kb_id, name=req.name, description=req.description,
                 allowed_roles=req.allowed_roles)
    db.audit(admin["uid"], admin["sub"], "update_kb", kb_id)
    return {"ok": True}


@router.delete("/kbs/{kb_id}")
async def delete_kb(kb_id: str, request: Request):
    admin = require_admin(request)
    db.delete_kb(kb_id)
    db.audit(admin["uid"], admin["sub"], "delete_kb", kb_id)
    return {"ok": True}


# ── Documents ──

_DOC_UPLOAD_SUFFIXES = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx",
    ".txt", ".md", ".csv", ".html", ".htm",
}
_INVALID_DOC_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _validated_doc_id(doc_id: str) -> str:
    """Keep document object IDs inside ``data/docs``.

    IDs may contain Chinese and punctuation, so an ASCII-only regex would
    reject legitimate data.  What matters is forbidding path semantics and
    control bytes before any destructive filesystem operation.
    """
    value = str(doc_id or "").strip()
    if (
        not value or len(value) > 220 or value in {".", ".."}
        or "/" in value or "\\" in value
        or any(ord(ch) < 32 for ch in value)
    ):
        raise HTTPException(400, "doc_id 格式无效")
    return value


def _safe_doc_upload_name(raw_name: str | None) -> str:
    raw = str(raw_name or "").replace("\\", "/")
    base = raw.rsplit("/", 1)[-1].strip().strip(".")
    base = _INVALID_DOC_NAME.sub("_", base).strip()
    if not base:
        raise HTTPException(400, "文件名无效")
    suffix = Path(base).suffix.lower()
    if suffix not in _DOC_UPLOAD_SUFFIXES:
        raise HTTPException(415, f"不支持的文档类型: {suffix or '无扩展名'}")
    if len(base) > 180:
        keep = max(1, 180 - len(suffix))
        base = f"{Path(base).stem[:keep]}{suffix}"
    return base


def _doc_upload_limit() -> int:
    import os
    try:
        configured = int(os.environ.get("HASHMM_MAX_DOC_UPLOAD_BYTES", 100 * 1024 * 1024))
    except (TypeError, ValueError):
        configured = 100 * 1024 * 1024
    return max(1024 * 1024, min(configured, 512 * 1024 * 1024))


async def _stage_document_upload(upload, staging: Path) -> tuple[Path, int, str]:
    """Stream one upload to an atomic, content-addressed staging file."""
    import hashlib
    import os
    import uuid

    name = _safe_doc_upload_name(getattr(upload, "filename", ""))
    limit = _doc_upload_limit()
    staging.mkdir(parents=True, exist_ok=True)
    temp = staging / f".upload-{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    total = 0
    try:
        with open(temp, "xb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, f"文件超过上传上限（{limit // (1024 * 1024)} MB）")
                digest.update(chunk)
                handle.write(chunk)
        if total == 0:
            raise HTTPException(400, "不能上传空文件")
        sha256 = digest.hexdigest()
        target = staging / name
        if target.exists():
            existing = hashlib.sha256()
            with open(target, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    existing.update(chunk)
            if existing.hexdigest() != sha256:
                target = staging / f"{Path(name).stem}-{sha256[:12]}{Path(name).suffix.lower()}"
        if target.exists():
            temp.unlink(missing_ok=True)
        else:
            os.replace(temp, target)
        return target, total, sha256
    except Exception:
        temp.unlink(missing_ok=True)
        raise

@router.get("/docs")
async def list_docs(request: Request):
    require_admin(request)
    try:
        docs: dict[str, dict] = {}

        # 1. Scan data/docs/ for parsed documents (primary source)
        import os as _os
        for root_dir in [Path(_os.getcwd()), Path("/root/autodl-tmp"), Path("/root")]:
            docs_dir = root_dir / "data" / "docs"
            if not docs_dir.exists():
                continue
            for d in sorted(docs_dir.iterdir()):
                if not d.is_dir():
                    continue
                parsed_file = d / "parsed.json"
                if not parsed_file.exists():
                    continue
                try:
                    info = json.loads(parsed_file.read_text(encoding="utf-8"))
                    did = info.get("doc_id", d.name)
                    # Read chunks count from data/chunks.jsonl
                    chunk_count = 0
                    chunks_file = root_dir / "data" / "chunks.jsonl"
                    if chunks_file.exists():
                        with open(chunks_file, encoding="utf-8") as cf:
                            for line in cf:
                                try:
                                    entry = json.loads(line.strip())
                                    if entry.get("doc_id") == did:
                                        chunk_count += 1
                                except Exception as _e:
                                    log_suppressed(logger, _e)

                    docs[did] = {
                        "doc_id": did,
                        "filename": info.get("filename", d.name),
                        "chunks": chunk_count or info.get("num_blocks", 0),
                        "modalities": [],
                        "source": "parsed",
                        "tables": info.get("tables", 0),
                        "images": info.get("images", 0),
                        "quality": info.get("quality", {}).get("overall_score", -1),
                        "parser": info.get("parser_used", ""),
                        "file_type": info.get("file_type", ""),
                        "file_size": info.get("file_size", 0),
                        "sections": len(info.get("sections", [])),
                        "full_text_chars": info.get("full_text_chars", 0),
                    }
                    # v11: Read meta.json for source_path + timestamps + folder
                    meta_file = d / "meta.json"
                    if meta_file.exists():
                        try:
                            meta = json.loads(meta_file.read_text(encoding="utf-8"))
                            # Absolute server paths are implementation details,
                            # not useful UI data.  Expose only whether reparsing
                            # has an available source receipt.
                            docs[did]["source_available"] = bool(meta.get("source_path", ""))
                            docs[did]["folder"] = meta.get("folder", "")
                            docs[did]["created_at"] = meta.get("created_at", parsed_file.stat().st_mtime)
                            docs[did]["updated_at"] = meta.get("updated_at", parsed_file.stat().st_mtime)
                        except Exception as _e:
                            log_suppressed(logger, _e)
                    if "created_at" not in docs[did]:
                        docs[did]["created_at"] = parsed_file.stat().st_mtime
                        docs[did]["updated_at"] = parsed_file.stat().st_mtime
                except Exception as _e:
                    log_suppressed(logger, _e)
            if docs:
                break  # Found docs, stop searching

        # 2. Also include docs from FAISS metadata (old indexed docs)
        try:
            metadata = app_state.state.get("metadata", [])
            if not metadata:
                metadata = _load_metadata_from_disk()
            for e in metadata:
                did = e.get("doc_id", "unknown")
                if did not in docs:
                    if did not in docs:
                        docs[did] = {"doc_id": did, "filename": e.get("source", did),
                                     "chunks": 0, "modalities": [], "source": "index"}
                    docs[did]["chunks"] = docs[did].get("chunks", 0) + 1
        except Exception as _e:
            log_suppressed(logger, _e)

        result = list(docs.values())
        result.sort(key=lambda x: -x.get("chunks", 0))
        return {"docs": result,
                "total_chunks": sum(d.get("chunks", 0) for d in result),
                "total_docs": len(result)}
    except Exception as e:
        return {"docs": [], "total_chunks": 0, "total_docs": 0, "error": str(e)}


@router.delete("/docs/{doc_id}")
async def delete_doc(doc_id: str, request: Request):
    """Delete a parsed document and its data."""
    admin = require_admin(request)
    doc_id = _validated_doc_id(doc_id)
    import os as _os, shutil
    deleted = False
    for root_dir in [Path(_os.getcwd()), Path("/root/autodl-tmp")]:
        doc_dir = root_dir / "data" / "docs" / doc_id
        if doc_dir.exists():
            shutil.rmtree(doc_dir)
            deleted = True
        chunks_file = root_dir / "data" / "chunks.jsonl"
        if chunks_file.exists():
            remaining = []
            with open(chunks_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("doc_id") != doc_id:
                            remaining.append(line)
                    except Exception:
                        remaining.append(line)
            with open(chunks_file, "w", encoding="utf-8") as f:
                f.writelines(remaining)
        if deleted:
            break
    # Remove from KG
    try:
        from hashmm.kg.storage import KGStorage
        storage = KGStorage()
        if storage.exists():
            kg, cm = storage.load()
            kg.remove_by_source(doc_id)
            storage.save(kg, cm)
    except Exception as _e:
        log_suppressed(logger, _e)
    # v6.0: Remove from FAISS + BM25 indexes
    try:
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        if pipe:
            removed_v = pipe.vector_index.remove_by_doc(doc_id)
            pipe.bm25_index.remove_by_doc(doc_id)
            pipe.vector_index.save()
            pipe.bm25_index.save()
            logger.info(f"Removed {removed_v} vectors for {doc_id}")
    except Exception as _e:
        logger.debug(f"Index cleanup failed: {_e}")
    db.audit(admin["uid"], admin["sub"], "delete_doc", doc_id)
    return {"ok": deleted, "doc_id": doc_id}


@router.post("/docs/{doc_id}/reparse")
async def reparse_doc(doc_id: str, request: Request):
    """Re-parse an existing document: delete old data → re-ingest with same doc_id → reload pipeline."""
    admin = require_admin(request)
    doc_id = _validated_doc_id(doc_id)
    import os as _os

    # ── Step 1: Find the original file ──
    original_path = None
    original_filename = ""

    # Derive the file stem from doc_id
    stem = doc_id[4:] if doc_id.startswith("doc-") else doc_id
    is_hash_id = all(c in "0123456789abcdef" for c in stem[:16]) and len(stem) > 16

    # First: read filename from parsed.json / meta.json
    for root_dir in [Path("/root/autodl-tmp"), Path(_os.getcwd())]:
        doc_dir = root_dir / "data" / "docs" / doc_id
        if not doc_dir.exists():
            continue
        for meta_name in ["meta.json", "parsed.json"]:
            meta_file = doc_dir / meta_name
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                # meta.json: check source_path first
                src = meta.get("source_path", "")
                if src and Path(src).exists():
                    original_path = Path(src)
                    break
                # parsed.json: get filename
                fn = meta.get("filename", "")
                if fn:
                    original_filename = fn
                # Also check file_path field
                fp = meta.get("file_path", meta.get("source_file", ""))
                if fp and Path(fp).exists():
                    original_path = Path(fp)
                    break
            except Exception as _e:
                log_suppressed(logger, _e)
        if original_path:
            break

    # Second: search known directories
    if not original_path:
        search_dirs = [
            Path("/root/autodl-tmp/data/staging"),
            Path("/root/autodl-tmp/data/pdfs"),
            Path("/root/autodl-tmp/data/uploads"),
            Path(f"/root/autodl-tmp/data/docs/{doc_id}"),
            Path("data/staging"),
            Path("data/pdfs"),
            Path("data/uploads"),
        ]

        # Build candidate filenames
        candidates = set()
        if original_filename:
            candidates.add(original_filename)
        # For name-based doc_id: stem IS the filename (minus extension)
        if not is_hash_id:
            for ext in [".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"]:
                candidates.add(stem + ext)

        for d in search_dirs:
            if not d.exists():
                continue
            # Exact filename match
            for fn in candidates:
                candidate = d / fn
                if candidate.exists() and candidate.is_file():
                    original_path = candidate
                    break
            if original_path:
                break
            # Fuzzy match (only for non-hash IDs or when we have original_filename)
            if not is_hash_id or original_filename:
                match_str = original_filename.rsplit(".", 1)[0] if original_filename else stem
                for f in d.iterdir():
                    if not f.is_file():
                        continue
                    if f.suffix.lower() in (".pdf", ".docx", ".doc", ".txt", ".md", ".csv", ".xlsx"):
                        if match_str and (match_str in f.stem or f.stem in match_str):
                            original_path = f
                            break
            if original_path:
                break

        # Last resort for hash IDs: check if any file in pdfs/ or staging/ was NOT parsed yet
        # by listing all parsed filenames and finding unmatched ones
        if not original_path and is_hash_id:
            # Try to find by listing the doc_dir for the original PDF copy
            for root_dir in [Path("/root/autodl-tmp"), Path(_os.getcwd())]:
                doc_dir = root_dir / "data" / "docs" / doc_id
                if doc_dir.exists():
                    for f in doc_dir.iterdir():
                        if f.is_file() and f.suffix.lower() in (".pdf", ".docx", ".txt"):
                            original_path = f
                            break
                if original_path:
                    break

    if not original_path or not original_path.exists():
        searched = "/root/autodl-tmp/data/{staging,pdfs,uploads}"
        raise HTTPException(404,
            f"找不到 {doc_id} 的原始文件\n"
            f"parsed.json filename='{original_filename}', is_hash={is_hash_id}\n"
            f"已搜索: {searched}\n"
            f"请重新上传文件。")

    # Step 2: Delete old data (chunks, vectors, KG)
    try:
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        if pipe:
            pipe.vector_index.remove_by_doc(doc_id)
            pipe.bm25_index.remove_by_doc(doc_id)
    except Exception as _e:
        log_suppressed(logger, _e)
    try:
        from hashmm.kg.storage import KGStorage
        ks = KGStorage()
        if ks.exists():
            kg, cm = ks.load()
            kg.remove_by_source(doc_id)
            ks.save(kg, cm)
    except Exception as _e:
        log_suppressed(logger, _e)
    # Remove old chunks from chunks.jsonl
    for root_dir in [Path(_os.getcwd()), Path("/root/autodl-tmp")]:
        chunks_file = root_dir / "data" / "chunks.jsonl"
        if chunks_file.exists():
            remaining = []
            with open(chunks_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("doc_id") != doc_id:
                            remaining.append(line)
                    except Exception:
                        remaining.append(line)
            with open(chunks_file, "w", encoding="utf-8") as f:
                f.writelines(remaining)
            break
    # Remove old doc directory
    for root_dir in [Path(_os.getcwd()), Path("/root/autodl-tmp")]:
        doc_dir = root_dir / "data" / "docs" / doc_id
        if doc_dir.exists():
            import shutil
            shutil.rmtree(doc_dir)
            break

    # Step 3: Re-ingest with the SAME doc_id
    try:
        import asyncio as _aio
        from hashmm.pipeline.ingest import IngestPipeline
        pipeline = IngestPipeline()
        await _aio.to_thread(pipeline.load_kg)   # V103.1: 重活丢线程，别卡事件循环（同 upload_and_parse）
        if app_state.llm_fn:
            pipeline.community_mgr.set_llm(app_state.llm_fn)
        # Pass doc_id to force reuse
        result = await _aio.to_thread(pipeline.ingest_file, original_path, extract_kg=True, doc_id=doc_id)

        # Step 4: Save source_path in metadata for future reparse
        for root_dir in [Path(_os.getcwd()), Path("/root/autodl-tmp")]:
            meta_path = root_dir / "data" / "docs" / doc_id / "meta.json"
            if meta_path.parent.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
                    meta["source_path"] = str(original_path.resolve())
                    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as _e:
                    log_suppressed(logger, _e)
                break

        # Step 5: Reload pipeline
        try:
            pipe = get_pipeline()
            if pipe:
                await _aio.to_thread(pipe.load)
                logger.info(f"Pipeline reloaded after reparse: {pipe.vector_index.total_vectors} vectors")
        except Exception as _e:
            log_suppressed(logger, _e)
        db.audit(admin["uid"], admin["sub"], "reparse", f"{doc_id}: {result.num_chunks} chunks")
        return {"ok": True, "doc_id": doc_id, "message": f"重新解析完成: {result.num_chunks} 切片"}
    except Exception as e:
        logger.error(f"Reparse failed for {doc_id}: {e}", exc_info=True)
        return {"ok": False, "error": str(e)[:200]}


@router.get("/docs/scan-files")
async def scan_source_files(request: Request):
    """Scan staging/pdfs directories for available source files.

    Returns files found on disk and whether they have a matching parsed doc.
    Useful for debugging reparse issues.
    """
    require_admin(request)
    import os as _os

    files_found = []
    existing_docs = set()

    # Get existing doc stems
    for root_dir in [Path("/root/autodl-tmp"), Path(_os.getcwd())]:
        docs_dir = root_dir / "data" / "docs"
        if docs_dir.exists():
            for d in docs_dir.iterdir():
                if d.is_dir():
                    existing_docs.add(d.name)  # e.g. "doc-小米集团-W：2025年度报告"
            break

    # Scan source directories
    for label, dir_path in [
        ("staging", Path("/root/autodl-tmp/data/staging")),
        ("pdfs", Path("/root/autodl-tmp/data/pdfs")),
        ("uploads", Path("/root/autodl-tmp/data/uploads")),
    ]:
        if not dir_path.exists():
            continue
        for f in sorted(dir_path.iterdir()):
            if not f.is_file():
                continue
            if f.suffix.lower() in (".pdf", ".docx", ".doc", ".txt", ".md", ".csv", ".xlsx"):
                expected_doc_id = f"doc-{f.stem}"
                files_found.append({
                    "filename": f.name,
                    "size_kb": round(f.stat().st_size / 1024),
                    "location": label,
                    "expected_doc_id": expected_doc_id,
                    "has_parsed": expected_doc_id in existing_docs,
                })

    return {"files": files_found, "total": len(files_found),
            "parsed_docs": len(existing_docs)}


@router.post("/docs/batch-reparse")
async def batch_reparse(request: Request):
    """Re-parse all documents that have source files available."""
    admin = require_admin(request)
    import os as _os

    # Get all doc IDs
    doc_ids = []
    for root_dir in [Path("/root/autodl-tmp"), Path(_os.getcwd())]:
        docs_dir = root_dir / "data" / "docs"
        if docs_dir.exists():
            for d in sorted(docs_dir.iterdir()):
                if d.is_dir() and (d / "parsed.json").exists():
                    doc_ids.append(d.name)
            break

    results = []
    for doc_id in doc_ids:
        try:
            stem = doc_id[4:] if doc_id.startswith("doc-") else doc_id
            is_hash_id = all(c in "0123456789abcdef" for c in stem[:16]) and len(stem) > 16
            original_path = None
            original_filename = ""

            # Read filename from parsed.json
            for root_dir in [Path("/root/autodl-tmp"), Path(_os.getcwd())]:
                pj = root_dir / "data" / "docs" / doc_id / "parsed.json"
                mj = root_dir / "data" / "docs" / doc_id / "meta.json"
                for jf in [mj, pj]:
                    if jf.exists():
                        try:
                            meta = json.loads(jf.read_text(encoding="utf-8"))
                            src = meta.get("source_path", "")
                            if src and Path(src).exists():
                                original_path = Path(src)
                                break
                            fn = meta.get("filename", "")
                            if fn:
                                original_filename = fn
                        except Exception as _e:
                            log_suppressed(logger, _e)
                if original_path:
                    break

            # Search directories
            if not original_path:
                candidates = set()
                if original_filename:
                    candidates.add(original_filename)
                if not is_hash_id:
                    for ext in [".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"]:
                        candidates.add(stem + ext)

                for dir_path in [
                    Path("/root/autodl-tmp/data/staging"),
                    Path("/root/autodl-tmp/data/pdfs"),
                    Path("/root/autodl-tmp/data/uploads"),
                ]:
                    if not dir_path.exists():
                        continue
                    for fn in candidates:
                        c = dir_path / fn
                        if c.exists():
                            original_path = c
                            break
                    if not original_path and (not is_hash_id or original_filename):
                        match_str = original_filename.rsplit(".", 1)[0] if original_filename else stem
                        for f in dir_path.iterdir():
                            if f.is_file() and f.suffix.lower() in (".pdf", ".docx", ".txt", ".md"):
                                if match_str and (match_str in f.stem or f.stem in match_str):
                                    original_path = f
                                    break
                    if original_path:
                        break

            if not original_path:
                results.append({"doc_id": doc_id, "ok": False,
                               "error": f"找不到源文件 (filename={original_filename})"})
                continue

            # Delete old data
            try:
                from hashmm.retriever_bridge import get_pipeline
                pipe = get_pipeline()
                if pipe:
                    pipe.vector_index.remove_by_doc(doc_id)
                    pipe.bm25_index.remove_by_doc(doc_id)
            except Exception as _e:
                log_suppressed(logger, _e)

            # Remove old chunks
            for root_dir in [Path("/root/autodl-tmp"), Path(_os.getcwd())]:
                chunks_file = root_dir / "data" / "chunks.jsonl"
                if chunks_file.exists():
                    remaining = []
                    with open(chunks_file, encoding="utf-8") as f:
                        for line in f:
                            try:
                                entry = json.loads(line.strip())
                                if entry.get("doc_id") != doc_id:
                                    remaining.append(line)
                            except Exception:
                                remaining.append(line)
                    with open(chunks_file, "w", encoding="utf-8") as f:
                        f.writelines(remaining)
                    break

            # Remove old doc dir
            import shutil
            for root_dir in [Path("/root/autodl-tmp"), Path(_os.getcwd())]:
                doc_dir = root_dir / "data" / "docs" / doc_id
                if doc_dir.exists():
                    shutil.rmtree(doc_dir)
                    break

            # Re-ingest
            import asyncio as _aio
            from hashmm.pipeline.ingest import IngestPipeline
            pipeline = IngestPipeline()
            await _aio.to_thread(pipeline.load_kg)
            if app_state.llm_fn:
                pipeline.community_mgr.set_llm(app_state.llm_fn)
            result = await _aio.to_thread(pipeline.ingest_file, original_path, extract_kg=True, doc_id=doc_id)

            # Save source_path
            for root_dir in [Path("/root/autodl-tmp"), Path(_os.getcwd())]:
                meta_path = root_dir / "data" / "docs" / doc_id / "meta.json"
                if meta_path.parent.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
                        meta["source_path"] = str(original_path.resolve())
                        meta["filename"] = original_path.name
                        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception as _e:
                        log_suppressed(logger, _e)
                    break

            results.append({"doc_id": doc_id, "ok": True, "chunks": result.num_chunks,
                           "file": original_path.name})
        except Exception as e:
            results.append({"doc_id": doc_id, "ok": False, "error": str(e)[:100]})

    # Reload pipeline once
    try:
        import asyncio as _aio
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        if pipe:
            await _aio.to_thread(pipe.load)
    except Exception as _e:
        log_suppressed(logger, _e)

    ok_count = sum(1 for r in results if r.get("ok"))
    db.audit(admin["uid"], admin["sub"], "batch_reparse", f"{ok_count}/{len(results)} docs")
    return {"ok": True, "results": results, "success": ok_count, "total": len(results)}


# ═══════════════════════════════════════════════════════════════
# v6.0: Prompt Templates API
# ═══════════════════════════════════════════════════════════════

_TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TEMPLATE_CATEGORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def _validated_template_id(template_id: str) -> str:
    value = str(template_id or "").strip()
    if not _TEMPLATE_ID_RE.fullmatch(value):
        raise HTTPException(400, "invalid template id")
    return value


async def _template_payload(request: Request) -> tuple[str, str, str, list[str]]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(400, "invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON body must be an object")
    name = str(body.get("name") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    category = str(body.get("category") or "general").strip().lower()
    variables_raw = body.get("variables", [])
    if not name or len(name) > 120:
        raise HTTPException(400, "name is required and must be at most 120 characters")
    if not prompt or len(prompt) > 100_000:
        raise HTTPException(400, "prompt is required and must be at most 100000 characters")
    if not _TEMPLATE_CATEGORY_RE.fullmatch(category):
        raise HTTPException(400, "invalid category")
    if not isinstance(variables_raw, list) or len(variables_raw) > 32:
        raise HTTPException(400, "variables must be a list with at most 32 items")
    variables: list[str] = []
    seen: set[str] = set()
    for raw in variables_raw:
        value = str(raw or "").strip()
        if not value:
            continue
        if len(value) > 64 or any(ord(ch) < 32 for ch in value):
            raise HTTPException(400, "invalid template variable")
        if value not in seen:
            variables.append(value)
            seen.add(value)
    return name, category, prompt, variables

@router.get("/templates")
async def list_templates(category: str = "", request: Request = None):
    """List prompt templates, optionally filtered by category."""
    require_admin(request)
    templates = db.list_templates(category=category if category else None)
    return {"templates": templates}


@router.post("/templates")
async def create_template(request: Request):
    """Create a new prompt template."""
    admin = require_admin(request)
    name, category, prompt, variables = await _template_payload(request)
    tid = db.create_template(name, category, prompt, variables, admin.get("sub", "admin"))
    db.audit(admin["uid"], admin.get("sub", "admin"), "create_template", tid)
    return {"ok": True, "id": tid, "template": next(
        (item for item in db.list_templates() if item.get("id") == tid), None
    )}


@router.patch("/templates/{template_id}")
async def update_template(template_id: str, request: Request):
    """Atomically update a template without deleting its identity or usage history."""
    admin = require_admin(request)
    template_id = _validated_template_id(template_id)
    name, category, prompt, variables = await _template_payload(request)
    updated = db.update_template(template_id, name, category, prompt, variables)
    if updated is None:
        raise HTTPException(404, "Template not found")
    db.audit(admin["uid"], admin.get("sub", "admin"), "update_template", template_id)
    return {"ok": True, "id": template_id, "template": updated}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, request: Request):
    """Delete a prompt template."""
    admin = require_admin(request)
    template_id = _validated_template_id(template_id)
    if not db.delete_template(template_id):
        raise HTTPException(404, "Template not found")
    db.audit(admin["uid"], admin.get("sub", "admin"), "delete_template", template_id)
    return {"ok": True, "id": template_id}


@router.post("/templates/{template_id}/use")
async def use_template(template_id: str, request: Request):
    """Increment use count for a template and return it."""
    require_admin(request)
    template_id = _validated_template_id(template_id)
    row = db.use_template(template_id)
    if not row:
        raise HTTPException(404, "Template not found")
    return row


# V308：本端点写好了却【丢了装饰器】，从未注册进路由 → 前端永远调不到。补回注册。
@router.get("/docs/{doc_id}/detail")
async def doc_detail(doc_id: str, request: Request):
    """Get full parsed document detail — sections, tables, images, quality."""
    require_admin(request)
    doc_id = _validated_doc_id(doc_id)
    import os as _os
    for root_dir in [Path(_os.getcwd()), Path("/root/autodl-tmp")]:
        doc_dir = root_dir / "data" / "docs" / doc_id
        if doc_dir.exists():
            result: dict = {}
            # parsed.json
            p = doc_dir / "parsed.json"
            if p.exists():
                result["info"] = json.loads(p.read_text(encoding="utf-8"))
            # quality_report.json
            p = doc_dir / "quality_report.json"
            if p.exists():
                result["quality"] = json.loads(p.read_text(encoding="utf-8"))
            # sections.json
            p = doc_dir / "sections.json"
            if p.exists():
                result["sections"] = json.loads(p.read_text(encoding="utf-8"))
            # Tables
            tables_dir = doc_dir / "tables"
            if tables_dir.exists():
                result["tables"] = []
                for tf in sorted(tables_dir.glob("*.md")):
                    result["tables"].append({
                        "name": tf.stem,
                        "content": tf.read_text(encoding="utf-8")[:2000],
                    })
            # Images
            images_dir = doc_dir / "images"
            if images_dir.exists():
                result["images"] = []
                for img in sorted(images_dir.glob("*.png")):
                    ctx_file = img.with_suffix(".context.txt")
                    ocr_file = img.parent / (img.stem + "_ocr.txt")
                    result["images"].append({
                        "name": img.name,
                        "context": ctx_file.read_text(encoding="utf-8")[:300] if ctx_file.exists() else "",
                        "ocr": ocr_file.read_text(encoding="utf-8")[:300] if ocr_file.exists() else "",
                    })
            # Full text preview (first 500 chars)
            p = doc_dir / "full_text.txt"
            if p.exists():
                result["text_preview"] = p.read_text(encoding="utf-8")[:500]

            return result
    raise HTTPException(404, f"Document {doc_id} not found")


@router.post("/docs/upload-and-parse")
async def upload_and_parse(request: Request):
    """Upload a file and immediately parse + index it."""
    import asyncio
    admin = require_admin(request)
    form = await request.form()
    file = form.get("file")
    if not file:
        raise HTTPException(400, "No file uploaded")

    # Save to staging without trusting filename path components or loading an
    # unbounded request body into RAM.
    staging = Path("data/staging")
    filepath, upload_bytes, upload_sha256 = await _stage_document_upload(file, staging)
    display_name = _safe_doc_upload_name(getattr(file, "filename", ""))

    # Run ingest
    try:
        from hashmm.pipeline.ingest import IngestPipeline
        pipeline = IngestPipeline()
        # V103.1 修复"解析时整个后端卡死"：解析/抽取/建索引是重 CPU/IO 同步操作，
        # 之前直接跑在 async 事件循环里 → 期间后端无法响应任何其它请求（用户管理/Chat 都不加载）。
        # 改为 asyncio.to_thread 丢到线程跑（解析库/embedding 多在原生层释放 GIL），
        # 事件循环空出来继续服务其它请求。与本文件 batch_index 已有写法一致。
        await asyncio.to_thread(pipeline.load_kg)

        # Set up LLM for community summaries (optional)
        if app_state.llm_fn:
            pipeline.community_mgr.set_llm(app_state.llm_fn)

        result = await asyncio.to_thread(pipeline.ingest_file, filepath, extract_kg=True)
        db.audit(admin["uid"], admin["sub"], "upload_and_parse", display_name)

        # v11: Save source_path in doc metadata for future reparse
        try:
            import os as _os
            for root_dir in [Path(_os.getcwd()), Path("/root/autodl-tmp")]:
                meta_path = root_dir / "data" / "docs" / result.doc_id / "meta.json"
                if meta_path.parent.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
                    meta["source_path"] = str(filepath.resolve())
                    meta["filename"] = display_name
                    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                    break
        except Exception as _e:
            log_suppressed(logger, _e)

        # v6.0: Reload global retrieval pipeline so new chunks are searchable immediately
        try:
            from hashmm.retriever_bridge import get_pipeline
            pipe = get_pipeline()
            if pipe:
                await asyncio.to_thread(pipe.load)  # Reload from disk（同样丢线程，别卡事件循环）
                logger.info(f"Global pipeline reloaded: {pipe.vector_index.total_vectors} vectors")
        except Exception as _e:
            logger.debug(f"Pipeline reload failed: {_e}")

        return {
            "ok": True,
            "result": result.to_dict(),
            "upload": {
                "filename": display_name,
                "bytes": upload_bytes,
                "sha256": upload_sha256,
            },
            "message": f"解析完成: {result.num_chunks} 切片, "
                       f"{result.num_entities} 实体, {result.num_relations} 关系",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _load_metadata_from_disk() -> list[dict]:
    """Fallback: load metadata directly from disk files."""
    import json as _json
    meta: list[dict] = []

    # Derive project root from common patterns
    import os as _os
    cwd = Path(_os.getcwd())
    # Also try the parent of the hashmm package
    pkg_root = Path(__file__).resolve().parent.parent.parent  # routes/admin.py -> api -> hashmm -> project_root

    search_roots = list(dict.fromkeys([cwd, pkg_root, Path("/root/autodl-tmp")]))

    for root in search_roots:
        # Try metadata.jsonl in indexes directory
        for sub in ["indexes/metadata.jsonl", "indexes/bge_m3/metadata.jsonl",
                     "data/metadata.jsonl"]:
            p = root / sub
            if p.exists():
                try:
                    with open(p) as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                meta.append(_json.loads(line))
                    if meta:
                        return meta
                except Exception as _e:
                    log_suppressed(logger, _e)

        # Try chunks.jsonl
        for sub in ["data/chunks.jsonl", "chunks.jsonl"]:
            p = root / sub
            if p.exists():
                try:
                    with open(p) as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                obj = _json.loads(line)
                                meta.append({
                                    "doc_id": obj.get("doc_id", obj.get("source", "unknown")),
                                    "modality": obj.get("modality", "text"),
                                })
                    if meta:
                        return meta
                except Exception as _e:
                    log_suppressed(logger, _e)

        # Try scanning data/parsed/ directory
        parsed_dir = root / "data" / "parsed"
        if parsed_dir.exists():
            for doc_dir in sorted(parsed_dir.iterdir()):
                if doc_dir.is_dir() and doc_dir.name.startswith("doc-"):
                    chunk_count = sum(1 for f in doc_dir.iterdir() if f.is_file())
                    modalities: set[str] = set()
                    for f in doc_dir.iterdir():
                        if f.suffix in (".txt", ".md"):
                            modalities.add("text")
                        elif f.suffix in (".png", ".jpg", ".jpeg"):
                            modalities.add("image")
                    for _ in range(max(chunk_count, 1)):
                        meta.append({
                            "doc_id": doc_dir.name,
                            "modality": list(modalities)[0] if modalities else "text",
                        })
            if meta:
                return meta

    return meta


@router.post("/docs/upload")
async def upload_doc(file: UploadFile = File(...), request: Request = None):
    if request:
        require_admin(request)
    content = await file.read()
    fname = file.filename or "unknown"
    staging_dir = Path("data/staging")
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_dir / fname
    dest.write_bytes(content)
    if request:
        user = require_admin(request)
        db.audit(user["uid"], user["sub"], "upload_doc", fname)
    return {
        "filename": fname, "size": len(content), "path": str(dest),
        "message": f"文件已保存到 {dest}。请运行以下命令进行索引：\n"
                   f"python -m hashmm.pipeline.ingest --input data/staging/{fname}",
    }


# ── Skills ──

@router.get("/skills")
async def list_skills(request: Request):
    require_admin(request)
    # Import _skills from server (populated by load_skills)
    from hashmm.api.intent_engine import _skills
    app_state.load_skills()
    return {"skills": [{"name": s["name"], "description": s.get("description", ""),
                        "triggers": s.get("triggers", []), "tools": s.get("tools", []),
                        "path": s.get("_path", "")} for s in _skills]}


@router.post("/skills")
async def create_skill(request: Request):
    admin = require_admin(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name required")
    skills_dir = Path("data/skills")
    skills_dir.mkdir(parents=True, exist_ok=True)
    fname = re.sub(r'[^\w]', '_', name) + ".json"
    skill_data = {
        "name": name, "description": body.get("description", ""),
        "triggers": body.get("triggers", []), "prompt": body.get("prompt", ""),
        "tools": body.get("tools", []),
    }
    (skills_dir / fname).write_text(
        json.dumps(skill_data, ensure_ascii=False, indent=2), encoding="utf-8")
    db.audit(admin["uid"], admin["sub"], "create_skill", name)
    app_state.load_skills()
    return {"ok": True, "filename": fname}


@router.delete("/skills/{skill_name}")
async def delete_skill(skill_name: str, request: Request):
    admin = require_admin(request)
    from hashmm.api.intent_engine import _skills
    for s in _skills:
        if s["name"] == skill_name:
            p = Path(s.get("_path", ""))
            if p.exists() and "data/skills" in str(p):
                p.unlink()
                db.audit(admin["uid"], admin["sub"], "delete_skill", skill_name)
                app_state.load_skills()
                return {"ok": True}
    raise HTTPException(404, "Skill not found")


# ── V91: 知识库/数据迁移（autodl ↔ 本地模式 一键搬家）──

@router.get("/kb/export")
async def kb_export(request: Request):
    """打包 data 白名单（语料/索引/KG/技能/库快照）为 zip 下载。"""
    admin = require_admin(request)
    from fastapi.responses import FileResponse
    from hashmm.api.kb_transfer import build_export_zip
    import time as _t
    out = Path("data") / f"_export-{_t.strftime('%Y%m%d-%H%M%S')}.zip"
    info = build_export_zip(Path("data"), out)
    db.audit(admin["uid"], admin["sub"], "kb_export", f"{info['size']}B {info['items']}")
    return FileResponse(str(out), filename=f"hashmm-kb-{_t.strftime('%Y%m%d')}.zip",
                        media_type="application/zip")


@router.post("/kb/import")
async def kb_import(request: Request, file: UploadFile = File(...)):
    """导入迁移包：白名单校验 + zip-slip 拦截 + 自动备份旧数据，失败回滚。"""
    admin = require_admin(request)
    from hashmm.api.kb_transfer import apply_import_zip
    import tempfile, shutil as _sh
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        _sh.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        result = apply_import_zip(Path("data"), tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "导入失败"))
    db.audit(admin["uid"], admin["sub"], "kb_import", ",".join(result.get("items", [])))
    return result


# ── Audit Logs ──

@router.get("/logs")
async def get_logs(request: Request, limit: int = 100, offset: int = 0):
    require_admin(request)
    return {"logs": db.get_audit_logs(limit, offset), "total": db.get_audit_count()}


# ═══════════════════════════════════════════════════════════════════
# v15 Phase 9: Enterprise data management
# ═══════════════════════════════════════════════════════════════════

@router.get("/audit/query", summary="按条件筛选审计日志", tags=["Admin"])
async def audit_query(request: Request, user_id: str = "", action: str = "",
                      username: str = "", start_ts: float = 0, end_ts: float = 0,
                      limit: int = 200, offset: int = 0):
    """Compliance-grade filtered audit search."""
    require_admin(request)
    return db.query_audit_logs(user_id=user_id, action=action, username=username,
                               start_ts=start_ts, end_ts=end_ts,
                               limit=limit, offset=offset)


@router.get("/audit/export", summary="导出审计日志为 CSV", tags=["Admin"])
async def audit_export(request: Request, user_id: str = "", action: str = "",
                       username: str = "", start_ts: float = 0, end_ts: float = 0):
    """Export filtered audit logs as CSV (compliance / archival)."""
    require_admin(request)
    import csv
    import io
    import datetime as _dt
    from fastapi.responses import StreamingResponse

    data = db.query_audit_logs(user_id=user_id, action=action, username=username,
                               start_ts=start_ts, end_ts=end_ts, limit=100000, offset=0)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["时间", "用户ID", "用户名", "动作", "详情", "IP"])
    for log in data["logs"]:
        ts = log.get("ts", 0)
        ts_str = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        writer.writerow([ts_str, log.get("user_id", ""), log.get("username", ""),
                         log.get("action", ""), log.get("detail", ""), log.get("ip", "")])
    buf.seek(0)
    fname = f"audit_logs_{int(__import__('time').time())}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.post("/index/rebuild", summary="重建检索索引（FAISS + BM25）", tags=["Admin"])
async def rebuild_index(request: Request):
    """Trigger a full retrieval-index rebuild as a background job."""
    admin = require_admin(request)
    from hashmm.api import jobs

    async def _worker(progress):
        progress(message="重建检索索引中…")
        import asyncio as _aio

        def _do_rebuild():
            # Reset the retrieval singleton so it reloads FAISS+BM25 fresh,
            # picking up all current chunks on next use.
            from hashmm.chat_retrieval import ChatRetrieval, get_chat_retrieval
            ChatRetrieval._instance = None
            cr = get_chat_retrieval()
            # Force pipeline load now (so errors surface here, not on first query)
            if hasattr(cr, "_ensure_pipeline"):
                cr._ensure_pipeline()
            elif hasattr(cr, "_pipeline") and cr._pipeline is None:
                from hashmm.retrieval_pipeline import RetrievalPipeline
                cr._pipeline = RetrievalPipeline()
            return "retrieval pipeline reloaded"

        result = await _aio.get_event_loop().run_in_executor(None, _do_rebuild)
        progress(message="索引重建完成")
        return {"reloaded": True, "detail": result}

    job_id = jobs.spawn("index_rebuild", _worker, owner_id=admin["uid"])
    db.audit(admin["uid"], admin["sub"], "index_rebuild", f"job={job_id}")
    return {"ok": True, "job_id": job_id}


@router.post("/docs/batch-index", summary="批量索引文档（后台任务+进度）", tags=["Admin"])
async def batch_index(request: Request):
    """Index multiple docs as a tracked background job. Body: {"doc_ids": [...]}.

    If doc_ids omitted, indexes all parsed docs that aren't indexed yet.
    """
    admin = require_admin(request)
    body = {}
    try:
        body = await request.json()
    except Exception as _e:
        log_suppressed(logger, _e)
    doc_ids = body.get("doc_ids") or []

    from hashmm.api import jobs

    async def _worker(progress):
        import asyncio as _aio
        from hashmm.pipeline.ingest import IngestPipeline

        # Resolve target files: explicit doc_ids → staging files, else all staging.
        staging = Path("data/staging")
        files = []
        if staging.exists():
            all_staged = list(staging.glob("*"))
            if doc_ids:
                for did in doc_ids:
                    for f in all_staged:
                        if did in f.stem or f.name == did:
                            files.append(f)
                            break
            else:
                files = [f for f in all_staged if f.is_file()]

        progress(total=len(files), done=0, message=f"待索引 {len(files)} 个文件")
        pipeline = IngestPipeline()
        if app_state.llm_fn:
            try:
                pipeline.set_kg_llm(app_state.llm_fn)
                pipeline.community_mgr.set_llm(app_state.llm_fn)
            except Exception as _e:
                log_suppressed(logger, _e)
        try:
            pipeline.load_kg()
        except Exception as _e:
            log_suppressed(logger, _e)

        ok, fail = 0, 0
        for i, f in enumerate(files, 1):
            try:
                await _aio.to_thread(pipeline.ingest_file, f, True)
                ok += 1
            except Exception as e:
                logger.warning(f"batch index failed for {f.name}: {e}")
                fail += 1
            progress(done=i, message=f"已处理 {i}/{len(files)}（成功 {ok}，失败 {fail}）")

        # Reload retrieval so new chunks are searchable
        try:
            from hashmm.chat_retrieval import ChatRetrieval
            ChatRetrieval._instance = None
        except Exception as _e:
            log_suppressed(logger, _e)

        # Rebuild the corpus vocabulary from all indexed chunks so the refusal
        # gate adapts to whatever was actually ingested (replaces the hard-coded
        # brand list). Best-effort: failure here must not fail the index job.
        try:
            from hashmm.retrieval_pipeline import RetrievalPipeline
            from hashmm.corpus_vocab import (
                build_corpus_vocab, save_corpus_vocab, get_default_vocab,
            )
            rp = RetrievalPipeline()
            rp.load()
            corpus = list(getattr(rp.bm25_index, "_corpus", []) or [])
            if corpus:
                vocab = build_corpus_vocab(corpus)
                if vocab:
                    save_corpus_vocab(vocab)
                    get_default_vocab().reload()
                    progress(done=len(files),
                             message=f"已处理 {len(files)}/{len(files)}；"
                                     f"语料词典 {len(vocab)} 词已更新")
        except Exception as e:
            logger.warning(f"corpus vocab rebuild skipped: {e}")

        return {"total": len(files), "ok": ok, "fail": fail}

    job_id = jobs.spawn("batch_index", _worker, total=len(doc_ids), owner_id=admin["uid"])
    db.audit(admin["uid"], admin["sub"], "batch_index", f"job={job_id}")
    return {"ok": True, "job_id": job_id}


@router.get("/jobs", summary="列出后台任务", tags=["Admin"])
async def list_jobs(request: Request):
    require_admin(request)
    from hashmm.api import jobs
    return {"jobs": jobs.list_jobs()}


@router.get("/jobs/{job_id}", summary="查询后台任务进度", tags=["Admin"])
async def get_job(job_id: str, request: Request):
    require_admin(request)
    from hashmm.api import jobs
    j = jobs.get_job(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    return j


@router.get("/usage/summary", summary="用量与成本统计", tags=["Admin"])
async def usage_summary(request: Request, days: int = 30):
    """Token usage + cost dashboard: totals, by-user, by-model, prices."""
    require_admin(request)
    from hashmm.api import usage
    return usage.summary(days=days)


@router.get("/usage/me", summary="当前用户用量", tags=["User"])
async def usage_me(request: Request, days: int = 30):
    # V260: 查自己的用量只需登录——此前误加 require_admin，导致普通用户看自己的
    # Token/成本被 403 挡下（用户实测"有的功能只有管理员能看"的一例）。
    user = require_auth(request)
    from hashmm.api import usage
    return usage.user_quota_used(user["uid"], days=days)


@router.get("/usage/overview", summary="当前可见范围的使用概览", tags=["User"])
async def usage_overview(request: Request, days: int = 30):
    """Stable cross-client usage view: team for admins, personal otherwise."""
    user = require_auth(request)
    from hashmm.api import usage
    return usage.overview_for(user, days=days)


@router.get("/audit/mine", summary="当前用户操作日志", tags=["User"])
async def audit_mine(request: Request, limit: int = 200):
    """V270 用户版控制台：普通用户查看**自己**的操作日志（登录即可）。
    复用 query_audit_logs 的 user_id 过滤——不新造查询、口径与管理员日志页一致；
    只回自己的行，越权面为零。"""
    user = require_auth(request)
    res = db.query_audit_logs(user_id=user["uid"], limit=max(1, min(limit, 500)))
    return res if isinstance(res, dict) else {"logs": res}


@router.get("/quality/dashboard", summary="线上质量大盘", tags=["Admin"])
async def quality_dashboard(request: Request, days: int = 7):
    """Real-traffic quality monitoring: groundedness, sources, weak-answer rate."""
    require_admin(request)
    from hashmm.api import quality_monitor
    return quality_monitor.dashboard(days=days)


@router.get("/all-conversations", summary="管理员：查看所有用户的会话", tags=["Admin"])
async def admin_all_conversations(request: Request, limit: int = 200):
    """Admin-only global view of every user's conversations (with owner). This is
    the dedicated management view — normal chat lists stay strictly per-user."""
    require_admin(request)
    rows = []
    try:
        with db._conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT c.id, c.title, c.user_id, c.created_at, c.updated_at, "
                "u.username FROM conversations c LEFT JOIN users u ON c.user_id=u.id "
                "ORDER BY c.updated_at DESC LIMIT ?", (limit,)).fetchall()]
    except Exception as _e:
        log_suppressed(logger, _e)
    # group summary by owner
    by_user = {}
    for r in rows:
        key = r.get("username") or r.get("user_id") or "(unknown)"
        by_user[key] = by_user.get(key, 0) + 1
    return {"total": len(rows), "by_user": by_user, "conversations": rows}


@router.get("/all-conversations/{conv_id}/messages", summary="管理员：查看任意会话的消息", tags=["Admin"])
async def admin_conversation_messages(conv_id: str, request: Request):
    """Admin-only: read any conversation's messages (for moderation/support)."""
    require_admin(request)
    msgs = []
    try:
        with db._conn() as c:
            msgs = [dict(r) for r in c.execute(
                "SELECT role, content, created_at FROM messages WHERE conv_id=? "
                "ORDER BY created_at LIMIT 500", (conv_id,)).fetchall()]
    except Exception as _e:
        log_suppressed(logger, _e)
    return {"conv_id": conv_id, "messages": msgs}


@router.get("/scheduled", summary="列出定时任务（主动服务）", tags=["Admin"])
async def list_scheduled(request: Request):
    require_admin(request)
    from hashmm import scheduler
    return {"enabled": scheduler.scheduler_enabled(),
            "actions": scheduler.list_actions(),
            "tasks": scheduler.list_tasks()}


@router.post("/scheduled", summary="创建定时任务（主动服务）", tags=["Admin"])
async def create_scheduled(request: Request):
    """Body: {"action":"corpus_digest","name":"每日摘要","schedule_kind":"daily",
    "daily_at":"09:00"} or {"action":"kg_health","schedule_kind":"interval","interval_seconds":3600}."""
    admin = require_admin(request)
    body = await request.json()
    action = (body.get("action") or "").strip()
    if not action:
        raise HTTPException(400, "缺少 action")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(400, "params 必须是对象")
    params = dict(params)
    conv_id = str(params.get("conv_id") or "").strip()
    if conv_id:
        # 定时结果会写回 Chat，因此创建时必须验证目标会话，并绑定当时的真实 owner。
        # 执行时还会复核 owner，避免会话删除/ID 复用后把结果写给另一个用户。
        conv = require_conv_access(request, conv_id)
        params["conv_id"] = conv_id
        params["conv_owner_uid"] = str(conv.get("user_id") or "")
    from hashmm import scheduler
    try:
        t = scheduler.create_task(
            action=action, name=body.get("name", ""), params=params,
            schedule_kind=body.get("schedule_kind", "interval"),
            interval_seconds=int(body.get("interval_seconds", 3600)),
            daily_at=body.get("daily_at", ""),
            tenant_id=body.get("tenant_id", "default"), created_by=admin["sub"],
            allow_unattended=bool(body.get("allow_unattended", False)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.audit(admin["uid"], admin["sub"], "create_scheduled", f"{action}")
    return {"ok": True, "task": t}


@router.post("/scheduled/{task_id}/run", summary="立即运行定时任务", tags=["Admin"])
async def run_scheduled_now(task_id: str, request: Request):
    admin = require_admin(request)
    import asyncio
    from hashmm import scheduler
    res = await asyncio.to_thread(scheduler.run_task_now, task_id)
    return res


@router.post("/scheduled/{task_id}/toggle", summary="启用/禁用定时任务", tags=["Admin"])
async def toggle_scheduled(task_id: str, request: Request):
    require_admin(request)
    body = await request.json()
    from hashmm import scheduler
    ok = scheduler.set_enabled(task_id, bool(body.get("enabled", True)))
    return {"ok": ok}


@router.delete("/scheduled/{task_id}", summary="删除定时任务", tags=["Admin"])
async def delete_scheduled(task_id: str, request: Request):
    admin = require_admin(request)
    from hashmm import scheduler
    ok = scheduler.delete_task(task_id)
    db.audit(admin["uid"], admin["sub"], "delete_scheduled", task_id)
    return {"ok": ok}


@router.get("/llm-routing", summary="读取任务→后端路由配置（角色→模型）", tags=["Admin"])
async def get_llm_routing(request: Request):
    require_admin(request)
    from hashmm import llm_router
    return llm_router.get_task_routing_config()


@router.put("/llm-routing", summary="保存任务→后端路由配置（角色→模型）", tags=["Admin"])
async def put_llm_routing(request: Request):
    admin = require_admin(request)
    body = await request.json()
    from hashmm import llm_router
    cfg = llm_router.save_task_routing(body.get("routing", {}) or {})
    db.audit(admin["uid"], admin["sub"], "set_llm_routing", str(body.get("routing", {}))[:200])
    return cfg


@router.post("/llm-routing/test", summary="实测某角色当前走哪个后端（真发一句话）", tags=["Admin"])
async def test_llm_routing(request: Request):
    """Body: {task}. 把一句话通过该任务的真实路由发出去，返回实际后端 + 延迟 + 样例回复。"""
    require_admin(request)
    body = await request.json()
    task = (body.get("task") or "").strip()
    if not task:
        raise HTTPException(400, "缺少 task")
    import asyncio as _aio
    import time as _t
    from hashmm import llm_router
    from hashmm.api.core.services import ServiceRegistry
    try:
        ServiceRegistry.ensure_loaded()
        cloud_fn = ServiceRegistry.llm_fn
        fn, backend = llm_router.route_llm(task, cloud_fn, user_id=None)
        if fn is None:
            return {"ok": False, "task": task, "backend": backend, "message": "该路由下无可用模型（请先在管理后台配置模型）"}
        t0 = _t.time()
        resp = await _aio.to_thread(fn, "请只用一句话回复：ok")
        latency = int((_t.time() - t0) * 1000)
        return {"ok": True, "task": task, "backend": backend, "latency_ms": latency,
                "sample": (resp or "").strip()[:80]}
    except Exception as e:
        return {"ok": False, "task": task, "message": f"{type(e).__name__}: {str(e)[:120]}"}


@router.get("/runs", summary="Agent 运行轨迹（run_record 遥测）", tags=["Admin"])
async def list_runs(request: Request, limit: int = 50):
    """读取最近的 run_record JSONL（工具序列/状态/耗时/停止理由/用量）。
    默认关（HASHMM_AGENT_TRACE=1 才落盘）；关时 runs 为空 + enabled=false。"""
    require_admin(request)
    import os as _os
    import json as _json
    from pathlib import Path as _Path
    enabled = _os.environ.get("HASHMM_AGENT_TRACE", "1") != "0"
    d = _Path(_os.environ.get("HASHMM_TRACE_DIR", "logs/agent_runs"))
    limit = max(1, min(int(limit or 50), 200))
    runs: list = []
    try:
        if d.exists():
            for fp in sorted(d.glob("*.jsonl"), reverse=True):   # 最新日期文件优先
                try:
                    lines = fp.read_text(encoding="utf-8").splitlines()
                except Exception:
                    continue
                for ln in reversed(lines):                       # 文件内最新在后
                    if not ln.strip():
                        continue
                    try:
                        runs.append(_json.loads(ln))
                    except Exception:
                        continue
                    if len(runs) >= limit:
                        break
                if len(runs) >= limit:
                    break
    except Exception:
        pass
    return {"enabled": enabled, "dir": str(d), "runs": runs[:limit]}


@router.get("/discovery", summary="自发现：主动扫描系统、找出该做的活", tags=["Admin"])
async def get_discovery(request: Request):
    """按需跑一次自发现扫描（只读）：从 KG 健康/检索质量/语料/定时任务等信号里找出值得主动做的事。"""
    require_admin(request)
    try:
        from hashmm.agent import discovery
        return discovery.run_discovery()
    except Exception:
        return {"generated_at": "", "findings": []}


@router.get("/tenants", summary="列出租户（多租户）", tags=["Admin"])
async def list_tenants(request: Request):
    require_admin(request)
    from hashmm import tenancy as _ten
    return _ten.tenant_overview(db)


@router.post("/tenants", summary="创建租户（多租户）", tags=["Admin"])
async def create_tenant_ep(request: Request):
    """Body: {"id": "acme", "name": "Acme", "monthly_token_quota": 1000000, ...}."""
    admin = require_admin(request)
    body = await request.json()
    tid = (body.get("id") or "").strip()
    if not tid:
        raise HTTPException(400, "缺少租户 id")
    from hashmm import tenancy as _ten
    quotas = {k: body[k] for k in ("max_users", "max_storage_mb", "monthly_token_quota") if k in body}
    t = _ten.create_tenant(db, tid, body.get("name", ""), **quotas)
    db.audit(admin["uid"], admin["sub"], "create_tenant", f"id={tid}")
    return {"ok": True, "tenant": t}


@router.post("/tenants/assign", summary="分配用户到租户（多租户）", tags=["Admin"])
async def assign_tenant_ep(request: Request):
    """Body: {"user_id": "...", "tenant_id": "acme"}."""
    admin = require_admin(request)
    body = await request.json()
    uid = body.get("user_id", "")
    tid = body.get("tenant_id", "")
    if not uid or not tid:
        raise HTTPException(400, "缺少 user_id 或 tenant_id")
    from hashmm import tenancy as _ten
    ok = _ten.assign_user(db, uid, tid)
    db.audit(admin["uid"], admin["sub"], "assign_tenant", f"user={uid} → {tid}")
    return {"ok": ok}


@router.get("/observability", summary="可观测总览（实时 SLO / 成本 / 检索质量）", tags=["Admin"])
async def observability_overview(request: Request):
    """Unified ops view combining two complementary signals:

      - **performance** (real-time, in-process): latency p50/p95/p99, per-query
        cost + total spend, retrieval latency, retrieval top-score, insufficient
        fallback rate, and SLO breach flags — from the observability ring buffer.
      - **http** (process-level): request/error counts, status distribution.

    Everything here is best-effort and never raises; missing signals come back as
    empty so a partial outage in telemetry never breaks the dashboard.
    """
    require_admin(request)
    out: dict = {"performance": {}, "http": {}, "tools": {}, "evolution": {}}
    try:
        from hashmm import observability as _obs
        out["performance"] = _obs.slo_report()
        out["tools"] = _obs.tool_stats()
        out["llm_routing"] = _obs.llm_routing_stats()
        out["scheduled"] = _obs.scheduled_stats()
    except Exception as _e:
        log_suppressed(logger, _e)
    try:
        from hashmm.evolution.skill_evolver import get_skill_evolver
        out["evolution"] = get_skill_evolver().evolution_overview()
    except Exception as _e:
        log_suppressed(logger, _e)
    try:
        from hashmm import tenancy as _ten
        out["tenancy"] = _ten.tenant_overview(db)
    except Exception as _e:
        log_suppressed(logger, _e)
    try:
        from hashmm.api.middleware import get_metrics_snapshot
        snap = get_metrics_snapshot()
        # Avoid duplicating the genai block (already in performance).
        out["http"] = {k: v for k, v in snap.items() if k != "genai"}
    except Exception as _e:
        log_suppressed(logger, _e)
    return out


@router.get("/settings", summary="列出运行时设置（搜索Key等）", tags=["Admin"])
async def list_settings(request: Request):
    require_admin(request)
    from hashmm.api import settings_store
    return {"settings": settings_store.list_settings()}


@router.get("/users/{user_id}/tasks", summary="查看用户的跨会话任务记忆", tags=["Admin"])
async def user_tasks(request: Request, user_id: str):
    require_admin(request)
    from hashmm.evolution.task_memory import get_task_memory
    return {"tasks": get_task_memory().list_tasks(user_id)}


@router.post("/settings", summary="更新运行时设置", tags=["Admin"])
async def update_setting(request: Request):
    """Body: {"key": "serper_api_key", "value": "..."}."""
    admin = require_admin(request)
    body = await request.json()
    key = body.get("key", "")
    value = body.get("value", "")
    from hashmm.api import settings_store
    if key not in settings_store._KNOWN:
        raise HTTPException(400, f"未知设置项: {key}")
    settings_store.set_setting(key, value)
    db.audit(admin["uid"], admin["sub"], "update_setting", f"key={key}")
    return {"ok": True, "key": key}


@router.post("/settings/test-search", summary="测试联网搜索是否可用", tags=["Admin"])
async def test_search(request: Request):
    """Run a quick web search to verify the configured backend works.
    Body: {"query": "测试查询"} (optional)."""
    require_admin(request)
    body = {}
    try:
        body = await request.json()
    except Exception as _e:
        log_suppressed(logger, _e)
    query = body.get("query") or "苹果公司 2024 营收"
    from hashmm.api.tool_registry import execute_tool
    import asyncio
    result = await asyncio.to_thread(execute_tool, "web_search", {"query": query, "num_results": 3}, {})
    ok = bool(result) and "暂不可用" not in result and "未找到" not in result and "未配置" not in result
    return {"ok": ok, "query": query, "result": result[:1500]}


# ── D1: Async Document Indexing ──

_ingest_status: dict[str, dict] = {}  # doc_id → progress

@router.post("/docs/{doc_id}/index")
async def index_document(doc_id: str, request: Request):
    """Trigger async document indexing with KG extraction."""
    import asyncio
    admin = require_admin(request)

    # Find the uploaded file
    staging = Path("data/staging")
    uploaded = list(staging.glob("*"))
    target = None
    for f in uploaded:
        if doc_id in f.stem or f.name == doc_id:
            target = f
            break

    if not target:
        raise HTTPException(404, f"File not found: {doc_id}")

    _ingest_status[doc_id] = {"stage": "queued", "progress": 0, "message": "排队中..."}
    _act_uid = admin.get("uid")           # 实时任务活动归属用户
    _act_last = {"p": -1.0, "stage": ""}   # 节流：阶段变化或进度变化≥5% 才推

    async def _run_ingest():
        try:
            from hashmm.pipeline.ingest import IngestPipeline
            pipeline = IngestPipeline()
            if app_state.llm_fn:
                pipeline.extractor.set_llm(app_state.llm_fn)
                pipeline.community_mgr.set_llm(app_state.llm_fn)
            pipeline.load_kg()

            def on_progress(p):
                _ingest_status[doc_id] = {
                    "stage": p.stage, "progress": round(p.progress, 2),
                    "message": p.message, "entities": p.entities_found,
                    "chunks": p.chunks_processed,
                }
                try:  # 实时任务进度 → Supabase（节流：阶段变化或进度≥5% 才推）
                    from hashmm.api import supabase_sync
                    if _act_uid and supabase_sync.enabled():
                        if p.stage != _act_last["stage"] or abs(p.progress - _act_last["p"]) >= 0.05:
                            _act_last["stage"] = p.stage
                            _act_last["p"] = p.progress
                            supabase_sync.push_activity({
                                "id": doc_id, "user_id": _act_uid, "kind": "job",
                                "title": p.message or "解析文档", "status": p.stage,
                                "done": int(round(p.progress * 100)), "total": 100,
                            })
                except Exception:
                    pass

            result = pipeline.ingest_file(target, extract_kg=True, on_progress=on_progress)
            _ingest_status[doc_id] = {
                "stage": "done", "progress": 1.0,
                "message": f"完成: {result.num_chunks} 切片, {result.num_entities} 实体",
                "result": result.to_dict(),
            }
            try:
                from hashmm.api import supabase_sync
                if _act_uid and supabase_sync.enabled():
                    supabase_sync.remove_activity(doc_id, _act_uid)
            except Exception:
                pass
        except Exception as e:
            _ingest_status[doc_id] = {
                "stage": "error", "progress": 0, "message": str(e),
            }
            try:
                from hashmm.api import supabase_sync
                if _act_uid and supabase_sync.enabled():
                    supabase_sync.remove_activity(doc_id, _act_uid)
            except Exception:
                pass

    asyncio.get_event_loop().create_task(asyncio.to_thread(_run_ingest))
    db.audit(admin["uid"], admin["sub"], "index_doc", doc_id)
    return {"ok": True, "doc_id": doc_id, "message": "索引任务已启动"}


@router.get("/docs/{doc_id}/status")
async def index_status(doc_id: str, request: Request):
    """Get indexing progress for a document."""
    require_admin(request)
    status = _ingest_status.get(doc_id)
    if not status:
        return {"stage": "unknown", "message": "无索引记录"}
    return status


# ── D6: RAG Config Center ──

@router.get("/rag-config")
async def get_rag_config_api(request: Request):
    """Get current RAG configuration."""
    require_admin(request)
    from hashmm.rag_config import get_rag_config
    return get_rag_config().to_dict()


@router.put("/rag-config")
async def update_rag_config_api(request: Request):
    """Update RAG configuration."""
    admin = require_admin(request)
    body = await request.json()
    from hashmm.rag_config import get_rag_config
    config = get_rag_config()
    config.update(**body)
    db.audit(admin["uid"], admin["sub"], "update_rag_config", json.dumps(body)[:200])
    return {"ok": True, "config": config.to_dict()}


# ═══════════════════════════════════════════════════════════════════
# v9.0: Quality Evaluation + Retrieval Analytics
# ═══════════════════════════════════════════════════════════════════

@router.get("/eval/cases",
            summary="列出所有 Golden Test 用例",
            tags=["Evaluation"])
async def list_eval_cases(request: Request):
    require_admin(request)
    from hashmm.evaluation import RAGEvaluator
    evaluator = RAGEvaluator()
    return {"cases": [evaluator._case_to_dict(c) for c in evaluator.cases]}


@router.post("/eval/cases",
             summary="添加/更新 Golden Test 用例",
             tags=["Evaluation"])
async def add_eval_case(request: Request):
    admin = require_admin(request)
    body = await request.json()
    from hashmm.evaluation import RAGEvaluator
    evaluator = RAGEvaluator()
    case = evaluator.add_case(body)
    return {"ok": True, "case_id": case.id}


@router.delete("/eval/cases/{case_id}",
               summary="删除 Golden Test 用例",
               tags=["Evaluation"])
async def delete_eval_case(case_id: str, request: Request):
    require_admin(request)
    from hashmm.evaluation import RAGEvaluator
    evaluator = RAGEvaluator()
    removed = evaluator.remove_case(case_id)
    return {"ok": removed}


@router.get("/eval/feedback-candidates",
            summary="列出真实流量反馈产生的待复核用例",
            tags=["Evaluation"])
async def list_feedback_candidates(request: Request, status: str = "pending",
                                   limit: int = 100):
    """V349 EDD flywheel: production failures awaiting human curation."""
    require_admin(request)
    allowed = {"", "pending", "reviewing", "approved", "dismissed", "positive", "withdrawn"}
    if status not in allowed:
        raise HTTPException(400, "不支持的反馈状态")
    cases = db.list_feedback_cases(status=status, limit=limit)
    return {
        "schema": "hashmm.feedback-review.v1",
        "cases": cases,
        "count": len(cases),
        "reason_options": db.FEEDBACK_FAILURE_REASONS,
        "policy": "负反馈只生成候选；人工填写可信参考答案后才进入 held-out 回归集。",
    }


@router.post("/eval/feedback-candidates/{candidate_id}",
             summary="复核真实流量反馈并决定是否进入回归集",
             tags=["Evaluation"])
async def review_feedback_candidate(candidate_id: str, request: Request):
    admin = require_admin(request)
    body = await request.json()
    decision = str(body.get("decision") or "").lower()
    if decision not in ("approve", "dismiss"):
        raise HTTPException(400, "decision 仅支持 approve 或 dismiss")
    candidate = db.get_feedback_case(candidate_id)
    if not candidate or candidate.get("status") != "pending":
        raise HTTPException(404, "待复核用例不存在")
    reference = str(body.get("reference_answer") or "").strip()
    eval_case_id = ""
    claimed = db.claim_feedback_case(case_id=candidate_id, reviewer=admin["uid"])
    if not claimed:
        raise HTTPException(409, "该用例已被其他管理员处理")
    if decision == "approve":
        if len(reference) < 5:
            db.release_feedback_case_claim(case_id=candidate_id, reviewer=admin["uid"])
            raise HTTPException(400, "加入回归集前必须填写可信参考答案")
        try:
            from hashmm.evaluation.feedback_loop import build_reviewed_eval_case
            eval_case = build_reviewed_eval_case(candidate, reference)
            eval_case_id = eval_case["id"]
            from hashmm.evaluation import RAGEvaluator
            RAGEvaluator().add_case(eval_case)
        except Exception:
            db.release_feedback_case_claim(case_id=candidate_id, reviewer=admin["uid"])
            raise
    reviewed = db.review_feedback_case(
        case_id=candidate_id, decision=decision, reviewer=admin["uid"],
        reference_answer=reference, eval_case_id=eval_case_id,
        expected_status="reviewing",
    )
    if not reviewed:
        if eval_case_id:
            from hashmm.evaluation import RAGEvaluator
            RAGEvaluator().remove_case(eval_case_id)
        db.release_feedback_case_claim(case_id=candidate_id, reviewer=admin["uid"])
        raise HTTPException(409, "该用例已被其他管理员处理")
    db.audit(admin["uid"], admin["sub"], "eval_feedback_review",
             f"{decision}:{candidate_id}:{eval_case_id}")
    return {"ok": True, "feedback_case": reviewed, "eval_case_id": eval_case_id}


@router.post("/eval/redteam",
             summary="运行注入防御红队评测",
             description="用红队用例验证间接提示注入防御（不可信区隔离 + 外泄闸）是否有效",
             tags=["Evaluation"])
async def run_redteam_endpoint(request: Request):
    """V300 第五期：注入防御红队。验证第二期加的注入防线是否真的有效——有防御没验证等于没防御。"""
    admin = require_admin(request)
    from hashmm.evaluation.injection_redteam import run_redteam
    report = run_redteam()
    db.audit(admin["uid"], admin["sub"], "eval_redteam", report.get("verdict", ""))
    return report


@router.post("/eval/ir",
             summary="运行检索 IR 评测（Recall@k / nDCG / MRR）",
             description="对 query→相关 doc_id 金标准跑经典 IR 指标，衡量检索器排序质量（独立于答案生成）",
             tags=["Evaluation"])
async def run_ir_eval_endpoint(request: Request):
    """V211 差距一：检索侧 IR 评测。用 ir_cases.json 的金标准跑 Recall@k/nDCG/MRR。

    Body 可选 {"ks": [1,3,5,10]}。返回 {n, summary, per_case}。
    """
    admin = require_admin(request)
    body = {}
    try:
        body = await request.json()
    except Exception as _e:
        log_suppressed(logger, _e)
    ks = tuple(int(x) for x in (body.get("ks") or [1, 3, 5, 10]))

    from hashmm.evaluation.ir_eval import load_ir_cases, run_ir_eval
    from hashmm.chat_retrieval import get_chat_retrieval
    chat_rag = get_chat_retrieval()

    def _retrieve(query: str, k: int = 10):
        msgs = [{"role": "user", "content": query}]
        try:
            _enh, sources, _strat = chat_rag.enhance(query, msgs, retrieval_mode="mix")
            return sources or []
        except Exception as _e:
            log_suppressed(logger, _e)
            return []

    cases = load_ir_cases()
    import asyncio
    report = await asyncio.to_thread(run_ir_eval, cases, _retrieve, ks)
    db.audit(admin["uid"], admin["sub"], "eval_ir", f"n={report.get('n')}")
    return report


@router.post("/eval/run",
             summary="运行全部 Golden Test",
             description="对所有测试用例执行检索+生成，返回质量报告",
             tags=["Evaluation"])
async def run_eval(request: Request):
    admin = require_admin(request)
    from hashmm.evaluation import RAGEvaluator
    from hashmm.chat_retrieval import get_chat_retrieval

    evaluator = RAGEvaluator()
    chat_rag = get_chat_retrieval()

    def chat_fn(query: str, mode: str):
        """Execute a query and return (answer, sources)."""
        from hashmm.api.core.services import ServiceRegistry
        ServiceRegistry.ensure_loaded()
        llm_fn = ServiceRegistry.llm_fn

        # Run retrieval
        msgs = [{"role": "user", "content": query}]
        enhanced, sources, strategy = chat_rag.enhance(query, msgs, retrieval_mode=mode)

        # Generate answer
        if llm_fn and hasattr(llm_fn, 'chat'):
            answer = llm_fn.chat(enhanced)
        elif llm_fn:
            flat = "\n\n".join(m["content"] for m in enhanced)
            answer = llm_fn(flat)
        else:
            answer = "LLM not configured"

        return answer, sources

    import asyncio
    report = await asyncio.to_thread(evaluator.run_all, chat_fn)
    db.audit(admin["uid"], admin["sub"], "eval_run", report.get("summary", ""))
    return report


@router.post("/eval/run-enhanced",
             summary="运行增强评测（检索指标 + LLM 评审 + 可保存对比）",
             tags=["Evaluation"])
async def run_eval_enhanced(request: Request):
    """v15 Phase 8: full eval — keyword + Recall@k/MRR/NDCG + LLM-as-judge.

    Body (optional): {"use_judge": true, "tag": "before-prompt-change"}
    """
    admin = require_admin(request)
    body = {}
    try:
        body = await request.json()
    except Exception as _e:
        log_suppressed(logger, _e)
    use_judge = body.get("use_judge", True)
    tag = body.get("tag", "")
    judge_model_id = (body.get("judge_model") or "").strip()
    comprehensive = bool(body.get("comprehensive", False))
    case_set = (body.get("case_set") or "").strip().lower()

    from hashmm.evaluation import RAGEvaluator
    from hashmm.chat_retrieval import get_chat_retrieval
    from hashmm.api.core.services import ServiceRegistry

    evaluator = RAGEvaluator()
    # 全面体检：合并所有内置用例集（一次跑全、一次定位所有问题），适配甲方"别反复测"的诉求。
    if comprehensive:
        try:
            from hashmm.evaluation import load_all_bundled_cases
            allc = load_all_bundled_cases()
            if allc:
                evaluator._cases = allc
        except Exception as _ce:
            log_suppressed(logger, _ce)
    elif case_set in ("agentic", "hard", "difficult", "困难"):
        # 只跑「困难·智能体级」用例集（v1+v2）——比全面体检快，定向测长思考/长任务/解决问题能力
        try:
            from hashmm.evaluation import load_agentic_cases
            ac = load_agentic_cases()
            if ac:
                evaluator._cases = ac
        except Exception as _ce:
            log_suppressed(logger, _ce)
    chat_rag = get_chat_retrieval()
    ServiceRegistry.ensure_loaded()
    llm_fn = ServiceRegistry.llm_fn

    def chat_fn(query: str, mode: str):
        # v17 Phase 28: prompt-injection / system-prompt extraction → clean security
        # refusal (not a vague "no info" deflection), before retrieval/generation.
        from hashmm.prompt_safety import detect_prompt_injection, security_refusal_message
        if detect_prompt_injection(query):
            return security_refusal_message(), []
        msgs = [{"role": "user", "content": query}]
        enhanced, sources, strat = chat_rag.enhance(query, msgs, retrieval_mode=mode)
        if llm_fn and hasattr(llm_fn, "chat"):
            answer = llm_fn.chat(enhanced)
        elif llm_fn:
            answer = llm_fn("\n\n".join(m["content"] for m in enhanced))
        else:
            answer = "LLM not configured"
        # v17 Phase 25 (B1): never feed an empty answer into eval (mirrors the live
        # guard) — empties on overclaim/over-vague prompts become a safe message.
        from hashmm.answer_guard import guard_empty_answer
        answer = guard_empty_answer(answer, strategy=getattr(strat, "mode", None))
        from hashmm.rag_security import guard_output_pii
        answer = guard_output_pii(answer)
        return answer, sources

    judge = llm_fn if use_judge else None
    # 评审模型：若指定了 judge_model（如你新建的 Claude 模型），用它当 LLM 评审；否则用默认模型。
    if use_judge and judge_model_id:
        try:
            from hashmm.api import model_manager
            mcfg = db.get_model(judge_model_id)
            if mcfg:
                jf = model_manager.make_llm_fn_from_model(mcfg)
                if jf:
                    judge = jf
                    logger.info(f"[eval] 使用指定评审模型：{mcfg.get('name') or mcfg.get('model_name')}")
        except Exception as _e:
            log_suppressed(logger, _e)
    import asyncio
    report = await asyncio.to_thread(evaluator.run_all_enhanced, chat_fn, judge, tag)
    try:
        if isinstance(report.get("summary"), dict):
            mc = (db.get_model(judge_model_id) or {}) if judge_model_id else {}
            report["summary"]["judge_model"] = mc.get("name") or mc.get("model_name") or ("默认模型" if use_judge else "未启用")
            report["summary"]["comprehensive"] = comprehensive
            report["summary"]["n_cases_run"] = len(evaluator._cases)
    except Exception as _e:
        log_suppressed(logger, _e)
    db.audit(admin["uid"], admin["sub"], "eval_run_enhanced",
             f"avg_score={report.get('summary', {}).get('avg_score')}")
    return report


@router.get("/eval/runs", summary="列出已保存的评测运行", tags=["Evaluation"])
async def list_eval_runs(request: Request):
    require_admin(request)
    from hashmm.evaluation import metrics as M
    return {"runs": M.list_runs(limit=30)}


def _pct(v) -> str:
    try:
        return "—" if v is None else f"{round(float(v) * 100)}%"
    except Exception:
        return "—"


def _eval_report_markdown(run: dict, run_id: str) -> str:
    """把一次评测（含每条用例明细）渲染成一份可分析的详细报告。
    重点是「哪一层在掉分 + 该改什么」，方便对比 DeepSeek 与 Claude、定位客户端问题。"""
    s = run.get("summary", {}) or {}
    results = run.get("results", []) or []
    L = s.get("layered", {}) or {}
    comp = s.get("component_metrics", {}) or {}
    retr = s.get("retrieval", {}) or {}
    out: list[str] = []
    out.append(f"# HashMM 质量评测 · 详细分析报告")
    out.append("")
    out.append(f"- 运行 ID：`{run_id}`　标签：{run.get('tag', '—')}")
    out.append(f"- 评审模型：{s.get('judge_model', '—')}　模式：{'全面体检' if s.get('comprehensive') else '标准'}")
    out.append(f"- 用例数：{s.get('n_cases_run', s.get('total', '—'))}　总耗时：{run.get('elapsed_ms', '—')}ms")
    out.append("")
    out.append("## 一、总体结果")
    out.append(f"- 通过率：**{_pct(s.get('pass_rate'))}**（{s.get('passed', '—')}/{s.get('total', '—')}）")
    out.append(f"- 平均分：{s.get('avg_score', '—')}　评审分：{s.get('avg_judge_score', '—')}")
    if L:
        rt = L.get("retrieval", {}); gn = L.get("generation", {}); e2e = L.get("end_to_end", {})
        out.append("")
        out.append("## 二、分层定位（关键：看是哪一层在掉分）")
        out.append(f"- **检索层**（找得准不准）：{_pct(rt.get('score'))}　覆盖 {rt.get('n_cases', 0)} 条")
        out.append(f"- **生成层**（答得好不好）：{_pct(gn.get('score'))}　覆盖 {gn.get('n_cases', 0)} 条")
        out.append(f"- **端到端**（整体通过）：{_pct(e2e.get('score'))}　覆盖 {e2e.get('n_cases', 0)} 条")
    if comp:
        out.append("")
        out.append("## 三、生成质量细分（RAGAS 口径，定位幻觉/跑题）")
        names = {"faithfulness": "忠实度(不编造)", "answer_relevancy": "答案相关性(不跑题)",
                 "context_precision": "上下文精度(检索噪音)", "context_recall": "上下文召回(漏检)"}
        for k, label in names.items():
            if comp.get(k) is not None:
                out.append(f"- {label}：{_pct(comp.get(k))}")

    # 四、可执行的改进建议（按指标自动推断 —— 这就是你要拿来改客户端的依据）
    insights: list[str] = []
    rt_score = (L.get("retrieval", {}) or {}).get("score")
    gn_score = (L.get("generation", {}) or {}).get("score")
    faith = comp.get("faithfulness"); arel = comp.get("answer_relevancy")
    cprec = comp.get("context_precision"); crec = comp.get("context_recall")
    overfit = (s.get("split_breakdown", {}) or {}).get("overfit_gap")
    if rt_score is not None and rt_score < 0.6:
        insights.append("检索层偏低（找不准）：知识库可能缺内容、切分过大/过小、或检索权重不当。优先补语料、调 BM25+向量混合权重、加重排。")
    if rt_score is not None and gn_score is not None and rt_score >= 0.7 and gn_score < 0.6:
        insights.append("检索到了但答得差（瓶颈在生成）：这是模型/提示词问题，不是检索。换更强模型（如 Claude）或优化系统提示词最有效。")
    if faith is not None and faith < 0.7:
        insights.append("忠实度低（在编造/幻觉）：加强 grounding 约束、降低温度、要求基于检索内容作答并标注来源。")
    if arel is not None and arel < 0.7:
        insights.append("答案相关性低（跑题）：提示词需更聚焦用户问题，避免答非所问。")
    if cprec is not None and cprec < 0.6:
        insights.append("上下文精度低（检索噪音多）：提高检索阈值或加重排，少喂无关片段给模型。")
    if crec is not None and crec < 0.6:
        insights.append("上下文召回低（漏检相关内容）：扩大召回数量(top-k)、补充语料、检查切分。")
    if overfit is not None and overfit > 0.15:
        insights.append(f"过拟合迹象（训练 vs 留出差 {overfit}）：是在背用例而非真能力，需扩充更多样化、未见过的用例。")
    cats = s.get("by_category", {}) or {}
    if cats:
        worst = sorted(cats.items(), key=lambda kv: kv[1].get("pass_rate", 1))[:3]
        worst_str = "、".join(f"{c}({_pct(d.get('pass_rate'))})" for c, d in worst if d.get("pass_rate", 1) < 1)
        if worst_str:
            insights.append(f"最弱类别：{worst_str} —— 优先针对这些类别补语料/调提示词。")
    fb = s.get("fail_buckets", {}) or {}
    if fb:
        fb_str = "、".join(f"{k}×{v}" for k, v in sorted(fb.items(), key=lambda kv: -kv[1]))
        insights.append(f"失败类型分布：{fb_str}。")
    out.append("")
    out.append("## 四、可执行的改进建议（拿这个去改客户端）")
    if insights:
        for i, t in enumerate(insights, 1):
            out.append(f"{i}. {t}")
    else:
        out.append("- 各项指标均良好，无明显短板。可继续扩充用例提高覆盖。")

    if cats:
        out.append("")
        out.append("## 五、按类别")
        for cat, d in sorted(cats.items(), key=lambda kv: kv[1].get("pass_rate", 1)):
            out.append(f"- {cat}：{d.get('passed', 0)}/{d.get('total', 0)} 通过（{_pct(d.get('pass_rate'))}），均分 {d.get('avg_score', '—')}")

    # 六、失败用例明细（含答案/评审理由/检索质量，逐条可分析）
    failed = [r for r in results if r.get("passed") is False]
    out.append("")
    out.append(f"## 六、失败用例明细（共 {len(failed)} 条）")
    for i, r in enumerate(failed[:60], 1):
        out.append("")
        out.append(f"### {i}. {r.get('query') or r.get('case_id') or ''}")
        out.append(f"- 类别：{r.get('category', '—')}　分数：{r.get('score', '—')}　评审分：{r.get('judge_score', '—')}")
        rm = r.get("retrieval_metrics") or {}
        if rm:
            out.append(f"- 检索：{', '.join(f'{k}={v}' for k, v in rm.items())}")
        cm = r.get("component_metrics") or {}
        if cm:
            out.append(f"- 生成细分：{', '.join(f'{k}={v}' for k, v in cm.items() if v is not None)}")
        if r.get("fail_reason"):
            out.append(f"- 失败原因：{r.get('fail_reason')}")
        if r.get("answer"):
            out.append(f"- 实际答案：{str(r.get('answer'))[:400]}")
        if r.get("reference_answer"):
            out.append(f"- 期望/参考答案：{str(r.get('reference_answer'))[:300]}")
        if r.get("rubric"):
            out.append(f"- 评分标准：{str(r.get('rubric'))[:300]}")
        if r.get("judge_reason"):
            out.append(f"- 评审理由：{str(r.get('judge_reason'))[:400]}")
        srcs = r.get("sources") or []
        if srcs:
            names = ", ".join(str(x.get("filename") or "")[:40] for x in srcs[:5] if x.get("filename"))
            if names:
                out.append(f"- 检索到的来源：{names}")
        if r.get("error"):
            out.append(f"- 错误：{r.get('error')}")
    if len(failed) > 60:
        out.append("")
        out.append(f"> 还有 {len(failed) - 60} 条失败用例未展开（完整数据在 `data/eval_runs/{run_id}.json`）。")
    out.append("")
    out.append("---")
    out.append(f"> 完整机器可读数据：服务器 `data/eval_runs/{run_id}.json`；本报告：`data/eval_runs/{run_id}.report.md`。")
    return "\n".join(out)


@router.get("/eval/runs/{run_id}/report",
            summary="导出某次评测的详细可分析报告(Markdown，并落盘到服务器)",
            tags=["Evaluation"])
async def eval_run_report(run_id: str, request: Request):
    """生成一份详细、可分析的评测报告：分层定位 + 生成细分 + 可执行改进建议 + 每条失败用例明细。
    同时写到服务器 data/eval_runs/{run_id}.report.md，方便你直接在服务器上取用/对比 DeepSeek 与 Claude。"""
    require_admin(request)
    from hashmm.evaluation import metrics as M
    run = M.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="评测运行不存在（请先跑一次并保存）")
    md = _eval_report_markdown(run, run_id)
    try:
        from pathlib import Path
        Path("data/eval_runs").mkdir(parents=True, exist_ok=True)
        (Path("data/eval_runs") / f"{run_id}.report.md").write_text(md, encoding="utf-8")
    except Exception as _e:
        log_suppressed(logger, _e)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        md, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="eval-report-{run_id}.md"'})


@router.get("/eval/capability", summary="能力层评测（语料无关基准）", tags=["Evaluation"])
async def eval_capability(request: Request):
    """Corpus-independent capability benchmark.

    - **routing**: evaluated inline (pure function, no LLM/corpus) — adaptive
      retrieval picks the right mode/hops for canonical query shapes.
    - **cases**: the corpus-independent answer-judged set (refusal / injection /
      fabrication resistance), returned so the caller can run them through the
      system-under-test. These are comparable across any deployment.
    """
    require_admin(request)
    from hashmm.evaluation.capability_eval import build_capability_set, evaluate_routing
    routing = evaluate_routing()
    # Build the answer-judged cases against the live corpus vocab (so refusal
    # subjects are guaranteed absent). Best-effort: vocab is optional.
    vocab = None
    try:
        from hashmm.corpus_vocab import load_corpus_vocab
        vocab = load_corpus_vocab()
    except Exception as _e:
        log_suppressed(logger, _e)
    cap = build_capability_set(corpus_vocab_terms=vocab)
    return {"routing": routing, "meta": cap["meta"],
            "n_answer_cases": len(cap["cases"])}


@router.post("/eval/generate-cases", summary="从语料自动生成候选评测用例", tags=["Evaluation"])
async def generate_eval_cases(request: Request):
    """v17: LLM generates candidate golden cases from corpus chunks.

    Body: {"n": 20, "save": false}. Returns candidates for human review;
    if save=true, merges them into the golden case file.
    """
    admin = require_admin(request)
    body = {}
    try:
        body = await request.json()
    except Exception as _e:
        log_suppressed(logger, _e)
    n = int(body.get("n", 20))
    save = bool(body.get("save", False))

    from hashmm.evaluation.casegen import generate_cases_from_chunks
    from hashmm.api.core.services import ServiceRegistry
    ServiceRegistry.ensure_loaded()
    llm_fn = ServiceRegistry.llm_fn

    # Sample chunks from the retrieval pipeline's corpus
    chunks = []
    try:
        from hashmm.chat_retrieval import get_chat_retrieval
        cr = get_chat_retrieval()
        pipe = getattr(cr, "_pipeline", None)
        bm25 = getattr(pipe, "bm25", None) or getattr(pipe, "_bm25", None)
        docs = getattr(bm25, "documents", None) or getattr(bm25, "_documents", None)
        if docs:
            for d in docs[:5000]:
                txt = d.get("text") if isinstance(d, dict) else str(d)
                fn = d.get("filename", "") if isinstance(d, dict) else ""
                if txt:
                    chunks.append({"text": txt, "filename": fn})
    except Exception as e:
        logger.warning(f"could not sample corpus chunks: {e}")

    import asyncio
    cases = await asyncio.to_thread(generate_cases_from_chunks, chunks, llm_fn, n)
    result = {"generated": len(cases), "cases": cases}

    if save and cases:
        try:
            from pathlib import Path
            p = Path("data/eval/golden_cases.json")
            p.parent.mkdir(parents=True, exist_ok=True)
            existing = []
            if p.exists():
                existing = json.loads(p.read_text(encoding="utf-8"))
            merged = existing + cases
            p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            result["saved_total"] = len(merged)
        except Exception as e:
            result["save_error"] = str(e)[:120]
    db.audit(admin["uid"], admin["sub"], "eval_generate_cases", f"n={len(cases)}")
    return result


@router.get("/eval/compare", summary="对比两次评测（检测回归）", tags=["Evaluation"])
async def compare_eval_runs(baseline: str, candidate: str, request: Request):
    """Diff two saved runs. ?baseline=<id>&candidate=<id>"""
    require_admin(request)
    from hashmm.evaluation import metrics as M
    return M.compare_runs(baseline, candidate)


@router.get("/retrieval-analytics",
            summary="检索质量分析数据",
            description="最近 N 小时的检索统计：score 分布、策略占比、零结果率、KG 命中率",
            tags=["Evaluation"])
async def retrieval_analytics(request: Request, hours: int = 24):
    require_admin(request)
    from hashmm.evaluation.analytics import get_analytics
    return get_analytics(hours=hours)


# ── Data Export / Import ──

@router.get("/export",
            summary="导出全部数据",
            description="导出对话、知识图谱、技能、用户画像、设置",
            tags=["Data"])
async def export_all_data(request: Request):
    """Export all system data as JSON for backup."""
    admin = require_admin(request)
    import time

    data: dict = {"version": "10.0", "exported_at": time.time(), "data": {}}

    # Conversations
    try:
        with db._conn() as c:
            convs = c.execute("SELECT * FROM conversations ORDER BY created_at DESC LIMIT 1000").fetchall()
            data["data"]["conversations"] = [dict(r) for r in convs]
    except Exception:
        data["data"]["conversations"] = []

    # Skills
    try:
        from hashmm.evolution.skill_manager import get_skill_manager
        data["data"]["skills"] = get_skill_manager().list_skills(include_all=True)
    except Exception:
        data["data"]["skills"] = []

    # User profiles
    try:
        with db._conn() as c:
            profiles = c.execute("SELECT * FROM user_profiles").fetchall()
            data["data"]["user_profiles"] = [dict(r) for r in profiles]
    except Exception:
        data["data"]["user_profiles"] = []

    # Settings (models, templates)
    try:
        data["data"]["models"] = db.list_models()
    except Exception:
        data["data"]["models"] = []

    try:
        data["data"]["templates"] = db.list_templates() if hasattr(db, "list_templates") else []
    except Exception:
        data["data"]["templates"] = []

    db.audit(admin["uid"], admin["sub"], "export", f"exported {len(data['data'])} sections")

    from fastapi.responses import JSONResponse
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": "attachment; filename=hashmm-export.json"}
    )


@router.post("/import",
             summary="导入数据",
             description="从备份文件恢复数据",
             tags=["Data"])
async def import_data(request: Request):
    """Import data from a previously exported JSON backup."""
    admin = require_admin(request)
    body = await request.json()

    if "data" not in body:
        raise HTTPException(400, "Invalid backup format: missing 'data' key")

    imported = []

    # Import skills
    if "skills" in body["data"]:
        try:
            from hashmm.evolution.skill_manager import get_skill_manager
            mgr = get_skill_manager()
            for skill_data in body["data"]["skills"]:
                from hashmm.evolution.skill_manager import Skill
                skill = Skill.from_dict(skill_data)
                mgr._save_skill(skill)
            imported.append(f"skills: {len(body['data']['skills'])}")
        except Exception as e:
            imported.append(f"skills: error ({str(e)[:50]})")

    db.audit(admin["uid"], admin["sub"], "import", f"imported: {', '.join(imported)}")
    return {"ok": True, "imported": imported}


# ── V103.48: LLM 网关配置档管理（CC Switch + 9Router 基座的操作入口）──
# 让管理员把多套 provider+key 存成命名档、一键切换当前生效档、配置多 provider
# 故障转移链。生效后由 ServiceRegistry.reload_llm 构建带故障转移的 FailoverLLM。

@router.get("/llm-profiles")
async def llm_profiles_list(request: Request):
    """列出所有 LLM 配置档 + 当前生效档。"""
    require_admin(request)
    from hashmm.llm_gateway import list_profiles
    return list_profiles()


@router.post("/llm-profiles")
async def llm_profile_save(request: Request):
    """新增/更新一个配置档。body: {name, providers:[{name,provider,model,api_key,base_url}], note?, set_active?}

    providers 按优先级排列：第一个是主 provider，其余是故障转移备援链。
    """
    require_admin(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    providers = body.get("providers") or []
    if not name or not providers:
        raise HTTPException(status_code=400, detail="name 和 providers 必填")
    from hashmm.llm_gateway import save_profile
    ok = save_profile(name, providers, note=body.get("note", ""),
                      set_active=bool(body.get("set_active")))
    if not ok:
        raise HTTPException(status_code=500, detail="保存失败")
    # 若设为生效档，立刻让服务重载 LLM（带故障转移）
    if body.get("set_active"):
        try:
            from hashmm.api.core.services import ServiceRegistry
            ServiceRegistry.reload_llm()
        except Exception as e:
            log_suppressed(logger, e)
    return {"ok": True, "active_reloaded": bool(body.get("set_active"))}


@router.post("/llm-profiles/switch")
async def llm_profile_switch(request: Request):
    """一键切换当前生效配置档（CC Switch 的核心动作）。body: {name}"""
    require_admin(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    from hashmm.llm_gateway import switch_profile
    if not switch_profile(name):
        raise HTTPException(status_code=404, detail=f"配置档不存在: {name}")
    try:
        from hashmm.api.core.services import ServiceRegistry
        ServiceRegistry.reload_llm()
    except Exception as e:
        log_suppressed(logger, e)
    admin = require_admin(request)
    db.audit(admin["uid"], admin["sub"], "llm_profile_switch", f"切换到配置档: {name}")
    return {"ok": True, "active": name}


@router.delete("/llm-profiles/{name}")
async def llm_profile_delete(request: Request, name: str):
    """删除一个配置档。"""
    require_admin(request)
    from hashmm.llm_gateway import delete_profile
    if not delete_profile(name):
        raise HTTPException(status_code=404, detail=f"配置档不存在: {name}")
    try:
        from hashmm.api.core.services import ServiceRegistry
        ServiceRegistry.reload_llm()
    except Exception as e:
        log_suppressed(logger, e)
    return {"ok": True}
