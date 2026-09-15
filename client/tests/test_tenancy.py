"""多租户隔离 测试。

覆盖：关闭时零变化、开启后创建/分配/解析/隔离、配额扣减拒绝、可逆。
全程用临时 DB（tmp_db fixture），绝不碰真实 data。
"""
import pytest

pytestmark = pytest.mark.unit


def _mk_users(db):
    """造两个用户，返回真实 id（users 主键是随机 id 非 username）。"""
    ids = {}
    try:
        a = db.create_user("alice", "pw_a", role="user")
        b = db.create_user("bob", "pw_b", role="user")
        ids["alice"] = a["id"] if a else None
        ids["bob"] = b["id"] if b else None
    except Exception:
        pass
    if not ids.get("alice"):
        with db._conn() as c:
            for u in ("alice", "bob"):
                row = c.execute("SELECT id FROM users WHERE username=?", (u,)).fetchone()
                if not row:
                    c.execute("INSERT OR IGNORE INTO users (id, username, password_hash, salt) VALUES (?,?,?,?)",
                              (u, u, "x", "x"))
            ids["alice"] = c.execute("SELECT id FROM users WHERE username='alice'").fetchone()["id"]
            ids["bob"] = c.execute("SELECT id FROM users WHERE username='bob'").fetchone()["id"]
    return ids


def _fresh_db(tmp_db):
    """强制让 database 模块用当前测试的临时 DB（DB_PATH + 连接池都是模块级，需刷新）。"""
    from pathlib import Path
    from hashmm.api import database as db
    db.DB_PATH = Path(tmp_db)   # 指向本测试的临时 sqlite
    db._pool = None             # 丢弃旧连接池，强制按新 DB_PATH 重建（测试隔离）
    db.init_db()
    return db


def test_disabled_is_zero_change(tmp_db, monkeypatch):
    """多租户关闭时：所有用户归 default、配额恒 allow（单租户零影响）。"""
    monkeypatch.delenv("HASHMM_MULTI_TENANT", raising=False)
    from hashmm import tenancy as T
    db = _fresh_db(tmp_db)
    ids = _mk_users(db)
    assert T.multi_tenant_enabled() is False
    assert T.resolve_tenant(db, ids["alice"]) == "default"
    assert T.resolve_tenant(db, None) == "default"
    assert T.check_quota(db, "default", add_tokens=10**9).get("allowed") is True


def test_enabled_create_assign_isolate(tmp_db, monkeypatch):
    """开启后：创建租户/分配用户/按租户解析/隔离（A 解析不到 B）。"""
    from hashmm import tenancy as T
    db = _fresh_db(tmp_db)
    ids = _mk_users(db)
    monkeypatch.setenv("HASHMM_MULTI_TENANT", "1")
    assert T.multi_tenant_enabled() is True
    T.create_tenant(db, "tenant_a", name="A", monthly_token_quota=1000)
    T.create_tenant(db, "tenant_b", name="B", monthly_token_quota=5000)
    T.assign_user(db, ids["alice"], "tenant_a")
    T.assign_user(db, ids["bob"], "tenant_b")
    assert T.resolve_tenant(db, ids["alice"]) == "tenant_a"
    assert T.resolve_tenant(db, ids["bob"]) == "tenant_b"
    assert T.resolve_tenant(db, ids["alice"]) != "tenant_b"   # 隔离
    assert T.resolve_tenant(db, "ghost") == "default"          # 未知用户兜底


def test_quota_deduction_and_reject(tmp_db, monkeypatch):
    """配额扣减正确、超限拒绝、租户间独立。"""
    from hashmm import tenancy as T
    db = _fresh_db(tmp_db)
    monkeypatch.setenv("HASHMM_MULTI_TENANT", "1")
    T.create_tenant(db, "t_a", name="A", monthly_token_quota=1000)
    T.create_tenant(db, "t_b", name="B", monthly_token_quota=5000)
    assert T.check_quota(db, "t_a", add_tokens=600).get("allowed") is True
    assert T.check_quota(db, "t_a", add_tokens=600).get("allowed") is False   # 累计超 1000
    assert T.check_quota(db, "t_b", add_tokens=600).get("allowed") is True    # B 独立


def test_reversible(tmp_db, monkeypatch):
    """关回单租户后行为完全恢复（可逆）。"""
    from hashmm import tenancy as T
    db = _fresh_db(tmp_db)
    ids = _mk_users(db)
    monkeypatch.setenv("HASHMM_MULTI_TENANT", "1")
    T.create_tenant(db, "t_a", name="A", monthly_token_quota=1000)
    T.assign_user(db, ids["alice"], "t_a")
    assert T.resolve_tenant(db, ids["alice"]) == "t_a"
    monkeypatch.delenv("HASHMM_MULTI_TENANT", raising=False)
    assert T.resolve_tenant(db, ids["alice"]) == "default"
