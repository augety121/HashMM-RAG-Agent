#!/usr/bin/env python3
"""一键安全初始化 —— 消除启动时的两条安全警告（MASTER_PLAN 第 0 步）。

服务每次启动都会警告：
  1. JWT 密钥仍是默认值（任何人都能伪造登录令牌）；
  2. admin 仍在用默认密码 admin123。

这两件是上线/演示前必须做的「零风险、立刻该做」项。本工具帮你一次搞定，
不用记命令、不用手动算密钥。

用法（真机，项目根 /root/autodl-tmp 下）：

    # 1) 看当前安全状态 + 生成一个强 JWT 密钥（不改任何东西，只打印）
    python -m hashmm.tools.secure_setup

    # 2) 改 admin 密码（会写 DB；需验证旧密码，避免误改）
    python -m hashmm.tools.secure_setup --change-admin-password \
        --username admin --old-password admin123 --new-password 'your_strong_pw'

    # 3) 只生成并打印一条可直接用的启动命令（带 JWT 密钥）
    python -m hashmm.tools.secure_setup --print-launch

说明：
  - JWT 密钥**不写文件**（密钥落盘本身是风险）——本工具生成强密钥后，告诉你怎么在启动
    命令里 `export HASHMM_JWT_SECRET=...`，由你决定持久化方式（写进你的启动脚本/环境）。
  - 改密码会**真的写 DB**，所以必须显式带 `--change-admin-password` 且通过旧密码验证；
    不带参数时只读、只打印，绝不偷偷改你的库。
  - 永不抛错地引导，不替你做你没明确要求的事。
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _gen_secret() -> str:
    return secrets.token_urlsafe(32)


def _status():
    from hashmm.api import security as sec
    issues = sec.run_startup_security_check()
    if not issues:
        print("\033[32m✔ 安全检查通过：没有发现默认密钥/默认密码问题。\033[0m")
    else:
        print(f"\033[33m⚠ 当前有 {len(issues)} 个安全问题：\033[0m")
        for i, m in enumerate(issues, 1):
            # 只显示第一句，避免把内置的随机示例密钥也打出来误导
            print(f"  {i}. {m.split('。')[0]}。")
    return issues


def main():
    ap = argparse.ArgumentParser(description="一键安全初始化")
    ap.add_argument("--change-admin-password", action="store_true",
                    help="改 admin 密码（会写 DB，需 --old-password/--new-password）")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--old-password", default="")
    ap.add_argument("--new-password", default="")
    ap.add_argument("--print-launch", action="store_true",
                    help="打印一条带新 JWT 密钥的启动命令")
    args = ap.parse_args()

    print("=" * 60)
    print("  HashMM 安全初始化")
    print("=" * 60)

    # ── 当前状态 ──
    try:
        _status()
    except Exception as e:
        print(f"（安全状态读取失败：{type(e).__name__}: {e}）")

    # ── 改密码（仅在显式要求时）──
    if args.change_admin_password:
        if not args.old_password or not args.new_password:
            print("\n\033[31m✗ 改密码需要同时提供 --old-password 和 --new-password。\033[0m")
            sys.exit(2)
        if args.new_password == "admin123":
            print("\n\033[31m✗ 新密码不能还是 admin123。\033[0m")
            sys.exit(2)
        if len(args.new_password) < 8:
            print("\n\033[31m✗ 新密码太短（建议 ≥ 8 位）。\033[0m")
            sys.exit(2)
        from hashmm.api import database as db
        user = db.verify_user(args.username, args.old_password)
        if not user:
            print(f"\n\033[31m✗ 旧密码验证失败（用户 {args.username}）——未做任何修改。\033[0m")
            sys.exit(1)
        ok = db.change_password(user["id"], args.new_password)
        if ok:
            print(f"\n\033[32m✔ 已修改 {args.username} 的密码。请用新密码登录。\033[0m")
        else:
            print("\n\033[31m✗ 改密码失败（DB 未变更）。\033[0m")
            sys.exit(1)

    # ── JWT 密钥引导 ──
    cur = os.environ.get("HASHMM_JWT_SECRET", "")
    secure_jwt = bool(cur) and cur != "hashmm-jwt-secret-change-me" and len(cur) >= 16
    print("\n" + "-" * 60)
    if secure_jwt:
        print("\033[32m✔ HASHMM_JWT_SECRET 已设置且足够强。\033[0m")
    else:
        new_secret = _gen_secret()
        print("JWT 密钥未设置/为默认。建议在启动命令前 export 一个强密钥：")
        print(f"\n    export HASHMM_JWT_SECRET='{new_secret}'\n")
        print("（密钥不写文件——由你决定持久化方式，比如写进你的启动脚本）")
        if args.print_launch:
            print("\n可直接用的完整启动命令：\n")
            print(f"    HASHMM_JWT_SECRET='{new_secret}' \\")
            print("    HASHMM_EVAL_EXEC=1 CUDA_VISIBLE_DEVICES=0 \\")
            print("    python -m uvicorn hashmm.api.server:app --host 0.0.0.0 --port 6006\n")

    print("-" * 60)
    print("提示：上线/演示前还可加 HASHMM_ENV=production，此时若仍有默认密钥/密码会拒绝启动。")


if __name__ == "__main__":
    main()
