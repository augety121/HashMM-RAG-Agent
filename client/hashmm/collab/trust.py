"""hashmm/collab/trust.py —— 跨 Agent 协作的信任关系模型（V317）。

对应用户需求：让多个用户（好友 / 同企业）的 agent 互相协作。但协作的**前置是信任**——
一个陌生 agent 绝不能凭空向你的 agent 要数据。所以先有信任关系，才谈协作。

信任模型（两类，都需双向确认）：
  · friend        —— 个人好友：A 发起 → B 接受，建立双向 friend 关系。
  · org_member    —— 同组织成员：由组织 owner/admin 拉入，成员间自动互信（同 org_id）。

安全原则（贯穿始终）：
  1. **双向确认**：好友关系必须双方都同意（不能单方面"加"别人为好友就获得访问权）。
  2. **最小暴露**：信任只是"能发起协作请求"的资格，不等于"能读对方数据"——
     具体每次协作能读什么，由 collab/policy.py 的授权策略逐次裁决（信任≠授权）。
  3. **可撤销**：任一方可随时解除好友 / 组织可移除成员，撤销后立即失效。
  4. **可审计**：所有信任变更落库留痕（collab/audit.py）。

存储：SQLite（bench_home 无关，走业务 DATA_ROOT/collab.sqlite3），进程内不缓存敏感态。
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from pathlib import Path

__all__ = [
    "TrustStore", "get_trust_store",
    "REL_FRIEND", "REL_ORG", "STATUS_PENDING", "STATUS_ACTIVE",
]

REL_FRIEND = "friend"
REL_ORG = "org_member"
STATUS_PENDING = "pending"      # 好友请求已发出，待对方接受
STATUS_ACTIVE = "active"        # 双向确认，生效
STATUS_BLOCKED = "blocked"      # 拉黑


def _db_path() -> Path:
    try:
        from hashmm.api.database import DATA_ROOT
        base = Path(DATA_ROOT)
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("HASHMM_DATA_ROOT", "data"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "collab.sqlite3"


class TrustStore:
    """信任关系存储。所有跨用户协作的守门人。"""

    def __init__(self, db_path: Path | None = None):
        self.path = Path(db_path) if db_path else _db_path()
        self._init_db()

    @contextlib.contextmanager
    def _conn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS trust_edges (
                user_a TEXT NOT NULL, user_b TEXT NOT NULL,
                rel TEXT NOT NULL, status TEXT NOT NULL,
                org_id TEXT DEFAULT '', created REAL, updated REAL,
                PRIMARY KEY (user_a, user_b, rel))""")
            c.execute("""CREATE TABLE IF NOT EXISTS org_members (
                org_id TEXT NOT NULL, user_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member', joined REAL,
                PRIMARY KEY (org_id, user_id))""")

    # ── 好友关系 ──────────────────────────────────────────
    def request_friend(self, requester: str, target: str) -> dict:
        """A 请求加 B 为好友。返回 {ok, status, detail}。"""
        requester, target = str(requester), str(target)
        if not requester or not target:
            return {"ok": False, "detail": "用户ID不能为空"}
        if requester == target:
            return {"ok": False, "detail": "不能加自己为好友"}
        now = time.time()
        with self._conn() as c:
            # 若对方已向我发过请求 → 直接互相确认（双向 active）
            row = c.execute(
                "SELECT status FROM trust_edges WHERE user_a=? AND user_b=? AND rel=?",
                (target, requester, REL_FRIEND)).fetchone()
            if row and row["status"] == STATUS_PENDING:
                self._set_edge(c, requester, target, REL_FRIEND, STATUS_ACTIVE, now)
                self._set_edge(c, target, requester, REL_FRIEND, STATUS_ACTIVE, now)
                return {"ok": True, "status": STATUS_ACTIVE, "detail": "双方互相请求，已成为好友"}
            # 检查是否被对方拉黑
            blk = c.execute(
                "SELECT status FROM trust_edges WHERE user_a=? AND user_b=? AND rel=?",
                (target, requester, REL_FRIEND)).fetchone()
            if blk and blk["status"] == STATUS_BLOCKED:
                return {"ok": False, "detail": "对方已拉黑，无法添加"}
            self._set_edge(c, requester, target, REL_FRIEND, STATUS_PENDING, now)
        return {"ok": True, "status": STATUS_PENDING, "detail": "好友请求已发送，待对方接受"}

    def accept_friend(self, accepter: str, requester: str) -> dict:
        """B 接受 A 的好友请求 → 建立双向 active。"""
        accepter, requester = str(accepter), str(requester)
        now = time.time()
        with self._conn() as c:
            row = c.execute(
                "SELECT status FROM trust_edges WHERE user_a=? AND user_b=? AND rel=?",
                (requester, accepter, REL_FRIEND)).fetchone()
            if not row or row["status"] != STATUS_PENDING:
                return {"ok": False, "detail": "没有待处理的好友请求"}
            self._set_edge(c, requester, accepter, REL_FRIEND, STATUS_ACTIVE, now)
            self._set_edge(c, accepter, requester, REL_FRIEND, STATUS_ACTIVE, now)
        return {"ok": True, "status": STATUS_ACTIVE, "detail": "已成为好友"}

    def remove_friend(self, user: str, other: str) -> dict:
        """解除好友（双向删除）。"""
        with self._conn() as c:
            c.execute("DELETE FROM trust_edges WHERE user_a=? AND user_b=? AND rel=?",
                      (str(user), str(other), REL_FRIEND))
            c.execute("DELETE FROM trust_edges WHERE user_a=? AND user_b=? AND rel=?",
                      (str(other), str(user), REL_FRIEND))
        return {"ok": True, "detail": "已解除好友关系"}

    def block(self, user: str, other: str) -> dict:
        """拉黑：删除现有关系并标记 blocked（阻止再次请求）。"""
        now = time.time()
        with self._conn() as c:
            c.execute("DELETE FROM trust_edges WHERE user_a=? AND user_b=? AND rel=?",
                      (str(other), str(user), REL_FRIEND))
            self._set_edge(c, str(user), str(other), REL_FRIEND, STATUS_BLOCKED, now)
        return {"ok": True, "detail": "已拉黑"}

    # ── 组织成员 ──────────────────────────────────────────
    def add_org_member(self, org_id: str, user_id: str, role: str = "member") -> dict:
        org_id, user_id = str(org_id), str(user_id)
        if not org_id or not user_id:
            return {"ok": False, "detail": "组织ID/用户ID不能为空"}
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO org_members(org_id, user_id, role, joined) "
                      "VALUES(?,?,?,?)", (org_id, user_id, role, time.time()))
        return {"ok": True, "detail": f"已加入组织 {org_id}（角色 {role}）"}

    def remove_org_member(self, org_id: str, user_id: str) -> dict:
        with self._conn() as c:
            c.execute("DELETE FROM org_members WHERE org_id=? AND user_id=?",
                      (str(org_id), str(user_id)))
        return {"ok": True, "detail": "已移出组织"}

    def org_members(self, org_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT user_id, role, joined FROM org_members WHERE org_id=?",
                             (str(org_id),)).fetchall()
        return [dict(r) for r in rows]

    def user_orgs(self, user_id: str) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT org_id FROM org_members WHERE user_id=?",
                             (str(user_id),)).fetchall()
        return [r["org_id"] for r in rows]

    # ── 核心查询：能不能协作 ───────────────────────────────
    def relationship(self, user_a: str, user_b: str) -> dict:
        """判定 A 与 B 的信任关系。返回 {trusted, rel, detail}。

        这是所有跨 agent 协作的**总闸**：trusted=False 时任何协作请求都不该放行。
        """
        user_a, user_b = str(user_a), str(user_b)
        if user_a == user_b:
            return {"trusted": True, "rel": "self", "detail": "同一用户"}
        with self._conn() as c:
            # ① 好友（双向 active）
            fa = c.execute(
                "SELECT status FROM trust_edges WHERE user_a=? AND user_b=? AND rel=?",
                (user_a, user_b, REL_FRIEND)).fetchone()
            fb = c.execute(
                "SELECT status FROM trust_edges WHERE user_a=? AND user_b=? AND rel=?",
                (user_b, user_a, REL_FRIEND)).fetchone()
            if fa and fb and fa["status"] == STATUS_ACTIVE and fb["status"] == STATUS_ACTIVE:
                return {"trusted": True, "rel": REL_FRIEND, "detail": "双向好友"}
            # 被拉黑 → 明确拒绝
            if (fa and fa["status"] == STATUS_BLOCKED) or (fb and fb["status"] == STATUS_BLOCKED):
                return {"trusted": False, "rel": "blocked", "detail": "存在拉黑关系"}
            # ② 同组织
            orgs_a = {r["org_id"] for r in c.execute(
                "SELECT org_id FROM org_members WHERE user_id=?", (user_a,)).fetchall()}
            orgs_b = {r["org_id"] for r in c.execute(
                "SELECT org_id FROM org_members WHERE user_id=?", (user_b,)).fetchall()}
            shared = orgs_a & orgs_b
            if shared:
                return {"trusted": True, "rel": REL_ORG, "detail": f"同属组织 {sorted(shared)[0]}",
                        "org_id": sorted(shared)[0]}
        return {"trusted": False, "rel": "none", "detail": "无信任关系（非好友、非同组织）"}

    def list_friends(self, user_id: str) -> list[dict]:
        """列出用户的好友（含 pending 请求，标注方向）。"""
        user_id = str(user_id)
        out = []
        with self._conn() as c:
            # 已确认的好友
            for r in c.execute(
                "SELECT user_b, status FROM trust_edges WHERE user_a=? AND rel=? AND status=?",
                    (user_id, REL_FRIEND, STATUS_ACTIVE)).fetchall():
                out.append({"user": r["user_b"], "status": STATUS_ACTIVE, "direction": "mutual"})
            # 我发出的待处理
            for r in c.execute(
                "SELECT user_b FROM trust_edges WHERE user_a=? AND rel=? AND status=?",
                    (user_id, REL_FRIEND, STATUS_PENDING)).fetchall():
                out.append({"user": r["user_b"], "status": STATUS_PENDING, "direction": "outgoing"})
            # 别人发给我的待处理
            for r in c.execute(
                "SELECT user_a FROM trust_edges WHERE user_b=? AND rel=? AND status=?",
                    (user_id, REL_FRIEND, STATUS_PENDING)).fetchall():
                out.append({"user": r["user_a"], "status": STATUS_PENDING, "direction": "incoming"})
        return out

    @staticmethod
    def _set_edge(c, a: str, b: str, rel: str, status: str, ts: float) -> None:
        # 保留原 created（若已存在），只更新 status/updated
        row = c.execute("SELECT created FROM trust_edges WHERE user_a=? AND user_b=? AND rel=?",
                        (a, b, rel)).fetchone()
        created = row["created"] if row and row["created"] else ts
        c.execute("INSERT OR REPLACE INTO trust_edges"
                  "(user_a, user_b, rel, status, created, updated) VALUES(?,?,?,?,?,?)",
                  (a, b, rel, status, created, ts))


_STORE: TrustStore | None = None


def get_trust_store(db_path: Path | None = None) -> TrustStore:
    global _STORE
    if db_path is not None:
        return TrustStore(db_path)
    if _STORE is None:
        _STORE = TrustStore()
    return _STORE
