#!/usr/bin/env python3
"""Deterministic contract gate; requires no running server or network access."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"[contracts] FAIL: {message}")


def main() -> int:
    release = json.loads((ROOT / "hashmm" / "release-manifest.json").read_text("utf-8"))
    event = json.loads((ROOT / "contracts" / "hashmm-event-v1.schema.json").read_text("utf-8"))
    asyncapi = json.loads((ROOT / "contracts" / "asyncapi.hashmm.v2802.json").read_text("utf-8"))
    require(event["properties"]["schema"]["const"] == release["protocols"]["event"],
            "event schema differs from release manifest")
    require(asyncapi["info"]["version"] == release["api_version"],
            "AsyncAPI version differs from API release")
    require(asyncapi["asyncapi"].startswith("3."), "AsyncAPI 3.x is required")
    require("workEvents" in asyncapi["channels"] and "remoteSignal" in asyncapi["channels"],
            "work or remote channel is missing")
    runtime = (ROOT / "hashmm" / "agent" / "work_runtime.py").read_text("utf-8")
    route = (ROOT / "hashmm" / "api" / "routes" / "work_runtime.py").read_text("utf-8")
    require(release["protocols"]["event"] in runtime, "runtime does not emit the declared envelope")
    require('"/protocol"' in route and '"authoritative": "server"' in route,
            "protocol discovery route is missing")
    forbidden = {"api_key", "password", "access_token", "refresh_token", "secret"}
    require(not (forbidden & set(event.get("properties", {}))), "event envelope exposes credentials")
    print(f"[contracts] OK: {release['release']} event={release['protocols']['event']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
