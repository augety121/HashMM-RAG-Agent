"""跨 Agent 协作 REST（V317）。

让用户管理好友/组织信任关系，并让自己的 agent 与好友/同事的 agent 安全协作。
所有端点都需登录；用户身份取自 token（不信任请求体里的 from_user，防伪造）。

  好友：
    GET  /api/collab/friends              我的好友列表（含 pending）
    POST /api/collab/friends/request      {target}       发起好友请求
    POST /api/collab/friends/accept       {requester}    接受好友请求
    POST /api/collab/friends/remove       {other}        解除好友
    POST /api/collab/friends/block        {other}        拉黑
  组织：
    GET  /api/collab/orgs                 我加入的组织
    POST /api/collab/orgs/add-member      {org_id,user_id,role}  （需 org owner/admin）
    GET  /api/collab/orgs/{org_id}/members
  协作：
    POST /api/collab/request              {to_user,scope,task,allow_private}  发起协作
    GET  /api/collab/scopes               可申请的协作作用域说明
  审计：
    GET  /api/collab/audit                我相关的协作审计日志
    GET  /api/collab/audit/incoming       别人向我请求过什么（自我审计）
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from hashmm.api.auth import require_admin, require_auth, require_principal_id
from hashmm.collab.audit import get_audit_log
from hashmm.collab.policy import ALLOWED_SCOPES
from hashmm.collab.trust import get_trust_store

router = APIRouter(prefix="/api/collab", tags=["collab"])


def _uid(request: Request) -> str:
    return require_principal_id(request)


# ── 好友 ──────────────────────────────────────────────
@router.get("/friends", summary="我的好友列表（含待处理请求）")
async def list_friends(request: Request):
    uid = _uid(request)
    return {"ok": True, "friends": get_trust_store().list_friends(uid)}


@router.post("/friends/request", summary="发起好友请求")
async def friend_request(request: Request):
    uid = _uid(request)
    body = await request.json()
    target = str(body.get("target") or "").strip()
    return get_trust_store().request_friend(uid, target)


@router.post("/friends/accept", summary="接受好友请求")
async def friend_accept(request: Request):
    uid = _uid(request)
    body = await request.json()
    requester = str(body.get("requester") or "").strip()
    return get_trust_store().accept_friend(uid, requester)


@router.post("/friends/remove", summary="解除好友")
async def friend_remove(request: Request):
    uid = _uid(request)
    body = await request.json()
    return get_trust_store().remove_friend(uid, str(body.get("other") or "").strip())


@router.post("/friends/block", summary="拉黑用户")
async def friend_block(request: Request):
    uid = _uid(request)
    body = await request.json()
    return get_trust_store().block(uid, str(body.get("other") or "").strip())


# ── 组织 ──────────────────────────────────────────────
@router.get("/orgs", summary="我加入的组织")
async def my_orgs(request: Request):
    uid = _uid(request)
    return {"ok": True, "orgs": get_trust_store().user_orgs(uid)}


@router.post("/orgs/add-member", summary="添加组织成员（需该组织 owner/admin）")
async def org_add_member(request: Request):
    uid = _uid(request)
    body = await request.json()
    org_id = str(body.get("org_id") or "").strip()
    target = str(body.get("user_id") or "").strip()
    role = str(body.get("role") or "member").strip()
    ts = get_trust_store()
    # 权限：只有该组织的 owner/admin 能拉人（首个成员自动成 owner）
    members = ts.org_members(org_id)
    if members:
        me = next((m for m in members if m["user_id"] == uid), None)
        if not me or me.get("role") not in ("owner", "admin"):
            return {"ok": False, "detail": "只有组织 owner/admin 能添加成员"}
    else:
        # 空组织 → 创建者先成为 owner
        ts.add_org_member(org_id, uid, role="owner")
    return ts.add_org_member(org_id, target, role=role)


@router.get("/orgs/{org_id}/members", summary="组织成员列表")
async def org_members(request: Request, org_id: str):
    uid = _uid(request)
    ts = get_trust_store()
    # 只有本组织成员能看成员列表
    if uid not in {m["user_id"] for m in ts.org_members(org_id)}:
        return {"ok": False, "detail": "非本组织成员，无权查看"}
    return {"ok": True, "members": ts.org_members(org_id)}


# ── 协作 ──────────────────────────────────────────────
@router.get("/scopes", summary="可申请的协作作用域")
async def collab_scopes(request: Request):
    require_auth(request)
    return {"ok": True, "scopes": [{"scope": k, **v} for k, v in ALLOWED_SCOPES.items()]}


@router.post("/request", summary="向好友/同事的 agent 发起协作（经交互 Agent 安全网关）")
async def collab_request(request: Request):
    uid = _uid(request)
    body = await request.json()
    to_user = str(body.get("to_user") or "").strip()
    scope = str(body.get("scope") or "").strip()
    task = str(body.get("task") or "").strip()
    allow_private = bool(body.get("allow_private"))
    if not to_user or not scope or not task:
        return {"ok": False, "detail": "缺少 to_user / scope / task"}

    from hashmm.collab.interaction_agent import get_interaction_agent
    from hashmm.collab.rpc_transport import is_remote_user, RpcClient, get_peer_registry
    ia = get_interaction_agent(trust_store=get_trust_store(), audit=get_audit_log())

    if is_remote_user(to_user):
        # ★ 跨机投递：交互 Agent 出站脱敏 + 目标信任校验后，走签名 RPC 发到对方机器。
        #   对方机器的 /rpc/inbound 验签 → 再经它自己的交互 Agent 入站。安全语义不变。
        transport = RpcClient(registry=get_peer_registry()).make_transport()
        return ia.send_outbound(uid, to_user, scope, task,
                                transport=transport, allow_private=allow_private)

    # 本地投递：同机不同用户，交互 Agent 出站 → 对方交互 Agent 入站（同进程）。
    def _executor(safe_task: str) -> str:
        try:
            from hashmm.api.model_manager import get_active_llm_fn
            fn, _ = get_active_llm_fn()
            if callable(fn):
                return str(fn(f"[协作任务，只在公开能力范围内回答]\n{safe_task}") or "")
        except Exception:  # noqa: BLE001
            pass
        return "（本机 agent 暂时不可用）"

    def _transport(req: dict) -> dict:
        return ia.handle_inbound(req, agent_executor=_executor, my_user_id=req.get("to_user", ""))

    return ia.send_outbound(uid, to_user, scope, task,
                            transport=_transport, allow_private=allow_private)


@router.get("/interaction-agent", summary="交互 Agent（对外通信安全网关）状态")
async def interaction_agent_status(request: Request):
    require_auth(request)
    from hashmm.collab.interaction_agent import get_interaction_agent
    ia = get_interaction_agent(trust_store=get_trust_store(), audit=get_audit_log())
    return {"ok": True, **ia.status()}


# ── 审计 ──────────────────────────────────────────────
@router.get("/audit", summary="我相关的协作审计日志")
async def collab_audit(request: Request):
    uid = _uid(request)
    return {"ok": True, "log": get_audit_log().query(uid, limit=100)}


@router.get("/audit/incoming", summary="别人向我请求过什么（自我审计）")
async def collab_audit_incoming(request: Request):
    uid = _uid(request)
    return {"ok": True, "log": get_audit_log().incoming_data_access(uid, limit=100)}


# ── 跨机对等体（RPC 传输）─────────────────────────────
# 配对建立的是【机器级信任】（共享验签密钥），风险高于加好友 → 需管理员。
@router.get("/peers", summary="已配对的远端 HashMM 实例（不含密钥）")
async def list_peers(request: Request):
    require_admin(request)
    from hashmm.collab.rpc_transport import get_peer_registry, self_peer_id
    return {"ok": True, "self_peer_id": self_peer_id(),
            "peers": get_peer_registry().list_peers()}


@router.post("/peers/gen-secret", summary="生成一个强共享密钥（配对用，两机各存一份）")
async def gen_peer_secret(request: Request):
    require_admin(request)
    from hashmm.collab.rpc_transport import PeerRegistry
    return {"ok": True, "secret": PeerRegistry.gen_secret()}


@router.post("/peers/pair", summary="配对一个远端实例 {peer_id,endpoint,secret,label}")
async def pair_peer(request: Request):
    require_admin(request)
    body = await request.json()
    from hashmm.collab.rpc_transport import get_peer_registry
    return get_peer_registry().pair(
        str(body.get("peer_id") or "").strip(),
        str(body.get("endpoint") or "").strip(),
        str(body.get("secret") or "").strip(),
        label=str(body.get("label") or "").strip())


@router.post("/peers/remove", summary="移除一个已配对的远端实例 {peer_id}")
async def remove_peer(request: Request):
    require_admin(request)
    body = await request.json()
    from hashmm.collab.rpc_transport import get_peer_registry
    return get_peer_registry().remove(str(body.get("peer_id") or "").strip())


# ── 入站 RPC（机器对机器）───────────────────────────────
# ★ 不走登录认证——它由 HMAC 签名认证（调用方是机器，不是登录用户）。
#   必须用【原始请求体字节】验签：FastAPI 若解析再序列化，字节会变、签名对不上。
@router.post("/rpc/inbound", summary="接收来自已配对远端实例的协作请求（签名认证）")
async def rpc_inbound(request: Request):
    raw = await request.body()
    body_str = raw.decode("utf-8", "replace")
    headers = {k: v for k, v in request.headers.items()}

    # 本机身份：作为 to_user（不信任线上字段）。取当前登录管理员之外，
    # 用请求体里对方期望的 to_user 的本地部分（对方已知我方用户名）。
    try:
        import json as _json
        _req = _json.loads(body_str) if body_str else {}
    except Exception:  # noqa: BLE001
        _req = {}
    my_user_id = str(_req.get("to_user") or "").strip()

    from hashmm.collab.rpc_transport import RpcInbound, get_peer_registry
    from hashmm.collab.interaction_agent import get_interaction_agent
    ia = get_interaction_agent(trust_store=get_trust_store(), audit=get_audit_log())

    def _executor(safe_task: str) -> str:
        try:
            from hashmm.api.model_manager import get_active_llm_fn
            fn, _ = get_active_llm_fn()
            if callable(fn):
                return str(fn(f"[远端协作任务，只在公开能力范围内回答]\n{safe_task}") or "")
        except Exception:  # noqa: BLE001
            pass
        return "（本机 agent 暂时不可用）"

    inbound = RpcInbound(ia, registry=get_peer_registry())
    return inbound.receive(headers, body_str, agent_executor=_executor, my_user_id=my_user_id)
