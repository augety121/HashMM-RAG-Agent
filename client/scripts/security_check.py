#!/usr/bin/env python3
"""Pre-deploy security gate (Phase 26 / B4).

The server warns about insecure defaults on every startup (default JWT secret,
default admin password admin123, unrestricted CORS). For an enterprise deploy
those are hard red lines. This gate turns the warning into an enforceable check:

    python scripts/security_check.py            # audit; exit 1 if any issue
    python scripts/security_check.py --print-env # print ready-to-paste exports

Wire it into CI / a pre-deploy step so a release with default secrets cannot ship.
"""
import argparse
import secrets
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        _reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hashmm.api.security import audit_for_deploy  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="hashmm pre-deploy security gate")
    ap.add_argument("--print-env", action="store_true",
                    help="打印可直接粘贴的安全环境变量(含新生成的随机密钥)")
    args = ap.parse_args()

    if args.print_env:
        print(f"export HASHMM_JWT_SECRET='{secrets.token_urlsafe(32)}'")
        print("export HASHMM_CORS_ORIGINS='https://你的前端域名'")
        print("export HASHMM_ENV='production'")
        print("# 然后登录后台修改 admin 默认密码 admin123")
        return 0

    issues = audit_for_deploy()
    if not issues:
        print("✓ 安全基线通过：JWT 密钥已设置、admin 非默认密码、CORS 已限制。可上线。")
        return 0

    crit = sum(1 for i in issues if i["severity"] == "critical")
    print(f"✗ 安全基线未通过：{len(issues)} 项问题（critical {crit}）\n")
    for i, it in enumerate(issues, 1):
        print(f"  {i}. [{it['severity'].upper()}] {it['msg']}")
        print(f"     修复：{it['fix']}")
    print("\n上线前必须修复以上问题（或显式承担风险）。可先跑：python scripts/security_check.py --print-env")
    return 1


if __name__ == "__main__":
    sys.exit(main())
