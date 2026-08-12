"""Typed, dependency-free Python client for HashMM's stable ``/v1`` API."""
from __future__ import annotations

import json
import uuid
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.request


class HashMMError(RuntimeError):
    """Base class for client, transport, protocol, and API failures."""


class HashMMTransportError(HashMMError):
    """The HashMM service could not be reached."""


class HashMMHTTPError(HashMMError):
    """The server returned a non-success HTTP response."""

    def __init__(self, status_code: int, message: str, *, error_code: str = "",
                 details: Mapping[str, Any] | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = int(status_code)
        self.error_code = error_code
        self.details = dict(details or {})
        self.body = body


class HashMMClient:
    """Small synchronous client for search and citation-backed RAG.

    Args:
        base_url: HashMM server origin, for example ``http://127.0.0.1:6006``.
        api_key: Value configured in ``HASHMM_API_KEY``. It is sent as Bearer auth.
        timeout: Per-request timeout in seconds.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:6006", *, api_key: str = "",
                 timeout: float = 60.0):
        base = str(base_url).strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base
        self.api_key = str(api_key)
        self.timeout = float(timeout)

    def _headers(self, trace_id: str | None = None,
                 idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "hashmm-python/0.8.20",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if trace_id:
            headers["X-Trace-ID"] = str(trace_id)
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        return headers

    @staticmethod
    def _decode(raw: bytes, *, url: str) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HashMMError(f"HashMM returned invalid JSON from {url}") from exc
        if not isinstance(value, dict):
            raise HashMMError(f"HashMM returned a non-object JSON response from {url}")
        return value

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None,
                 *, trace_id: str | None = None,
                 idempotency_key: str | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers=self._headers(trace_id, idempotency_key), method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return self._decode(response.read(), url=url)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                body: Any = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                body = {"raw": raw.decode("utf-8", "replace")[:1000]}
            error = body.get("error", {}) if isinstance(body, dict) else {}
            if isinstance(error, dict):
                message = str(error.get("message") or f"HashMM request failed with HTTP {exc.code}")
                code = str(error.get("code") or "")
                details = error.get("details") if isinstance(error.get("details"), dict) else {}
            else:
                message, code, details = str(error or f"HTTP {exc.code}"), "", {}
            raise HashMMHTTPError(exc.code, message, error_code=code,
                                  details=details, body=body) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HashMMTransportError(f"Could not reach HashMM at {url}: {exc}") from exc

    @staticmethod
    def _query(query: str) -> str:
        value = str(query).strip()
        if not value:
            raise ValueError("query must not be empty")
        return value

    @staticmethod
    def _top_k(top_k: int) -> int:
        value = int(top_k)
        if not 1 <= value <= 100:
            raise ValueError("top_k must be between 1 and 100")
        return value

    def info(self, *, trace_id: str | None = None) -> dict[str, Any]:
        """Return server version, authentication mode, and stable endpoints."""
        return self._request("GET", "/v1/info", trace_id=trace_id)

    @staticmethod
    def new_idempotency_key() -> str:
        """Create a caller-owned key that may be reused for transport retries."""
        return str(uuid.uuid4())

    @staticmethod
    def _idem(value: str) -> str:
        key = str(value or "").strip()
        if not key:
            raise ValueError("idempotency_key is required for write operations")
        return key

    def capabilities(self, *, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/v1/capabilities", trace_id=trace_id)

    def models(self, *, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/v1/models", trace_id=trace_id)

    def create_thread(self, *, idempotency_key: str, title: str = "",
                      project_id: str = "", metadata: Mapping[str, Any] | None = None,
                      trace_id: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/v1/threads", {
            "title": str(title), "project_id": str(project_id), "metadata": dict(metadata or {}),
        }, trace_id=trace_id, idempotency_key=self._idem(idempotency_key))

    def get_thread(self, thread_id: str, *, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/v1/threads/{thread_id}", trace_id=trace_id)

    def update_thread(self, thread_id: str, *, idempotency_key: str,
                      title: str | None = None, metadata: Mapping[str, Any] | None = None,
                      status: str | None = None, expected_revision: int | None = None,
                      trace_id: str | None = None) -> dict[str, Any]:
        payload = {key: value for key, value in {
            "title": title, "metadata": dict(metadata) if metadata is not None else None,
            "status": status, "expected_revision": expected_revision,
        }.items() if value is not None}
        return self._request("PATCH", f"/v1/threads/{thread_id}", payload,
                             trace_id=trace_id, idempotency_key=self._idem(idempotency_key))

    def fork_thread(self, thread_id: str, *, idempotency_key: str, title: str = "",
                    trace_id: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/v1/threads/{thread_id}/fork", {"title": title},
                             trace_id=trace_id, idempotency_key=self._idem(idempotency_key))

    def create_response(self, input: str | Sequence[Mapping[str, Any]], *,
                        idempotency_key: str, model: str = "", thread_id: str = "",
                        project_id: str = "", background: bool = False, rag: bool = False,
                        document_scope: Sequence[str] | None = None,
                        trace_id: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/v1/responses", {
            "input": input, "model": model, "thread_id": thread_id, "project_id": project_id,
            "background": bool(background), "rag": bool(rag),
            "document_scope": list(document_scope or []),
        }, trace_id=trace_id, idempotency_key=self._idem(idempotency_key))

    def get_response(self, response_id: str, *, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/v1/responses/{response_id}", trace_id=trace_id)

    def delete_response(self, response_id: str, *, idempotency_key: str,
                        trace_id: str | None = None) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/responses/{response_id}", {},
                             trace_id=trace_id, idempotency_key=self._idem(idempotency_key))

    def create_run(self, goal: str, *, idempotency_key: str, title: str = "",
                   project_id: str = "", acceptance: Sequence[str] | None = None,
                   autonomy_level: int = 0, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/v1/runs", {
            "goal": str(goal), "title": str(title), "project_id": str(project_id),
            "acceptance": list(acceptance or []), "autonomy_level": int(autonomy_level),
        }, trace_id=trace_id, idempotency_key=self._idem(idempotency_key))

    def get_run(self, run_id: str, *, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/v1/runs/{run_id}", trace_id=trace_id)

    def run_checkpoints(self, run_id: str, *, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/v1/runs/{run_id}/checkpoints", trace_id=trace_id)

    def decide_approval(self, run_id: str, approval_id: str, decision: str, *,
                        idempotency_key: str, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/v1/runs/{run_id}/approvals/{approval_id}",
                             {"decision": decision}, trace_id=trace_id,
                             idempotency_key=self._idem(idempotency_key))

    def command_run(self, run_id: str, action: str, expected_revision: int, *,
                    idempotency_key: str, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/v1/runs/{run_id}/commands", {
            "action": action, "expected_revision": int(expected_revision),
        }, trace_id=trace_id, idempotency_key=self._idem(idempotency_key))

    def usage(self, *, days: int = 30, trace_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/v1/usage?days={max(1, min(int(days), 366))}", trace_id=trace_id)

    def search(self, query: str, *, top_k: int = 5,
               trace_id: str | None = None) -> dict[str, Any]:
        """Run HashMM's hybrid retrieval pipeline without generation."""
        return self._request("POST", "/v1/search",
                             {"query": self._query(query), "top_k": self._top_k(top_k)},
                             trace_id=trace_id)

    def rag(self, query: str, *, top_k: int = 5,
            history: Sequence[Mapping[str, Any]] | None = None,
            trace_id: str | None = None) -> dict[str, Any]:
        """Generate a retrieval-grounded answer and return its source records."""
        return self._request("POST", "/v1/rag", {
            "query": self._query(query),
            "top_k": self._top_k(top_k),
            "history": [dict(item) for item in (history or [])],
        }, trace_id=trace_id)


__all__ = [
    "HashMMClient", "HashMMError", "HashMMHTTPError", "HashMMTransportError",
]
