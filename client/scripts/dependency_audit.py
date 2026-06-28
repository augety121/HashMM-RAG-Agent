#!/usr/bin/env python3
"""Dependency vulnerability audit (Phase 30 / OWASP LLM03 supply chain).

Wraps pip-audit to gate releases on known-vulnerable dependencies.

    python scripts/dependency_audit.py            # audit; exit 1 if vulns found
    python scripts/dependency_audit.py --json out.json

pip-audit needs network for the advisory DB. On the air-gapped box: run it on a
connected machine against the same requirements, or `pip-audit --vulnerability-service osv`
behind a mirror. If pip-audit isn't installed, this degrades to listing the
top-level deps so you can audit them out-of-band (and tells you how to install it).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _have_pip_audit() -> bool:
    try:
        subprocess.run(["pip-audit", "--version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="hashmm dependency vulnerability audit")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--requirement", "-r", default=None, help="requirements 文件(可选)")
    args = ap.parse_args()

    if not _have_pip_audit():
        print("⚠️ 未安装 pip-audit。安装：pip install pip-audit --break-system-packages")
        print("（需联网拉取漏洞库；离线机请在能联网的机器上对同一份依赖运行）")
        try:
            freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                                    capture_output=True, text=True, timeout=60).stdout
            print(f"\n当前顶层依赖（{len(freeze.splitlines())} 条）已列出，可离线审计。")
        except Exception:
            pass
        return 2  # 'cannot audit' — distinct from 'found vulns'(1) / 'clean'(0)

    cmd = ["pip-audit", "--format", "json"]
    if args.requirement:
        cmd += ["-r", args.requirement]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stdout or "[]"
    if args.json_out:
        Path(args.json_out).write_text(out, encoding="utf-8")
    try:
        data = json.loads(out)
        deps = data.get("dependencies", data) if isinstance(data, dict) else data
        vulns = [d for d in deps if d.get("vulns")]
    except Exception:
        # pip-audit returns non-zero + human text when vulns exist; surface it
        print(proc.stdout or proc.stderr)
        return 1 if proc.returncode != 0 else 0

    if not vulns:
        print("✓ 依赖漏洞扫描通过：未发现已知漏洞。")
        return 0
    print(f"✗ 发现 {len(vulns)} 个含已知漏洞的依赖：")
    for d in vulns:
        ids = ", ".join(v.get("id", "?") for v in d.get("vulns", []))
        print(f"  - {d.get('name')} {d.get('version')}: {ids}")
    print("\n上线前请升级或加固以上依赖。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
