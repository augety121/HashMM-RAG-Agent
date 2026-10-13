"""Remote transport policy shared by WebSocket signaling and HTTP relay."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import ipaddress
from urllib.parse import urlparse
from typing import Any


DEFAULT_STUN = ({"urls": "stun:stun.l.google.com:19302"},)
REMOTE_PROTOCOL = "hashmm.remote.v4"
REMOTE_BOOTSTRAP_SCHEMA = "hashmm.remote-bootstrap.v4"
LEGACY_REMOTE_PROTOCOL = "hashmm.remote.v3"
LEGACY_REMOTE_BOOTSTRAP_SCHEMA = "hashmm.remote-bootstrap.v3"

REMOTE_TIME_BUDGETS = {
    "offer_answer_ms": 2_000,
    "path_selection_ms": 5_000,
    "first_frame_ms": 8_000,
    "migration_ms": 6_000,
    "compat_first_frame_ms": 8_000,
}


def secure_remote_required() -> bool:
    return os.environ.get("HASHMM_REQUIRE_SECURE_REMOTE", "").lower() in ("1", "true", "yes", "on")


def turn_required() -> bool:
    return os.environ.get("HASHMM_REMOTE_REQUIRE_TURN", "").lower() in ("1", "true", "yes", "on")


def _headers_from_scope(scope: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_value in scope.get("headers") or ():
        try:
            result[raw_key.decode("latin1").lower()] = raw_value.decode("latin1")
        except Exception:
            continue
    return result


def _bounded_header(value: Any, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def assess_transport_scope(scope: dict[str, Any]) -> dict[str, Any]:
    """Create one non-spoofable transport verdict for HTTP and WebSocket routes.

    Uvicorn may already have converted the ASGI scheme after validating its
    configured proxy peer.  When it has not, forwarded protocol is accepted
    only from an explicitly trusted immediate peer.  Cloudflare metadata is
    retained for diagnostics, never used as an authentication credential.
    """
    headers = _headers_from_scope(scope)
    scheme = _bounded_header(scope.get("scheme"), 16).lower()
    client = scope.get("client") or ("", 0)
    peer = _bounded_header(client[0] if isinstance(client, (tuple, list)) and client else "", 96)
    forwarded = _bounded_header(headers.get("x-forwarded-proto"), 32).split(",", 1)[0].strip().lower()
    cf_ray = _bounded_header(headers.get("cf-ray"), 96)
    cf_connecting = _bounded_header(headers.get("cf-connecting-ip"), 96)
    trusted_peer = _trusted_proxy(peer)
    if scheme in ("https", "wss"):
        secure, source = True, "asgi-secure-scheme"
    elif trusted_peer and forwarded in ("https", "wss"):
        secure, source = True, "trusted-forwarded-proto"
    else:
        secure = False
        source = "forwarded-proto-insecure" if forwarded else "secure-protocol-unverified"
    return {
        "secure": secure,
        "source": source,
        "asgi_scheme": scheme,
        "forwarded_proto": forwarded,
        "trusted_proxy": trusted_peer,
        "peer_class": "trusted_proxy" if trusted_peer else "client_or_untrusted_proxy",
        "cloudflare": bool(cf_ray and cf_connecting),
        "cf_ray": cf_ray,
    }


class VerifiedTransportMiddleware:
    """Attach a single verified transport verdict to HTTP and WS scopes."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") in ("http", "websocket"):
            state = scope.setdefault("state", {})
            state["transport_security"] = assess_transport_scope(scope)
        await self.app(scope, receive, send)


def request_security_context(request: Any) -> dict[str, Any]:
    try:
        state = getattr(request, "state", None)
        context = getattr(state, "transport_security", None) if state is not None else None
        if isinstance(context, dict):
            return dict(context)
        scope = dict(getattr(request, "scope", {}) or {})
        if not scope:
            headers_obj = getattr(request, "headers", {}) or {}
            encoded_headers = []
            for key, value in dict(headers_obj).items():
                encoded_headers.append((str(key).lower().encode("latin1"), str(value).encode("latin1")))
            client = getattr(request, "client", None)
            scope = {
                "scheme": str(getattr(getattr(request, "url", None), "scheme", "") or ""),
                "client": (str(getattr(client, "host", "") or ""), int(getattr(client, "port", 0) or 0)),
                "headers": encoded_headers,
            }
        return assess_transport_scope(scope)
    except Exception:
        return {"secure": False, "source": "security-context-error", "asgi_scheme": "",
                "forwarded_proto": "", "trusted_proxy": False, "peer_class": "unknown",
                "cloudflare": False, "cf_ray": ""}


def request_is_secure(request: Any) -> bool:
    """Determine security without trusting spoofable proxy headers.

    Forwarded protocol is authoritative only when the immediate peer belongs
    to ``HASHMM_TRUSTED_PROXIES``. Loopback is trusted by default; remote proxy
    networks must be listed explicitly as comma-separated IPs/CIDRs.
    """
    return bool(request_security_context(request).get("secure"))


def _trusted_proxy(peer: str) -> bool:
    if not peer:
        return False
    raw = os.environ.get("HASHMM_TRUSTED_PROXIES", "127.0.0.1/32,::1/128")
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for item in raw.split(",")[:64]:
        try:
            network = ipaddress.ip_network(item.strip(), strict=False)
        except ValueError:
            continue
        if address in network:
            return True
    return False


def remote_bootstrap_config(public_url: str | None = None, *,
                            protocol: str = REMOTE_PROTOCOL) -> dict[str, Any]:
    """Return the canonical authenticated control-plane endpoints.

    Clients must consume this response and must not derive a WebSocket URL
    from a cached or local backend address.
    """
    raw = str(public_url if public_url is not None else os.environ.get("HASHMM_PUBLIC_URL", "")).strip().rstrip("/")
    parsed = urlparse(raw)
    production = os.environ.get("HASHMM_ENV", "").strip().lower() in {"prod", "production"}
    if parsed.scheme not in ({"https"} if production or secure_remote_required() else {"http", "https"}) or not parsed.netloc:
        raise ValueError("canonical_public_https_url_missing")
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    api_base = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    current = protocol == REMOTE_PROTOCOL
    wire_protocol = REMOTE_PROTOCOL if current else LEGACY_REMOTE_PROTOCOL
    schema = REMOTE_BOOTSTRAP_SCHEMA if current else LEGACY_REMOTE_BOOTSTRAP_SCHEMA
    wire_version = "v4" if current else "v3"
    control_wss = f"{ws_scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/api/remote/{wire_version}/ws"
    turn_status = turn_configuration_status()
    normalized = json.dumps({
        "api_base": api_base,
        "control_wss": control_wss,
        "protocol": wire_protocol,
        "turn": turn_status,
    }, sort_keys=True, separators=(",", ":"))
    return {
        "schema": schema,
        "protocol": wire_protocol,
        "api_base": api_base,
        "control_wss": control_wss,
        "edge_mode": str(os.environ.get("HASHMM_REMOTE_EDGE_MODE", "cloudflare-tunnel") or "cloudflare-tunnel")[:40],
        "transport_policy": "direct-turn-migrate-compat-preview" if current else "direct-turn-compat",
        "ice_policy": {
            "direct": True,
            "turn_available": bool(turn_status.get("configured")),
            "compat_https": True,
            "compat_role": "diagnostic_preview",
            "parallel_gathering": current,
        },
        "time_budgets": dict(REMOTE_TIME_BUDGETS) if current else {},
        "terminal_error_required": current,
        "config_revision": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16],
    }


def load_ice_servers(raw: str | None = None) -> list[dict[str, Any]]:
    """Parse a bounded RTCIceServer list and reject incomplete TURN credentials."""
    value = os.environ.get("HASHMM_ICE_SERVERS", "") if raw is None else raw
    if not str(value).strip():
        return [dict(item) for item in DEFAULT_STUN]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return [dict(item) for item in DEFAULT_STUN]
    if not isinstance(parsed, list):
        return [dict(item) for item in DEFAULT_STUN]

    result: list[dict[str, Any]] = []
    for item in parsed[:16]:
        if isinstance(item, str):
            urls = [item]
            source: dict[str, Any] = {"urls": item}
        elif isinstance(item, dict):
            raw_urls = item.get("urls")
            urls = [raw_urls] if isinstance(raw_urls, str) else raw_urls if isinstance(raw_urls, list) else []
            source = item
        else:
            continue
        urls = [str(url).strip() for url in urls[:8] if isinstance(url, str)]
        if not urls or any(not url.startswith(("stun:", "stuns:", "turn:", "turns:")) for url in urls):
            continue
        needs_credentials = any(url.startswith(("turn:", "turns:")) for url in urls)
        username = str(source.get("username") or "")[:256]
        credential = str(source.get("credential") or "")[:1024]
        if needs_credentials and (not username or not credential):
            continue
        clean: dict[str, Any] = {"urls": urls[0] if len(urls) == 1 else urls}
        if username:
            clean["username"] = username
        if credential:
            clean["credential"] = credential
        result.append(clean)
    return result or [dict(item) for item in DEFAULT_STUN]


def _turn_urls(raw: str | None = None) -> list[str]:
    value = os.environ.get("HASHMM_TURN_URLS", "") if raw is None else raw
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        values = parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        values = text.split(",")
    urls: list[str] = []
    for item in values[:8]:
        url = str(item).strip()
        if url.startswith(("turn:", "turns:")) and len(url) <= 512:
            urls.append(url)
    return urls


def dynamic_turn_configured() -> bool:
    secret = os.environ.get("HASHMM_TURN_SHARED_SECRET", "")
    return len(secret) >= 32 and bool(_turn_urls())


def issue_ice_servers(uid: str, device_id: str, *, now: int | None = None) -> list[dict[str, Any]]:
    """Return authenticated ICE config with coturn REST-API credentials.

    Coturn's REST mode expects ``expiry:username`` and a base64 HMAC-SHA1
    password.  The durable account/device identifiers are replaced by a short
    keyed pseudonym and the shared secret never leaves the backend.
    """
    base = load_ice_servers()
    secret = os.environ.get("HASHMM_TURN_SHARED_SECRET", "")
    urls = _turn_urls()
    if len(secret) < 32 or not urls:
        return base
    try: ttl = max(300, min(int(os.environ.get("HASHMM_TURN_CREDENTIAL_TTL", "3600")), 86400))
    except ValueError: ttl = 3600
    issued = int(time.time()) if now is None else int(now)
    opaque = hmac.new(secret.encode("utf-8"), f"{uid}\0{device_id}".encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    username = f"{issued + ttl}:{opaque}"
    credential = base64.b64encode(
        hmac.new(secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")
    dynamic = {"urls": urls[0] if len(urls) == 1 else urls, "username": username, "credential": credential}
    # Keep STUN and explicitly configured static ICE entries, but avoid adding
    # the same TURN URL twice when an operator migrates from long-lived creds.
    filtered: list[dict[str, Any]] = []
    dynamic_set = set(urls)
    for item in base:
        raw_urls = item.get("urls")
        item_urls = [raw_urls] if isinstance(raw_urls, str) else raw_urls if isinstance(raw_urls, list) else []
        if any(str(url) in dynamic_set and str(url).startswith(("turn:", "turns:")) for url in item_urls):
            continue
        filtered.append(item)
    return filtered + [dynamic]


def turn_configuration_status() -> dict[str, Any]:
    static_turn = any(
        str(url).startswith(("turn:", "turns:"))
        for item in load_ice_servers()
        for url in ([item.get("urls")] if isinstance(item.get("urls"), str) else item.get("urls") or [])
    )
    return {
        "required": turn_required(),
        "dynamic_credentials": dynamic_turn_configured(),
        "static_credentials": static_turn,
        "configured": dynamic_turn_configured() or static_turn,
        "urls": len(_turn_urls()),
    }
