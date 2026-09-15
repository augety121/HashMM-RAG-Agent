#!/usr/bin/env python3
"""Render coturn config without printing or committing the shared secret."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "turn" / "turnserver.conf.template"
DEFAULT_OUT = ROOT / "deploy" / "turn" / "runtime" / "turnserver.conf"


def render(template: str, *, secret: str, realm: str, external_ip: str, cert: str, pkey: str) -> str:
    if len(secret) < 32:
        raise ValueError("HASHMM_TURN_SHARED_SECRET must be at least 32 characters")
    values = {"__HASHMM_TURN_SHARED_SECRET__": secret, "__HASHMM_TURN_REALM__": realm,
              "__HASHMM_TURN_EXTERNAL_IP__": external_ip, "__HASHMM_TURN_CERT__": cert,
              "__HASHMM_TURN_PKEY__": pkey}
    for value in values.values():
        if not value or any(ch in value for ch in "\r\n\x00"):
            raise ValueError("TURN config values must be non-empty single-line strings")
    for marker, value in values.items():
        template = template.replace(marker, value)
    if "__HASHMM_" in template:
        raise ValueError("unresolved TURN template marker")
    return template


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--realm", required=True); parser.add_argument("--external-ip", required=True)
    parser.add_argument("--cert", required=True); parser.add_argument("--pkey", required=True)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    secret = os.environ.get("HASHMM_TURN_SHARED_SECRET", "")
    content = render(TEMPLATE.read_text(encoding="utf-8"), secret=secret, realm=args.realm,
                     external_ip=args.external_ip, cert=args.cert, pkey=args.pkey)
    out = Path(args.out).resolve(); allowed = (ROOT / "deploy" / "turn").resolve()
    if allowed not in out.parents:
        raise SystemExit("output must stay under deploy/turn")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    try: os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError: pass
    os.replace(tmp, out)
    try: os.chmod(out, stat.S_IRUSR | stat.S_IWUSR)
    except OSError: pass
    print(f"rendered {out} (secret not printed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

