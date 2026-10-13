#!/usr/bin/env python3
"""多租户隔离 —— 数据层回归测试（先建测试，后动逻辑）。

按项目铁律：动多租户隔离前，必须先有数据层回归测试兜底。本脚本在一个**全新的临时
sqlite**（tempdir）里建库、造用户、建租户、分配、查配额，验证：

  1. 关闭多租户时（HASHMM_MULTI_TENANT 未开）行为与现状**完全一致**：
     所有用户解析到 default 租户、配额检查恒 allow（单租户部署零影响）。
  2. 开启后：创建租户 / 分配用户 / 按租户解析 / 配额扣减与拒绝 / 默认兜底 都正确。
  3. 隔离：A 租户的用户解析不到 B 租户；未分配用户回落 default。

**绝不触碰真实 data**：整个测试在 mktemp 临时目录里，HASHMM_DB_PATH 指向临时库，
跑完即弃。这就是 MASTER_PLAN 第 5 步「先建数据层回归测试再动」的那一步。

用法（真机或沙箱都能跑，不需要 GPU/模型/真实 data）：
    python -m hashmm.tools.tenancy_regression
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = ""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  \033[32m✔\033[0m {name}")
    else:
        _failed += 1
        print(f"  \033[31m✗\033[0m {name}  {detail}")


def main():
    # ── 关键：在 import database 之前把 DB 指向临时库，彻底隔离真实 data ──
    tmp = tempfile.mkdtemp(prefix="hashmm_tenancy_")
    os.environ["HASHMM_DB_PATH"] = str(Path(tmp) / "test.sqlite")
    os.environ["DATA_DIR"] = tmp
    # 先确保关闭态
    os.environ.pop("HASHMM_MULTI_TENANT", None)

    print("=" * 56)
    print("  多租户隔离 —— 数据层回归测试")
    print("=" * 56)
    print(f"  临时库: {os.environ['HASHMM_DB_PATH']}")
    print(f"  (绝不触碰真实 data)\n")

    from hashmm.api import database as db
    from hashmm import tenancy as T

    db.init_db()

    # 造两个用户，记录它们真实的 id（users 主键是随机 id，不是 username）
    alice_id = bob_id = None
    try:
        a = db.create_user("alice", "pw_a", role="user")
        b = db.create_user("bob", "pw_b", role="user")
        alice_id = a["id"] if a else None
        bob_id = b["id"] if b else None
    except Exception:
        pass
    if not alice_id or not bob_id:
        with db._conn() as c:
            for u in ("alice", "bob"):
                row = c.execute("SELECT id FROM users WHERE username=?", (u,)).fetchone()
                if not row:
                    c.execute("INSERT OR IGNORE INTO users (id, username, password_hash, salt) "
                              "VALUES (?,?,?,?)", (u, u, "x", "x"))
            alice_id = c.execute("SELECT id FROM users WHERE username='alice'").fetchone()["id"]
            bob_id = c.execute("SELECT id FROM users WHERE username='bob'").fetchone()["id"]

    # ── 1. 关闭态：现状不变 ──
    print("[1] 多租户关闭时 —— 行为零变化")
    check("关闭时 multi_tenant_enabled() == False", T.multi_tenant_enabled() is False)
    check("关闭时任何用户都解析到 default",
          T.resolve_tenant(db, alice_id) == "default" and T.resolve_tenant(db, "bob") == "default")
    q = T.check_quota(db, "default", add_tokens=999999)
    check("关闭时配额检查恒 allow（单租户不被限流）", q.get("allowed") is True, str(q))
    check("关闭时 resolve_tenant(None) 兜底 default", T.resolve_tenant(db, None) == "default")

    # ── 2. 开启态：创建/分配/解析/隔离 ──
    print("\n[2] 多租户开启时 —— 创建/分配/解析/隔离")
    os.environ["HASHMM_MULTI_TENANT"] = "1"
    check("开启后 multi_tenant_enabled() == True", T.multi_tenant_enabled() is True)

    T.create_tenant(db, "tenant_a", name="公司A", monthly_token_quota=1000)
    T.create_tenant(db, "tenant_b", name="公司B", monthly_token_quota=5000)
    ta = T.get_tenant(db, "tenant_a")
    check("创建租户A，配额写入正确", int(ta.get("monthly_token_quota", 0)) == 1000, str(ta))

    T.assign_user(db, alice_id, "tenant_a")
    T.assign_user(db, bob_id, "tenant_b")
    check("alice 解析到 tenant_a", T.resolve_tenant(db, alice_id) == "tenant_a")
    check("bob 解析到 tenant_b", T.resolve_tenant(db, bob_id) == "tenant_b")
    check("隔离：alice 不会解析到 tenant_b", T.resolve_tenant(db, alice_id) != "tenant_b")
    check("未分配/未知用户回落 default", T.resolve_tenant(db, "ghost_user") == "default")

    # ── 3. 配额扣减与拒绝 ──
    print("\n[3] 配额扣减与超限拒绝")
    r1 = T.check_quota(db, "tenant_a", add_tokens=600)   # 600/1000 ok
    check("扣 600 token：allowed", r1.get("allowed") is True, str(r1))
    r2 = T.check_quota(db, "tenant_a", add_tokens=600)   # 累计 1200 > 1000 → 拒
    check("再扣 600（累计超 1000）：拒绝", r2.get("allowed") is False, str(r2))
    check("租户B配额独立，不受A影响",
          T.check_quota(db, "tenant_b", add_tokens=600).get("allowed") is True)

    # ── 4. 总览 ──
    print("\n[4] 租户总览")
    ov = T.tenant_overview(db)
    check("overview 报告 enabled=True 且含租户列表",
          ov.get("enabled") is True and ov.get("n_tenants", 0) >= 2, str(ov)[:120])

    # ── 5. 关回去：确认可逆，回到单租户零影响 ──
    print("\n[5] 关回单租户 —— 可逆")
    os.environ.pop("HASHMM_MULTI_TENANT", None)
    check("关回后所有用户又解析到 default（行为可逆）",
          T.resolve_tenant(db, alice_id) == "default")
    check("关回后配额检查又恒 allow",
          T.check_quota(db, "tenant_a", add_tokens=999999).get("allowed") is True)

    # 汇总
    print("\n" + "=" * 56)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 56)
    if _failed == 0:
        print("\033[32m  ✔ 多租户数据层回归全部通过 —— 隔离/配额/可逆性有保障，可在此基础上安全推进。\033[0m")

    # 清理临时目录
    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass

    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
