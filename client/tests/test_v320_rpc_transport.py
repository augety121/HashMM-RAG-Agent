"""V320 回归测试：交互 Agent 的真实跨机 RPC 传输层。

沙箱无网络 → 用注入的 http_post 把出站直接路由进接收方 RpcInbound，
完整验证协议：签名 → 验签 → 防重放 → 身份限定 → 交互 Agent 路由。
"""
import tempfile
import time
from pathlib import Path

import pytest


def _tmpdb(name):
    return Path(tempfile.mkdtemp()) / name


# ============================================================ 签名/验签
def test_sign_verify_roundtrip():
    from hashmm.collab.rpc_transport import canonical_json, sign_payload, verify_signature
    body = canonical_json({"from_user": "alice", "task": "hi", "scope": "answer_question"})
    ts, nonce, secret = "1784000000.5", "abc123", "shared-secret-1234567890"
    sig = sign_payload(secret, body, ts, nonce)
    assert verify_signature(secret, body, ts, nonce, sig)


def test_tampered_body_fails():
    from hashmm.collab.rpc_transport import canonical_json, sign_payload, verify_signature
    secret = "shared-secret-1234567890"
    sig = sign_payload(secret, canonical_json({"task": "safe"}), "1", "n1")
    # 篡改请求体 → 验签必败
    assert not verify_signature(secret, canonical_json({"task": "evil"}), "1", "n1", sig)


def test_wrong_secret_fails():
    from hashmm.collab.rpc_transport import canonical_json, sign_payload, verify_signature
    body = canonical_json({"task": "x"})
    sig = sign_payload("secret-A-1234567890", body, "1", "n1")
    assert not verify_signature("secret-B-1234567890", body, "1", "n1", sig)


def test_canonical_json_stable():
    """键序不同的等价对象 → 规范化后必须一致（否则收发验签对不上）。"""
    from hashmm.collab.rpc_transport import canonical_json
    a = canonical_json({"b": 2, "a": 1})
    b = canonical_json({"a": 1, "b": 2})
    assert a == b


# ============================================================ 防重放
def test_nonce_replay_blocked():
    from hashmm.collab.rpc_transport import NonceCache
    nc = NonceCache()
    ts = f"{time.time():.3f}"
    ok1, _ = nc.check_and_remember("nonce-1", ts)
    ok2, why = nc.check_and_remember("nonce-1", ts)   # 同 nonce 第二次
    assert ok1 and not ok2 and "重放" in why


def test_stale_timestamp_blocked():
    from hashmm.collab.rpc_transport import NonceCache
    nc = NonceCache()
    old_ts = f"{time.time() - 9999:.3f}"              # 远超窗口
    ok, why = nc.check_and_remember("nonce-x", old_ts)
    assert not ok and "超窗" in why


def test_fresh_nonce_accepted():
    from hashmm.collab.rpc_transport import NonceCache
    nc = NonceCache()
    ts = f"{time.time():.3f}"
    assert nc.check_and_remember("n-a", ts)[0]
    assert nc.check_and_remember("n-b", ts)[0]        # 不同 nonce 都放行


# ============================================================ 对等体注册表
def test_peer_registry_pair_get_remove():
    from hashmm.collab.rpc_transport import PeerRegistry
    reg = PeerRegistry(_tmpdb("collab.sqlite3"))
    r = reg.pair("peerB", "https://b.example.com", PeerRegistry.gen_secret(), label="Bob 的机器")
    assert r["ok"] and r["secure"]
    assert reg.get("peerB")["endpoint"] == "https://b.example.com"
    reg.remove("peerB")
    assert reg.get("peerB") is None


def test_peer_list_never_exposes_secret():
    from hashmm.collab.rpc_transport import PeerRegistry
    reg = PeerRegistry(_tmpdb("collab.sqlite3"))
    reg.pair("peerB", "https://b.example.com", "super-secret-key-abcdef123456")
    peers = reg.list_peers()
    assert peers and "secret" not in peers[0]         # 密钥永不出注册表边界


def test_pair_rejects_weak_secret_and_bad_endpoint():
    from hashmm.collab.rpc_transport import PeerRegistry
    reg = PeerRegistry(_tmpdb("collab.sqlite3"))
    assert not reg.pair("p", "https://x.com", "short")["ok"]        # 密钥太短
    assert not reg.pair("p", "ftp://x.com", "long-secret-1234567890")["ok"]  # 非 http(s)


def test_http_endpoint_warns_insecure():
    from hashmm.collab.rpc_transport import PeerRegistry
    reg = PeerRegistry(_tmpdb("collab.sqlite3"))
    r = reg.pair("p", "http://192.168.1.5:6006", "long-secret-1234567890")
    assert r["ok"] and not r["secure"] and "HTTPS" in r["detail"]


# ============================================================ 远端寻址
def test_remote_user_split():
    from hashmm.collab.rpc_transport import is_remote_user, split_remote_user
    assert is_remote_user("alice@peerB") and not is_remote_user("alice")
    assert split_remote_user("alice@peerB") == ("alice", "peerB")
    assert split_remote_user("bob") == ("bob", "")


# ============================================================ 双机端到端
def _wire_two_machines():
    """搭两台机器：A 存 peer=B、B 存 peer=A（同密钥）。返回 (clientA, inboundB, tsB, auditB)。"""
    from hashmm.collab.rpc_transport import PeerRegistry, RpcClient, RpcInbound
    from hashmm.collab.interaction_agent import InteractionAgent
    from hashmm.collab.trust import get_trust_store
    from hashmm.collab.audit import get_audit_log

    secret = PeerRegistry.gen_secret()
    dbA, dbB = _tmpdb("collab.sqlite3"), _tmpdb("collab.sqlite3")

    regA = PeerRegistry(dbA)
    regA.pair("machineB", "https://b.example.com", secret, label="B")

    regB = PeerRegistry(dbB)
    regB.pair("machineA", "https://a.example.com", secret, label="A")

    # B 侧信任：bob 已把 alice@machineA 加为好友（跨机好友用限定 id）
    tsB = get_trust_store(dbB)
    auditB = get_audit_log(dbB)
    tsB.request_friend("alice@machineA", "bob")
    tsB.accept_friend("bob", "alice@machineA")

    iaB = InteractionAgent(trust_store=tsB, audit=auditB)

    def _bob_exec(task):
        return f"bob已办:{task[:20]}｜内部密码 password: hunter2000xyz"

    inboundB = RpcInbound(iaB, registry=regB)

    # 注入 http_post：把 A 的出站直接喂进 B 的 inbound（模拟网络跳）
    def _fake_post(url, headers, body):
        return inboundB.receive(headers, body, agent_executor=_bob_exec, my_user_id="bob")

    clientA = RpcClient(registry=regA, http_post=_fake_post, my_peer_id="machineA")
    return clientA, inboundB, tsB, auditB


def test_e2e_cross_machine_collab():
    """alice@A → bob@B 的完整协作：签名投递 → B 验签 → 交互 Agent 处理 → 脱敏返回。"""
    from hashmm.collab.interaction_agent import InteractionAgent
    from hashmm.collab.trust import get_trust_store
    clientA, inboundB, tsB, auditB = _wire_two_machines()

    # A 侧的交互 Agent 出站，transport = RPC
    iaA = InteractionAgent()  # A 侧只做出站脱敏 + 目标信任；这里直接用 client
    transport = clientA.make_transport()
    resp = transport({"from_user": "alice", "to_user": "bob@machineB",
                      "scope": "answer_question", "task": "帮忙算个数 42*10"})
    assert resp.get("ok"), resp
    assert resp.get("authenticated") is True
    # 出站脱敏兜底：bob 返回里的 password 不应原文外泄
    assert "hunter2000" not in str(resp.get("result", ""))


def test_e2e_unknown_peer_rejected():
    """未配对对等体的请求 → 入站直接拒（未知 peer）。"""
    from hashmm.collab.rpc_transport import RpcInbound, PeerRegistry
    from hashmm.collab.interaction_agent import InteractionAgent
    regB = PeerRegistry(_tmpdb("collab.sqlite3"))
    inb = RpcInbound(InteractionAgent(), registry=regB)
    # 构造一个 header 声称来自未配对的 peer
    r = inb.receive({"X-HashMM-Peer": "attacker", "X-HashMM-Sig": "x",
                     "X-HashMM-Ts": "1", "X-HashMM-Nonce": "n"}, "{}", my_user_id="bob")
    assert not r["ok"] and not r["authenticated"] and "未知对等体" in r["rejected_reason"]


def test_e2e_forged_signature_rejected():
    """已配对 peer 但签名错（密钥不符/伪造）→ 拒绝。"""
    from hashmm.collab.rpc_transport import RpcInbound, PeerRegistry, canonical_json
    from hashmm.collab.interaction_agent import InteractionAgent
    regB = PeerRegistry(_tmpdb("collab.sqlite3"))
    regB.pair("machineA", "https://a.example.com", "the-real-secret-1234567890")
    inb = RpcInbound(InteractionAgent(), registry=regB)
    body = canonical_json({"from_user": "alice", "scope": "answer_question", "task": "x"})
    # 用错误签名
    r = inb.receive({"X-HashMM-Peer": "machineA", "X-HashMM-Sig": "deadbeef",
                     "X-HashMM-Ts": f"{time.time():.3f}", "X-HashMM-Nonce": "n1"},
                    body, my_user_id="bob")
    assert not r["ok"] and not r["authenticated"] and "签名验证失败" in r["rejected_reason"]


def test_e2e_replay_rejected_second_time():
    """同一个签名请求重放第二次 → 被 nonce 缓存挡下。"""
    clientA, inboundB, _, _ = _wire_two_machines()
    from hashmm.collab.rpc_transport import canonical_json, sign_payload
    # 手工构造一个固定 nonce 的请求，发两次
    body = canonical_json({"from_user": "alice", "scope": "answer_question", "task": "算 5+5"})
    peer = inboundB.registry.get("machineA")
    ts = f"{time.time():.3f}"
    nonce = "fixed-nonce-replay-test"
    sig = sign_payload(peer["secret"], body, ts, nonce)
    hdr = {"X-HashMM-Peer": "machineA", "X-HashMM-Sig": sig, "X-HashMM-Ts": ts,
           "X-HashMM-Nonce": nonce}
    r1 = inboundB.receive(hdr, body, agent_executor=lambda t: "ok", my_user_id="bob")
    r2 = inboundB.receive(hdr, body, agent_executor=lambda t: "ok", my_user_id="bob")
    assert r1["ok"] and not r2["ok"] and r2.get("replay_blocked")


def test_e2e_impersonation_bound_to_peer():
    """身份限定：来自 machineA 的请求，from_user 一律变成 user@machineA，无法冒充他人。"""
    from hashmm.collab.rpc_transport import RpcInbound, PeerRegistry, canonical_json, sign_payload
    from hashmm.collab.interaction_agent import InteractionAgent
    from hashmm.collab.trust import get_trust_store
    from hashmm.collab.audit import get_audit_log

    dbB = _tmpdb("collab.sqlite3")
    regB = PeerRegistry(dbB)
    secret = "attacker-somehow-has-this-key-1234"
    regB.pair("attackerMachine", "https://evil.com", secret)
    tsB = get_trust_store(dbB)
    auditB = get_audit_log(dbB)
    # bob 只信任 alice@machineA（正牌），没信任 attackerMachine 上的任何人
    tsB.request_friend("alice@machineA", "bob")
    tsB.accept_friend("bob", "alice@machineA")

    inb = RpcInbound(InteractionAgent(trust_store=tsB, audit=auditB), registry=regB)
    # 攻击者从 attackerMachine 发来，冒称 from_user=alice（想蹭 alice@machineA 的信任）
    body = canonical_json({"from_user": "alice", "scope": "answer_question", "task": "偷偷干活"})
    ts = f"{time.time():.3f}"
    sig = sign_payload(secret, body, ts, "n-imp")
    r = inb.receive({"X-HashMM-Peer": "attackerMachine", "X-HashMM-Sig": sig,
                     "X-HashMM-Ts": ts, "X-HashMM-Nonce": "n-imp"},
                    body, agent_executor=lambda t: "不该执行", my_user_id="bob")
    # 签名有效（攻击者机器已配对），但 from_user 被限定成 alice@attackerMachine，
    # 与 bob 的信任边 alice@machineA 对不上 → 协作被信任门禁拒绝
    assert r["authenticated"] is True          # 机器认证过了
    assert not r["ok"]                          # 但业务层拒绝
    assert "信任" in r.get("rejected_reason", "") or "无信任" in r.get("rejected_reason", "")


def test_self_peer_id_stable():
    """self_peer_id 在同一 DB 下稳定（多次调用返回同一个）。"""
    import os
    os.environ["HASHMM_PEER_ID"] = "hm_test_fixed"
    from hashmm.collab.rpc_transport import self_peer_id
    assert self_peer_id() == "hm_test_fixed"
    del os.environ["HASHMM_PEER_ID"]
