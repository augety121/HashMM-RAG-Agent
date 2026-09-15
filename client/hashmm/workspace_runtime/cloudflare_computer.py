"""HashMM adapter for the official Cloudflare Computer Worker gateway.

The adapter is deliberately service-to-service.  Renderer and App clients see
provider status and execution receipts, but never the gateway token or the raw
Cloudflare Durable Object identifier.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urlparse

import httpx


PROTOCOL = "hashmm-cloud-workspace/1.0"
PROVIDER_ID = "cloudflare_computer_worker_shell"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024 + 64 * 1024


def _bool_env(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CloudflareComputerConfig:
    enabled: bool
    base_url: str
    token: str
    handle_secret: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "CloudflareComputerConfig":
        try:
            timeout = float(os.environ.get("HASHMM_CLOUDFLARE_COMPUTER_TIMEOUT_SECONDS", "60"))
        except (TypeError, ValueError):
            timeout = 60.0
        return cls(
            enabled=_bool_env("HASHMM_CLOUDFLARE_COMPUTER_ENABLED"),
            base_url=str(os.environ.get("HASHMM_CLOUDFLARE_COMPUTER_URL", "")).strip().rstrip("/"),
            token=str(os.environ.get("HASHMM_CLOUDFLARE_COMPUTER_TOKEN", "")).strip(),
            handle_secret=str(os.environ.get("HASHMM_WORKSPACE_HANDLE_SECRET", "")).strip(),
            timeout_seconds=max(1.0, min(timeout, 120.0)),
        )

    def problems(self) -> list[str]:
        problems: list[str] = []
        if not self.enabled:
            problems.append("disabled")
        if not self.base_url:
            problems.append("missing_url")
        elif not _safe_service_url(self.base_url):
            problems.append("insecure_or_invalid_url")
        if not self.token:
            problems.append("missing_token")
        if len(self.handle_secret.encode("utf-8")) < 32:
            problems.append("missing_or_weak_handle_secret")
        return problems

    @property
    def configured(self) -> bool:
        return not self.problems()

    def public(self) -> dict[str, Any]:
        parsed = urlparse(self.base_url) if self.base_url else None
        endpoint = f"{parsed.scheme}://{parsed.netloc}" if parsed and parsed.scheme and parsed.netloc else ""
        return {
            "id": PROVIDER_ID,
            "label": "Cloudflare Computer",
            "protocol": PROTOCOL,
            "configured": self.configured,
            "enabled": self.enabled,
            "preview": True,
            "endpoint": endpoint,
            "problems": self.problems(),
            "capabilities": ["workspace_files", "worker_shell", "durable_vfs"],
        }


def _safe_service_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.username or parsed.password or not parsed.hostname:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def build_workspace_handle(owner_id: str, workspace_id: str, secret: str) -> str:
    owner = str(owner_id or "").strip()
    workspace = str(workspace_id or "").strip()
    key = str(secret or "").encode("utf-8")
    if not owner or not workspace or len(key) < 32:
        raise ValueError("owner, workspace and a 32-byte handle secret are required")
    digest = hmac.new(key, owner.encode("utf-8") + b"\0" + workspace.encode("utf-8"), hashlib.sha256).digest()
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=").lower()
    return "ws_" + encoded[:40]


class CloudflareComputerError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 502, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


class CloudflareComputerClient:
    def __init__(
        self,
        config: CloudflareComputerConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.config = config or CloudflareComputerConfig.from_env()
        self._transport = transport
        self._client_factory = client_factory

    def status(self) -> dict[str, Any]:
        return self.config.public()

    def health(self) -> dict[str, Any]:
        self._require_configured()
        payload = self._request("GET", "/v1/health", authenticated=False)
        if payload.get("protocol") != PROTOCOL:
            raise CloudflareComputerError("protocol_mismatch", status_code=502)
        return {
            **self.config.public(),
            "reachable": True,
            "ready": bool(payload.get("ready")),
            "upstream": str(payload.get("upstream") or "")[:120],
        }

    def exec(self, owner_id: str, workspace_id: str, argv: list[str], cwd: str = "/workspace") -> dict[str, Any]:
        self._require_configured()
        if not isinstance(argv, list) or not argv or len(argv) > 64:
            raise CloudflareComputerError("invalid_argv", status_code=422)
        if any(not isinstance(arg, str) or not arg or len(arg) > 4096 or "\x00" in arg for arg in argv):
            raise CloudflareComputerError("invalid_argv", status_code=422)
        if not isinstance(cwd, str) or len(cwd) > 512 or (cwd != "/workspace" and not cwd.startswith("/workspace/")):
            raise CloudflareComputerError("invalid_cwd", status_code=422)
        if ".." in cwd.split("/") or "\x00" in cwd or "\\" in cwd:
            raise CloudflareComputerError("invalid_cwd", status_code=422)
        handle = build_workspace_handle(owner_id, workspace_id, self.config.handle_secret)
        payload = self._request("POST", f"/v1/workspaces/{handle}/exec", json_body={"argv": argv, "cwd": cwd})
        stdout = str(payload.get("stdout") or "")
        stderr = str(payload.get("stderr") or "")
        return {
            "provider": PROVIDER_ID,
            "exit_code": int(payload.get("exitCode") or 0),
            "stdout": stdout[: 2 * 1024 * 1024],
            "stderr": stderr[: 2 * 1024 * 1024],
            "truncated": bool(payload.get("truncated")) or len(stdout) > 2 * 1024 * 1024 or len(stderr) > 2 * 1024 * 1024,
        }

    def write_file(self, owner_id: str, workspace_id: str, path: str, content: bytes) -> None:
        self._require_configured()
        safe_path = _file_path(path)
        if len(content) > 8 * 1024 * 1024:
            raise CloudflareComputerError("file_too_large", status_code=413)
        handle = build_workspace_handle(owner_id, workspace_id, self.config.handle_secret)
        self._request_bytes("PUT", f"/v1/workspaces/{handle}/files/{quote(safe_path, safe='/')}", content=content)

    def _require_configured(self) -> None:
        if not self.config.configured:
            raise CloudflareComputerError("provider_not_configured", status_code=503)

    def _headers(self, authenticated: bool) -> dict[str, str]:
        headers = {"accept": "application/json", "user-agent": "HashMM/1.3 CloudWorkspaceRuntime"}
        if authenticated:
            headers["authorization"] = f"Bearer {self.config.token}"
        return headers

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None,
                 authenticated: bool = True) -> dict[str, Any]:
        try:
            with self._client_factory(timeout=self.config.timeout_seconds, transport=self._transport) as client:
                response = client.request(method, self.config.base_url + path, headers=self._headers(authenticated), json=json_body)
        except httpx.TimeoutException as exc:
            raise CloudflareComputerError("upstream_timeout", status_code=504, retryable=False) from exc
        except httpx.HTTPError as exc:
            raise CloudflareComputerError("upstream_unreachable", status_code=502, retryable=True) from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise CloudflareComputerError("upstream_response_too_large", status_code=502)
        if response.status_code >= 400:
            code = "upstream_rejected"
            try:
                raw_code = response.json().get("error")
                if isinstance(raw_code, str) and raw_code.replace("_", "").isalnum():
                    code = raw_code[:80]
            except (ValueError, AttributeError):
                pass
            raise CloudflareComputerError(code, status_code=502, retryable=response.status_code >= 500)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudflareComputerError("invalid_upstream_json", status_code=502) from exc
        if not isinstance(payload, dict):
            raise CloudflareComputerError("invalid_upstream_payload", status_code=502)
        return payload

    def _request_bytes(self, method: str, path: str, *, content: bytes) -> None:
        try:
            with self._client_factory(timeout=self.config.timeout_seconds, transport=self._transport) as client:
                response = client.request(method, self.config.base_url + path, headers=self._headers(True), content=content)
        except httpx.TimeoutException as exc:
            raise CloudflareComputerError("upstream_timeout", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise CloudflareComputerError("upstream_unreachable", status_code=502, retryable=True) from exc
        if response.status_code >= 400:
            raise CloudflareComputerError("upstream_rejected", status_code=502, retryable=response.status_code >= 500)


def _file_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    if not value or value.startswith("/") or len(value) > 512 or "\x00" in value:
        raise CloudflareComputerError("invalid_path", status_code=422)
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise CloudflareComputerError("invalid_path", status_code=422)
    return value
