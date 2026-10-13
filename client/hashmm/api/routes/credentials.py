"""凭据仓 REST（V205 P2-8）——管理员管理 per-connector 密钥，列表只回掩码。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from hashmm.api import database as db
from hashmm.api.auth import require_admin
from hashmm import credentials as cred

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


@router.get("", summary="凭据列表（掩码）")
async def list_creds(request: Request):
    require_admin(request)
    return {"items": cred.list_credentials(),
            "placeholder": "在 MCP/工具配置里写 ${cred:名称}，执行时服务端替换，密钥不进前端与日志"}


@router.put("/{connector}", summary="写入/更新凭据")
async def put_cred(connector: str, request: Request):
    admin = require_admin(request)
    body = await request.json()
    value = str(body.get("value", "")).strip()
    if not value:
        raise HTTPException(400, "value 不能为空")
    ok = cred.set_credential(connector, value, note=str(body.get("note", ""))[:200])
    if not ok:
        raise HTTPException(500, "写入失败")
    db.audit(admin["uid"], admin["sub"], "credential_set", connector)
    return {"ok": True, "connector": connector}


@router.delete("/{connector}", summary="删除凭据")
async def del_cred(connector: str, request: Request):
    admin = require_admin(request)
    ok = cred.delete_credential(connector)
    if not ok:
        raise HTTPException(404, "凭据不存在")
    db.audit(admin["uid"], admin["sub"], "credential_delete", connector)
    return {"ok": True}
