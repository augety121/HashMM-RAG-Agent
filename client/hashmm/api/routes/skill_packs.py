"""Skill pack routes — 技能包的增删查改与导入（/api/skills/packs）。

权限模型（与项目其它面板一致）：
  · 列表 / 详情：登录即可（普通用户能看到 Agent 装了哪些"手册"，透明可审计）。
  · 导入 / 启停 / 删除 / 播种内置：仅管理员（改变全局 Agent 行为的操作）。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from hashmm.api.auth import require_admin, require_auth
from hashmm.utils import get_logger

logger = get_logger("hashmm.routes.skill_packs")

router = APIRouter(prefix="/api/skills/packs", tags=["skill-packs"])


def _mgr():
    from hashmm.agent.skill_packs import get_skill_pack_manager
    return get_skill_pack_manager()


def _user_mgr(request: Request):
    """Resolve one owner-isolated skill library from trusted auth state."""
    user = require_auth(request)
    owner = str(user.get("uid") or user.get("id") or user.get("sub") or "").strip()
    if not owner:
        raise HTTPException(401, "登录状态缺少用户标识")
    from hashmm.agent.skill_packs import get_user_skill_pack_manager
    return user, get_user_skill_pack_manager(owner)


# ── V300 第四期：技能市场（可安装目录 + 一键安装）──────────────────────
@router.get("/catalog")
async def list_catalog(request: Request):
    """技能市场：列出可安装的精选技能（内置 Anthropic 官方 skills，已适配本项目）。登录可见。"""
    require_auth(request)
    from hashmm.agent.skill_catalog import list_catalog as _lc, catalog_stats as _cs
    return {"ok": True, "catalog": _lc(), "stats": _cs()}


class CatalogInstallReq(BaseModel):
    catalog_id: str


@router.post("/catalog/install")
async def install_catalog(req: CatalogInstallReq, request: Request):
    """从市场一键安装一个技能到用户技能仓。仅管理员（改变全局 Agent 行为）。"""
    require_admin(request)
    from hashmm.agent.skill_catalog import install_from_catalog
    r = install_from_catalog(req.catalog_id)
    if not r.get("ok"):
        raise HTTPException(400, r.get("message", "安装失败"))
    return r


# ── V300 第四期：用户可配置 Hooks（工具调用的 notify/confirm/block 规则）──
@router.get("/hooks")
async def get_user_hooks(request: Request):
    """列出用户配置的 hook 规则。登录可见。"""
    require_auth(request)
    from hashmm.agent.user_hooks import HookConfigError, load_hooks_strict
    try:
        hooks = load_hooks_strict()
    except HookConfigError as exc:
        raise HTTPException(500, f"Hook 配置损坏：{exc}") from exc
    return {"ok": True, "hooks": hooks}


class HooksReq(BaseModel):
    hooks: list[dict]


@router.post("/hooks")
async def set_user_hooks(req: HooksReq, request: Request):
    """保存 hook 规则。仅管理员（影响全局 Agent 工具调用行为）。"""
    require_admin(request)
    from hashmm.agent.user_hooks import save_hooks, load_hooks
    if not save_hooks(req.hooks):
        raise HTTPException(400, "保存失败")
    return {"ok": True, "hooks": load_hooks()}


# ── V338：可执行 Python Hooks 的精确哈希信任闸 ─────────────────────
@router.get("/code-hooks")
async def list_code_hooks(request: Request):
    """列出 Hook 来源、当前摘要与信任状态；只做 AST 检查，不导入未信任代码。"""
    user = require_auth(request)
    from hashmm.user_hooks import list_hook_inventory
    return {
        "ok": True,
        "enabled": (os.environ.get("HASHMM_USER_HOOKS", "1") or "1") != "0",
        "can_manage": user.get("role") == "admin",
        "items": list_hook_inventory(),
        "warning": "Python Hook 以 后端系统用户 的完整权限执行；仅信任你逐行审查过的当前 SHA-256。",
    }


class CodeHookTrustReq(BaseModel):
    expected_sha256: str


def _audit_code_hook(user: dict, action: str, name: str, sha256: str = "") -> None:
    """Best-effort audit without recording hook source or user data."""
    try:
        from hashmm.api import database as db
        uid = str(user.get("uid") or user.get("sub") or user.get("id") or "admin")
        detail = f"{name} sha256={sha256}" if sha256 else name
        db.audit(uid, uid, action, detail)
    except Exception as exc:
        logger.warning("code hook audit failed: %s", type(exc).__name__)


@router.post("/code-hooks/{hook_name}/trust")
async def trust_code_hook(hook_name: str, req: CodeHookTrustReq, request: Request):
    """信任请求携带界面看到的摘要；文件变化时比较失败，不会误信新内容。"""
    user = require_admin(request)
    from hashmm.user_hooks import HookTrustError, trust_hook
    try:
        item = trust_hook(
            hook_name,
            req.expected_sha256,
            trusted_by=str(user.get("uid") or user.get("sub") or user.get("id") or "admin"),
        )
    except HookTrustError as exc:
        raise HTTPException(400, str(exc)) from exc
    _audit_code_hook(user, "code_hook_trust", hook_name, item.get("sha256", ""))
    return {"ok": True, "hook": item, "restart_required": True}


@router.post("/code-hooks/{hook_name}/revoke")
async def revoke_code_hook(hook_name: str, request: Request):
    """撤销后，已加载 wrapper 在下一次事件前也会因摘要检查失活。"""
    user = require_admin(request)
    from hashmm.user_hooks import HookTrustError, revoke_hook
    try:
        revoked = revoke_hook(hook_name)
    except HookTrustError as exc:
        raise HTTPException(400, str(exc)) from exc
    _audit_code_hook(user, "code_hook_revoke", hook_name)
    return {"ok": True, "revoked": revoked}


@router.get("")
async def list_packs(request: Request):
    require_auth(request)
    packs = [p.to_dict() for p in _mgr().list_packs()]
    return {"ok": True, "packs": packs, "enabled_count": sum(1 for p in packs if p["enabled"])}


# ── V571：普通用户的私有技能库 ────────────────────────────────────
# 路由必须放在 /{pack_id} 之前，避免 FastAPI 把 "mine" 当成 pack_id。
@router.get("/mine")
async def list_my_packs(request: Request):
    _, manager = _user_mgr(request)
    packs = [pack.to_dict() for pack in manager.list_packs()]
    return {
        "ok": True,
        "packs": packs,
        "enabled_count": sum(1 for pack in packs if pack["enabled"]),
        "execution": "instructions_only",
    }


@router.get("/mine/{pack_id}")
async def my_pack_detail(pack_id: str, request: Request):
    _, manager = _user_mgr(request)
    pack = manager.get_pack(pack_id)
    if not pack:
        raise HTTPException(404, "技能包不存在")
    return {
        "ok": True,
        "pack": pack.to_dict(),
        "skill_md": manager.read_skill_md(pack_id),
        "files": manager.file_tree(pack_id),
    }


class MyImportReq(BaseModel):
    kind: str
    url: str | None = None
    source: str | None = None


@router.post("/mine/import")
async def import_my_pack(req: MyImportReq, request: Request):
    _, manager = _user_mgr(request)
    source = str(req.source or "website").strip().lower()
    if source not in {"website", "github", "codex", "claude", "agent-skills"}:
        raise HTTPException(400, "未知的技能来源")
    try:
        if req.kind == "github":
            if not req.url:
                raise HTTPException(400, "缺少 GitHub 网址")
            packs = manager.install_from_github(req.url)
        elif req.kind == "url":
            if not req.url:
                raise HTTPException(400, "缺少技能网址")
            packs = manager.install_from_url(req.url, source)
        else:
            raise HTTPException(400, "kind 只支持 github / url")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"导入失败：{exc}") from exc
    return {"ok": True, "installed": [pack.to_dict() for pack in packs]}


@router.post("/mine/upload")
async def upload_my_pack(request: Request, file: UploadFile = File(...)):
    _, manager = _user_mgr(request)
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "请上传 .zip 技能包")
    source = str(request.query_params.get("source") or "upload").strip().lower()
    if source not in {"upload", "codex", "claude", "agent-skills"}:
        raise HTTPException(400, "未知的技能来源")
    from hashmm.agent.skill_packs import MAX_ZIP_BYTES
    data = await file.read(MAX_ZIP_BYTES + 1)
    if len(data) > MAX_ZIP_BYTES:
        raise HTTPException(413, f"技能包不能超过 {MAX_ZIP_BYTES // (1024 * 1024)}MB")
    try:
        packs = manager.install_from_zip_bytes(data, f"{source}:{file.filename}")
    except Exception as exc:
        raise HTTPException(400, f"导入失败：{exc}") from exc
    return {"ok": True, "installed": [pack.to_dict() for pack in packs]}


class MyToggleReq(BaseModel):
    enabled: bool


@router.post("/mine/{pack_id}/toggle")
async def toggle_my_pack(pack_id: str, req: MyToggleReq, request: Request):
    _, manager = _user_mgr(request)
    if not manager.set_enabled(pack_id, req.enabled):
        raise HTTPException(404, "技能包不存在")
    return {"ok": True, "enabled": req.enabled}


@router.delete("/mine/{pack_id}")
async def delete_my_pack(pack_id: str, request: Request):
    _, manager = _user_mgr(request)
    if not manager.delete_pack(pack_id):
        raise HTTPException(404, "技能包不存在")
    return {"ok": True}


@router.get("/{pack_id}")
async def pack_detail(pack_id: str, request: Request):
    require_auth(request)
    m = _mgr()
    p = m.get_pack(pack_id)
    if not p:
        raise HTTPException(404, "技能包不存在")
    return {"ok": True, "pack": p.to_dict(), "skill_md": m.read_skill_md(pack_id), "files": m.file_tree(pack_id)}


class ImportReq(BaseModel):
    kind: str                 # "github" | "path"
    url: str | None = None    # kind=github：https://github.com/owner/repo[/tree/branch/sub]
    path: str | None = None   # kind=path：后端服务器上的目录（含 SKILL.md）


@router.post("/import")
async def import_pack(req: ImportReq, request: Request):
    require_admin(request)
    m = _mgr()
    try:
        if req.kind == "github":
            if not req.url:
                raise HTTPException(400, "缺少 url")
            packs = m.install_from_github(req.url)
        elif req.kind == "path":
            if not req.path:
                raise HTTPException(400, "缺少 path")
            from pathlib import Path
            packs = [m.install_from_dir(Path(req.path).expanduser(), f"path:{req.path}")]
        else:
            raise HTTPException(400, "kind 只支持 github / path")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"导入失败：{e}") from e
    return {"ok": True, "installed": [p.to_dict() for p in packs]}


@router.post("/upload")
async def upload_pack(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "请上传 .zip（一个技能目录或一整个技能仓库均可）")
    data = await file.read()
    try:
        packs = _mgr().install_from_zip_bytes(data, f"upload:{file.filename}")
    except Exception as e:
        raise HTTPException(400, f"导入失败：{e}") from e
    return {"ok": True, "installed": [p.to_dict() for p in packs]}


class ToggleReq(BaseModel):
    enabled: bool


@router.post("/{pack_id}/toggle")
async def toggle_pack(pack_id: str, req: ToggleReq, request: Request):
    require_admin(request)
    if not _mgr().set_enabled(pack_id, req.enabled):
        raise HTTPException(404, "技能包不存在")
    return {"ok": True, "enabled": req.enabled}


@router.delete("/{pack_id}")
async def delete_pack(pack_id: str, request: Request):
    require_admin(request)
    if not _mgr().delete_pack(pack_id):
        raise HTTPException(404, "技能包不存在")
    return {"ok": True}


@router.post("/seed-builtins")
async def seed_builtins(request: Request):
    """把仓库随发的内置技能包补种进数据区（幂等：只补缺，不覆盖）。"""
    require_admin(request)
    added = _mgr().seed_builtins()
    return {"ok": True, "added": added}
