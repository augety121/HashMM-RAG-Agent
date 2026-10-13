#!/usr/bin/env python3
"""Run and evaluate real HashMM remote-network acceptance measurements.

Examples:
  python scripts/remote-acceptance.py probe --device-id office-pc --role host \
      --backend https://hashmm.example.com --turn turn.example.com --out pc.json
  python scripts/remote-acceptance.py probe --device-id phone-01 --role viewer \
      --backend https://hashmm.example.com --turn turn.example.com --out phone.json
  python scripts/remote-acceptance.py evaluate-network --device pc.json --device phone.json \
      --audit remote-audit.json --out network-result.json
  python scripts/remote-acceptance.py soak --backend https://hashmm.example.com \
      --token-env HASHMM_ACCEPTANCE_TOKEN --duration 86400 --interval 60 --out soak.jsonl \
      --result soak-result.json

Probe connectivity is not a TURN allocation proof.  Network acceptance also
requires an actual HashMM session whose host and viewer both reported a selected
``relay`` ICE candidate in the persistent remote audit.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import struct
import sys
import time
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hashmm.evaluation.remote_acceptance import evaluate_network_acceptance, evaluate_soak_acceptance


MAGIC = 0x2112A442


def _write_json(path: str, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path == "-":
        sys.stdout.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8")


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _stun_binding(host: str, port: int, sock: socket.socket, timeout: float) -> tuple[str, int] | None:
    txid = secrets.token_bytes(12)
    packet = struct.pack("!HHI12s", 0x0001, 0, MAGIC, txid)
    addresses = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
    if not addresses:
        return None
    sock.settimeout(timeout)
    sock.sendto(packet, addresses[0][4])
    data, _ = sock.recvfrom(2048)
    if len(data) < 20:
        return None
    msg_type, length, cookie, response_txid = struct.unpack("!HHI12s", data[:20])
    if msg_type != 0x0101 or cookie != MAGIC or response_txid != txid:
        return None
    end, offset = min(len(data), 20 + length), 20
    while offset + 4 <= end:
        kind, size = struct.unpack("!HH", data[offset:offset + 4])
        value = data[offset + 4:offset + 4 + size]
        if kind in (0x0020, 0x0001) and len(value) >= 8 and value[1] == 0x01:
            port_n = struct.unpack("!H", value[2:4])[0]
            addr = value[4:8]
            if kind == 0x0020:
                port_n ^= MAGIC >> 16
                mask = struct.pack("!I", MAGIC)
                addr = bytes(a ^ b for a, b in zip(addr, mask))
            return socket.inet_ntoa(addr), port_n
        offset += 4 + ((size + 3) // 4) * 4
    return None


def _nat_probe(servers: list[str], timeout: float) -> dict[str, Any]:
    mapped: list[tuple[str, int]] = []
    failures: list[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("0.0.0.0", 0))
        for entry in servers:
            host, port_s = entry.rsplit(":", 1)
            try:
                result = _stun_binding(host, int(port_s), sock, timeout)
                if result:
                    mapped.append(result)
                else:
                    failures.append(entry)
            except Exception:
                failures.append(entry)
    unique = set(mapped)
    mapping = "unknown"
    if len(mapped) >= 2:
        mapping = "address_and_port_dependent" if len(unique) > 1 else "endpoint_independent_or_unknown"
    return {"nat_mapping": mapping, "stun_responses": len(mapped), "stun_failures": failures}


def _tcp_tls(host: str, port: int, timeout: float) -> bool:
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                return True
    except Exception:
        return False


def _turn_udp(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("0.0.0.0", 0))
            return _stun_binding(host, port, sock, timeout) is not None
    except Exception:
        return False


def _get_json(url: str, timeout: float, token: str = "") -> dict[str, Any] | None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with urlopen(Request(url, headers=headers), timeout=timeout) as response:
            if not 200 <= int(response.status) < 300:
                return None
            value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else None
    except Exception:
        return None


def _backend_secure(url: str, timeout: float, token: str = "") -> bool:
    if not url.lower().startswith("https://"):
        return False
    return _get_json(url.rstrip("/") + "/api/health", timeout, token) is not None


def _remote_relay_state(backend: str, token: str, timeout: float, since_at: float) -> tuple[bool, bool]:
    if not backend.lower().startswith("https://") or not token:
        return False, False
    endpoint = backend.rstrip("/") + f"/api/remote/audit?since={max(0.0, since_at):.6f}&limit=500"
    payload = _get_json(endpoint, timeout, token)
    if payload is None or payload.get("schema") != "hashmm.remote.owner-audit.v1":
        return False, False
    relay_by_session: dict[str, set[str]] = {}
    for event in payload.get("events", []):
        if not isinstance(event, dict) or event.get("event") != "transport_observed":
            continue
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        if str(detail.get("candidate_type") or "").lower() != "relay":
            continue
        session_id = str(event.get("session_id") or "")
        role = str(detail.get("role") or "")
        if session_id and role in {"host", "viewer"}:
            relay_by_session.setdefault(session_id, set()).add(role)
    return True, any(roles == {"host", "viewer"} for roles in relay_by_session.values())


def cmd_probe(args: argparse.Namespace) -> int:
    nat = _nat_probe(args.stun, args.timeout)
    token = os.environ.get(args.token_env, "") if args.token_env else ""
    report = {
        "schema": "hashmm.remote.device-probe.v1", "measured_at": time.time(),
        "device_id": args.device_id, "role": args.role,
        "backend_secure": _backend_secure(args.backend, args.timeout, token),
        "turn_udp_reachable": _turn_udp(args.turn, args.turn_port, args.timeout),
        "turn_tls_reachable": _tcp_tls(args.turn, args.turn_tls_port, args.timeout),
        **nat,
    }
    _write_json(args.out, report)
    return 0 if report["backend_secure"] else 2


def cmd_evaluate_network(args: argparse.Namespace) -> int:
    devices = [_read_json(path) for path in args.device]
    audit_raw = _read_json(args.audit)
    events = audit_raw.get("events", []) if isinstance(audit_raw, dict) else audit_raw
    result = evaluate_network_acceptance(devices, events)
    _write_json(args.out, result)
    if args.record_owner:
        _record(args.record_owner, "network", result, min((d.get("measured_at", time.time()) for d in devices), default=time.time()),
                time.time(), result.get("device_count", 0))
    return 0 if result["status"] == "passed" else 3


def _sample(backend: str, token: str, timeout: float) -> dict[str, Any]:
    started = time.time()
    health_ok = _backend_secure(backend, timeout, token)
    control_plane_ok, relay_ok = _remote_relay_state(backend, token, timeout, started - 180)
    return {
        "at": started, "ok": health_ok and control_plane_ok and relay_ok,
        "health_ok": health_ok, "control_plane_ok": control_plane_ok, "relay_ok": relay_ok,
        "latency_ms": round((time.time() - started) * 1000, 3),
    }


def cmd_soak(args: argparse.Namespace) -> int:
    token = os.environ.get(args.token_env, "")
    if not token:
        raise SystemExit(f"missing token in environment variable {args.token_env}")
    started = time.time()
    deadline = started + args.duration
    samples: list[dict[str, Any]] = []
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        while time.time() < deadline:
            row = _sample(args.backend, token, args.timeout)
            samples.append(row)
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            remaining = deadline - time.time()
            if remaining > 0:
                time.sleep(min(args.interval, remaining))
    finished = time.time()
    result = evaluate_soak_acceptance(samples, started_at=started, finished_at=finished,
                                      minimum_duration=max(86_400, args.duration),
                                      minimum_availability_pct=args.availability,
                                      maximum_consecutive_failures=args.max_streak)
    _write_json(args.result, result)
    if args.record_owner:
        _record(args.record_owner, "soak", result, started, finished, 1)
    return 0 if result["status"] == "passed" else 4


def cmd_evaluate_soak(args: argparse.Namespace) -> int:
    samples = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line.strip()]
    started = args.started_at if args.started_at is not None else (samples[0]["at"] if samples else 0)
    finished = args.finished_at if args.finished_at is not None else (samples[-1]["at"] if samples else 0)
    result = evaluate_soak_acceptance(samples, started_at=started, finished_at=finished,
                                      minimum_duration=max(86_400, args.duration),
                                      minimum_availability_pct=args.availability,
                                      maximum_consecutive_failures=args.max_streak)
    _write_json(args.out, result)
    if args.record_owner:
        _record(args.record_owner, "soak", result, started, finished, 1)
    return 0 if result["status"] == "passed" else 4


def _record(owner: str, kind: str, result: dict[str, Any], started: float, finished: float, devices: int) -> None:
    from hashmm.api import database
    from hashmm.api.remote_persistence import remote_persistence
    database.init_db()
    remote_persistence.initialize()
    remote_persistence.record_acceptance(owner, kind, str(result.get("status") or "incomplete"),
                                         started, finished, devices, result)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="HashMM remote acceptance")
    commands = root.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--device-id", required=True); probe.add_argument("--role", choices=("host", "viewer"), required=True)
    probe.add_argument("--backend", required=True); probe.add_argument("--turn", required=True)
    probe.add_argument("--turn-port", type=int, default=3478); probe.add_argument("--turn-tls-port", type=int, default=5349)
    probe.add_argument("--stun", action="append", default=["stun.l.google.com:19302", "stun1.l.google.com:19302"])
    probe.add_argument("--timeout", type=float, default=5.0); probe.add_argument("--token-env", default="")
    probe.add_argument("--out", default="-"); probe.set_defaults(func=cmd_probe)

    network = commands.add_parser("evaluate-network")
    network.add_argument("--device", action="append", required=True); network.add_argument("--audit", required=True)
    network.add_argument("--out", default="-"); network.add_argument("--record-owner", default="")
    network.set_defaults(func=cmd_evaluate_network)

    soak = commands.add_parser("soak")
    soak.add_argument("--backend", required=True); soak.add_argument("--token-env", default="HASHMM_ACCEPTANCE_TOKEN")
    soak.add_argument("--duration", type=int, default=86400); soak.add_argument("--interval", type=float, default=60)
    soak.add_argument("--timeout", type=float, default=10); soak.add_argument("--availability", type=float, default=99.5)
    soak.add_argument("--max-streak", type=int, default=3); soak.add_argument("--out", default="remote-soak.jsonl")
    soak.add_argument("--result", default="remote-soak-result.json"); soak.add_argument("--record-owner", default="")
    soak.set_defaults(func=cmd_soak)

    evaluate = commands.add_parser("evaluate-soak")
    evaluate.add_argument("--input", required=True); evaluate.add_argument("--started-at", type=float)
    evaluate.add_argument("--finished-at", type=float); evaluate.add_argument("--duration", type=int, default=86400)
    evaluate.add_argument("--availability", type=float, default=99.5); evaluate.add_argument("--max-streak", type=int, default=3)
    evaluate.add_argument("--out", default="-"); evaluate.add_argument("--record-owner", default="")
    evaluate.set_defaults(func=cmd_evaluate_soak)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
