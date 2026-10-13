"""Versioned public API backed by HashMM's durable WorkRuntime.

The legacy ``/v1/search`` and ``/v1/rag`` endpoints remain compatible.  New
agent endpoints use owner-scoped Threads/Turns/Items, durable Responses,
structured replayable SSE and mandatory idempotency for every write.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from hashmm.api import platform_access
from hashmm.api import platform_protocol as protocol
from hashmm.utils import get_logger, log_suppressed


logger = get_logger("hashmm.api.public_api")
router = APIRouter(prefix="/v1", tags=["public-api"])


def _err(code: str, message: str = "", status: int | None = None,
         details: dict | None = None) -> JSONResponse:
    """Return the stable public error envelope."""
    try:
        from hashmm import error_codes as EC
        from hashmm.trace_context import current_trace_id
        spec = EC.spec_for(code)
        body: dict[str, Any] = {"error": {
            "code": spec.code, "message": message or spec.message,
            "category": spec.category, "details": details or {},
        }}
        trace_id = current_trace_id()
        if trace_id:
            body["error"]["trace_id"] = trace_id
        try:
            from hashmm import observability
            observability.record_error(spec.code, trace_id=trace_id)
        except Exception:
            pass
        return JSONResponse(body, status_code=status or spec.http_status)
    except Exception:
        return JSONResponse(
            {"error": {"code": code, "message": message or code, "details": details or {}}},
            status_code=status or 500,
        )


def _protocol_error(exc: protocol.ProtocolError) -> JSONResponse:
    return _err(exc.code, exc.message, status=exc.status)


def public_api_enabled() -> bool:
    return os.environ.get("HASHMM_PUBLIC_API", "0").strip().lower() in {"1", "true", "yes", "on"}


def _source_ip(request: Request) -> str:
    # Do not trust X-Forwarded-For by default.  A deployment can place its
    # trusted proxy in front of ASGI and expose the verified peer as client.
    client = getattr(request, "client", None)
    return str(client.host if client else "")


def _supplied_secret(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    bearer = auth[7:] if auth.startswith("Bearer ") else ""
    return str(bearer or request.headers.get("X-API-Key", "")
               or request.headers.get("X-Goog-API-Key", ""))


def _resolve_access(request: Request) -> platform_access.AccessContext:
    state = getattr(request, "state", None)
    cached = getattr(state, "hashmm_api_access", None)
    if isinstance(cached, platform_access.AccessContext):
        return cached
    if any(request.query_params.get(name) for name in ("api_key", "key", "access_token")):
        raise platform_access.AccessError(
            "invalid_api_key", "API keys are not accepted in query parameters", 401,
        )
    supplied = _supplied_secret(request)
    if supplied:
        managed = platform_access.authenticate(supplied, source_ip=_source_ip(request))
        if managed is not None:
            if state is not None:
                state.hashmm_api_access = managed
            return managed
    configured = os.environ.get("HASHMM_API_KEY", "")
    if configured and supplied and hmac.compare_digest(supplied, configured):
        value = platform_access.AccessContext(
            kind="legacy", owner_id="api_" + hashlib.sha256(supplied.encode("utf-8")).hexdigest()[:24],
            scopes=("*",),
        )
        if state is not None:
            state.hashmm_api_access = value
        return value
    try:
        from hashmm.api.auth import get_current_user
        user = get_current_user(request)
        if user and user.get("role") == "admin":
            owner = str(user.get("uid") or user.get("id") or user.get("sub") or "admin")
            value = platform_access.AccessContext(kind="admin", owner_id=owner, scopes=("*",))
            if state is not None:
                state.hashmm_api_access = value
            return value
    except Exception:
        pass
    if os.environ.get("HASHMM_PUBLIC_API_OPEN", "").strip().lower() in {"1", "true", "yes", "on"}:
        value = platform_access.AccessContext(kind="open", owner_id="public_open", scopes=("*",))
        if state is not None:
            state.hashmm_api_access = value
        return value
    raise platform_access.AccessError("invalid_api_key", "invalid API key", 401)


def _authorized(request: Request) -> bool:
    """Backward-compatible boolean probe used by older tests/extensions."""
    try:
        _resolve_access(request)
        return True
    except platform_access.AccessError:
        return False


def _required_scope(request: Request) -> str:
    path = request.url.path.rstrip("/")
    method = request.method.upper()
    if path in {"/v1/models", "/v1/capabilities"}:
        return "models:read"
    if path == "/v1/usage":
        return "usage:read"
    if path.startswith("/v1/threads"):
        return "threads:read" if method == "GET" else "threads:write"
    if path.startswith("/v1/runs"):
        return "runs:read" if method == "GET" else "runs:write"
    if path.startswith("/v1/responses"):
        return "responses:read" if method == "GET" else "responses:write"
    if path in {"/v1/chat/completions", "/v1/search", "/v1/rag"}:
        return "responses:write"
    return ""


def _access_error(exc: platform_access.AccessError) -> JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    if exc.retry_after:
        headers["Retry-After"] = str(exc.retry_after)
    if exc.limit is not None:
        headers["X-RateLimit-Limit"] = str(exc.limit)
    if exc.remaining is not None:
        headers["X-RateLimit-Remaining"] = str(exc.remaining)
    value = _err(exc.code, exc.message, status=exc.status)
    value.headers.update(headers)
    return value


def _guard(request: Request) -> JSONResponse | None:
    if not public_api_enabled():
        return _err("not_found", "public API disabled (set HASHMM_PUBLIC_API=1)", status=404)
    try:
        context = _resolve_access(request)
        platform_access.require_scope(context, _required_scope(request))
        rate = platform_access.consume_rate_limit(context)
        if rate:
            request.state.hashmm_rate_limit = rate
        return None
    except platform_access.AccessError as exc:
        return _access_error(exc)


def _owner(request: Request) -> str:
    """Resolve the owner already verified by the access-control plane."""
    return _resolve_access(request).owner_id


def _resource_guard(request: Request, *, model: str = "", project_id: str = "") -> JSONResponse | None:
    try:
        platform_access.require_resources(_resolve_access(request), model=model, project_id=project_id)
        return None
    except platform_access.AccessError as exc:
        return _access_error(exc)


def _rate_headers(request: Request) -> dict[str, str]:
    rate = getattr(request.state, "hashmm_rate_limit", None)
    if not isinstance(rate, dict):
        return {}
    return {
        "X-RateLimit-Limit": str(rate.get("limit", "")),
        "X-RateLimit-Remaining": str(rate.get("remaining", "")),
        "X-RateLimit-Reset": str(rate.get("reset", "")),
    }


async def _body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        value = await request.json()
    except Exception:
        return _err("bad_request", "invalid JSON body", status=400)
    if not isinstance(value, dict):
        return _err("bad_request", "JSON body must be an object", status=400)
    return value


def _idem_key(request: Request) -> str:
    return str(request.headers.get("Idempotency-Key") or "").strip()


def _begin_write(request: Request, owner: str, route: str, body: dict[str, Any]):
    try:
        claim = protocol.begin_idempotent(owner, route, _idem_key(request), body)
    except protocol.ProtocolError as exc:
        return None, _protocol_error(exc)
    if claim["state"] == "replay":
        return None, JSONResponse(
            claim["response"], status_code=claim["status_code"],
            headers={"Idempotent-Replayed": "true", "Cache-Control": "private, no-store"},
        )
    return claim, None


def _finish_write(request: Request, owner: str, route: str, status: int,
                  content: dict[str, Any], resource_id: str = "") -> JSONResponse:
    try:
        protocol.finish_idempotent(
            owner, route, _idem_key(request), status_code=status,
            response=content, resource_id=resource_id,
        )
    except protocol.ProtocolError as exc:
        return _protocol_error(exc)
    return JSONResponse(
        content, status_code=status,
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization", **_rate_headers(request)},
    )


def _admit_execution(request: Request, *, amount: float, request_id: str) -> dict[str, Any]:
    context = _resolve_access(request)
    lease = platform_access.acquire_lease(context, request_id=request_id, ttl_seconds=3600)
    try:
        quota = platform_access.reserve_quota(
            context, amount=amount, idempotency_key=request_id, ttl_seconds=3600,
        )
    except Exception:
        if lease:
            platform_access.release_lease(context, str(lease["id"]))
        raise
    return {"context": context, "lease": lease, "quota": quota}


def _release_admission(admission: dict[str, Any] | None, *, failed: bool = False) -> None:
    if not admission:
        return
    context = admission.get("context")
    if not isinstance(context, platform_access.AccessContext):
        return
    quota = admission.get("quota")
    if failed and quota:
        platform_access.release_quota(context, str(quota.get("id") or ""))
    lease = admission.get("lease")
    if lease:
        platform_access.release_lease(context, str(lease.get("id") or ""))


def _model_name(body: dict[str, Any]) -> str:
    requested = str(body.get("model") or "").strip()
    if requested:
        return requested[:120]
    try:
        from hashmm.api import database as db
        current = db.get_default_model() or {}
        return str(current.get("model_name") or current.get("name") or "hashmm-default")[:120]
    except Exception:
        return "hashmm-default"


def _normalise_input(raw: Any) -> tuple[list[dict[str, Any]], str, list[dict[str, str]]]:
    items: list[dict[str, Any]] = []
    history: list[dict[str, str]] = []
    if isinstance(raw, str):
        items = [{"type": "message", "role": "user", "content": raw}]
    elif isinstance(raw, list):
        for value in raw[:200]:
            if isinstance(value, str):
                items.append({"type": "message", "role": "user", "content": value})
            elif isinstance(value, dict):
                role = str(value.get("role") or "user")[:20]
                content = value.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for part in content[:100]:
                        if isinstance(part, dict) and part.get("type") in {"input_text", "text"}:
                            text_parts.append(str(part.get("text") or ""))
                    content = "\n".join(text_parts)
                items.append({"type": str(value.get("type") or "message")[:40],
                              "role": role, "content": str(content)[:100_000]})
    if not items:
        raise protocol.ProtocolError("invalid_input", "input must contain at least one message", 400)
    for item in items[:-1]:
        history.append({"role": str(item.get("role") or "user"),
                        "content": str(item.get("content") or "")})
    prompt = str(items[-1].get("content") or "").strip()
    if not prompt:
        raise protocol.ProtocolError("invalid_input", "the last input message is empty", 400)
    return items, prompt, history[-40:]


def _sources_for(body: dict[str, Any], prompt: str, owner: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    should_retrieve = bool(body.get("rag") or body.get("document_scope") or body.get("attachments"))
    if not should_retrieve:
        return [], {}
    scope = body.get("document_scope") or body.get("attachments") or []
    if isinstance(scope, str):
        scope = [scope]
    try:
        from hashmm.retriever_bridge import kb_search_bridge
        result = kb_search_bridge(
            {"query": prompt, "top_k": max(1, min(int(body.get("top_k") or 8), 40))},
            {"user_id": owner, "doc_filter": scope},
        )
        return list(result.get("results") or []), dict(result.get("retrieval_contract") or {})
    except Exception as exc:
        log_suppressed(logger, exc)
        return [], {"evidence_status": "unavailable", "error_type": type(exc).__name__}


def _execute_text(prompt: str, history: list[dict[str, str]], sources: list[dict[str, Any]]) -> str:
    from hashmm.api import server
    if getattr(server, "_llm_fn", None) is None:
        raise protocol.ProtocolError("llm_unavailable", "LLM service is not ready", 503)
    generated = server.generate(prompt, sources, history, "kb" if sources else "chat")
    answer = str(generated or "").strip()
    if not answer:
        raise protocol.ProtocolError("llm_error", "model returned an empty response", 502)
    return answer


def _usage(prompt: str, answer: str) -> dict[str, int]:
    # Provider usage wins when available; this deterministic estimate is
    # explicitly labelled and never presented as provider billing evidence.
    input_tokens = max(1, (len(prompt) + 3) // 4)
    output_tokens = max(1, (len(answer) + 3) // 4)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens, "source": "estimated"}


def _estimated_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    try:
        from hashmm.api.usage import compute_cost
        return max(0.000001, float(compute_cost(model, input_tokens, output_tokens)))
    except Exception:
        return 0.000001


def _public_output(answer: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "id": "msg_" + hashlib.sha256(answer.encode("utf-8")).hexdigest()[:24],
        "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": answer,
                     "annotations": [{"type": "source", "source": item.get("source"),
                                      "filename": item.get("filename"), "page": item.get("page")}
                                     for item in sources[:40]]}],
    }]


def _record_event(owner: str, response_id: str, event_type: str,
                  response: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return protocol.append_response_event(owner, response_id, event_type, {
        "run_id": response.get("run_id", ""), "turn_id": response.get("turn_id", ""),
        "item_id": extra.pop("item_id", ""), **extra,
    })


def _sse(events: list[dict[str, Any]]) -> AsyncIterator[str]:
    async def generate():
        for event in events:
            yield f"id: {event['id']}\nevent: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
    return generate()


def _after_sequence(request: Request) -> int:
    raw = request.headers.get("Last-Event-ID", "") or request.query_params.get("after", "0")
    match = re.search(r"(\d+)$", str(raw))
    return int(match.group(1)) if match else 0


@router.get("")
@router.get("/")
@router.get("/info")
async def v1_info(request: Request):
    if not public_api_enabled():
        return _err("not_found", "public API disabled (set HASHMM_PUBLIC_API=1)", status=404)
    from hashmm import RELEASE, __version__
    return {
        "service": "hashmm", "version": __version__, "release": RELEASE,
        "schema_version": protocol.SCHEMA_VERSION,
        "capabilities": ["responses", "chat_completions", "threads", "durable_runs",
                         "replayable_sse", "hybrid_search", "citation_rag", "usage_ledger"],
        "auth": "Bearer HASHMM_API_KEY / X-API-Key / admin JWT",
    }


@router.get("/capabilities")
async def v1_capabilities(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    return JSONResponse({
        "object": "capabilities", "schema_version": protocol.SCHEMA_VERSION,
        "responses": {"stream": True, "background": True, "event_replay": True},
        "runs": {"durable": True, "checkpoints": True, "approvals": True,
                 "public_task_chain": True, "hidden_chain_of_thought": False},
        "retrieval": {"hybrid": True, "strict_document_scope": True,
                      "expected_gain_selector": True, "citation_guard": True},
        "compute": {"read_only": True, "isolate": False, "container": False,
                    "note": "external runtimes require an explicitly configured adapter"},
    }, headers=_rate_headers(request))


@router.get("/models")
async def v1_models(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    models: list[dict[str, Any]] = []
    try:
        from hashmm.api import database as db
        for item in db.list_models():
            models.append({
                "id": str(item.get("model_name") or item.get("name") or item.get("id")),
                "object": "model", "owned_by": str(item.get("provider") or "hashmm"),
                "capabilities": ["responses", "chat_completions"],
            })
    except Exception as exc:
        log_suppressed(logger, exc)
    if not models:
        models.append({"id": "hashmm-default", "object": "model", "owned_by": "hashmm",
                       "capabilities": ["responses", "chat_completions"]})
    context = _resolve_access(request)
    if context.managed and context.allowed_models:
        models = [item for item in models if item["id"] in context.allowed_models]
    return JSONResponse({"object": "list", "data": models}, headers=_rate_headers(request))


@router.post("/threads")
async def v1_create_thread(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    denied = _resource_guard(request, project_id=str(body.get("project_id") or ""))
    if denied is not None:
        return denied
    owner, route = _owner(request), "POST:/v1/threads"
    claim, replay = _begin_write(request, owner, route, body)
    if replay is not None:
        return replay
    try:
        thread_id = "thread_" + protocol.fingerprint([owner, route, _idem_key(request)])[:24]
        value = protocol.create_thread(
            owner, title=str(body.get("title") or ""), project_id=str(body.get("project_id") or ""),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
            thread_id=thread_id,
        )
        return _finish_write(request, owner, route, 201, value, value["id"])
    except protocol.ProtocolError as exc:
        error = {"error": {"code": exc.code, "message": exc.message}}
        return _finish_write(request, owner, route, exc.status, error)


@router.get("/threads")
async def v1_list_threads(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    project_id = str(request.query_params.get("project_id") or "")
    denied = _resource_guard(request, project_id=project_id)
    if denied is not None:
        return denied
    data = protocol.list_threads(
        _owner(request), project_id=project_id,
        limit=int(request.query_params.get("limit") or 50),
    )
    context = _resolve_access(request)
    if context.managed and context.allowed_projects and not project_id:
        data = [item for item in data if str(item.get("project_id") or "") in context.allowed_projects]
    return {"object": "list", "data": data}


@router.get("/threads/{thread_id}")
async def v1_get_thread(thread_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    value = protocol.get_thread(_owner(request), thread_id)
    return value if value is not None else _err("not_found", "thread not found", status=404)


@router.patch("/threads/{thread_id}")
async def v1_update_thread(thread_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    owner, route = _owner(request), f"PATCH:/v1/threads/{thread_id}"
    _, replay = _begin_write(request, owner, route, body)
    if replay is not None:
        return replay
    try:
        value = protocol.update_thread(
            owner, thread_id, title=body.get("title"), metadata=body.get("metadata"),
            status=body.get("status"), expected_revision=body.get("expected_revision"),
        )
        if value is None:
            return _finish_write(request, owner, route, 404,
                                 {"error": {"code": "not_found", "message": "thread not found"}})
        return _finish_write(request, owner, route, 200, value, thread_id)
    except protocol.ProtocolError as exc:
        return _finish_write(request, owner, route, exc.status,
                             {"error": {"code": exc.code, "message": exc.message}})


@router.post("/threads/{thread_id}/fork")
async def v1_fork_thread(thread_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    owner, route = _owner(request), f"POST:/v1/threads/{thread_id}/fork"
    _, replay = _begin_write(request, owner, route, body)
    if replay is not None:
        return replay
    value = protocol.fork_thread(owner, thread_id, title=str(body.get("title") or ""))
    if value is None:
        return _finish_write(request, owner, route, 404,
                             {"error": {"code": "not_found", "message": "thread not found"}})
    return _finish_write(request, owner, route, 201, value, value["id"])


def _create_response_resources(owner: str, body: dict[str, Any], idem_key: str,
                               namespace: str = "responses"):
    from hashmm.agent import work_runtime
    items, prompt, history = _normalise_input(body.get("input"))
    response_id = "resp_" + protocol.fingerprint([owner, namespace, idem_key])[:24]
    existing_response = protocol.get_response(owner, response_id)
    if existing_response is not None:
        existing_work = work_runtime.get_run(existing_response["run_id"], owner, limit=1)
        if existing_work is None:
            raise protocol.ProtocolError("recovery_incomplete", "response run is unavailable", 503)
        return existing_response, prompt, history, existing_work
    thread_id = str(body.get("thread_id") or "")
    if thread_id:
        if protocol.get_thread(owner, thread_id) is None:
            raise protocol.ProtocolError("not_found", "thread not found", 404)
    else:
        thread_id = "thread_" + protocol.fingerprint([owner, "response-thread", idem_key])[:24]
        existing = protocol.get_thread(owner, thread_id)
        if existing is None:
            protocol.create_thread(owner, title=prompt[:80], project_id=str(body.get("project_id") or ""),
                                   metadata={"created_by": "responses"}, thread_id=thread_id)
    turn_id = protocol.create_turn_with_input(owner, thread_id, items)
    source_id = f"v1-{namespace}:" + protocol.fingerprint([owner, namespace, idem_key])[:40]
    work = work_runtime.create_run(
        user_id=owner, kind="chat", source_id=source_id, title=prompt[:240], status="queued",
        project_id=str(body.get("project_id") or ""),
        snapshot={"public_api": True, "thread_id": thread_id, "turn_id": turn_id,
                  "acceptance_required": True, "hidden_chain_of_thought_exposed": False},
    )
    response = protocol.create_response(
        owner, request=body, thread_id=thread_id, turn_id=turn_id, run_id=work["id"],
        model=_model_name(body), response_id=response_id,
    )
    return response, prompt, history, work


def _complete_response(owner: str, body: dict[str, Any], response: dict[str, Any],
                       prompt: str, history: list[dict[str, str]],
                       work: dict[str, Any], admission: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute one admitted response and persist every externally visible fact."""
    from hashmm.agent import work_runtime
    work_runtime.append_event_once(
        work["id"], user_id=owner, event_type="response.started", status="running",
        summary="Public response execution started",
        idempotency_key=f"v1-response:{response['id']}:started",
    )
    response = protocol.update_response(owner, response["id"], status="in_progress") or response
    _record_event(owner, response["id"], "run.status.changed", response, status="running")

    limit_raw = os.environ.get("HASHMM_API_BUDGET_LIMIT_CNY", "").strip()
    budget_limit = float(limit_raw) if limit_raw else None
    maximum_output = max(1, min(int(body.get("max_output_tokens") or 4096), 128_000))
    reserve_amount = _estimated_cost(
        response["model"], max(1, (len(prompt) + 3) // 4), maximum_output,
    )
    reservation = protocol.reserve_budget(
        owner, run_id=work["id"], amount=reserve_amount,
        currency="CNY", idempotency_key=f"response:{response['id']}:budget",
        limit=budget_limit,
    )
    sources, retrieval_contract = _sources_for(body, prompt, owner)
    answer = _execute_text(prompt, history, sources)
    usage = _usage(prompt, answer)
    output = _public_output(answer, sources)
    message_id = output[0]["id"]
    for offset in range(0, len(answer), 160):
        _record_event(
            owner, response["id"], "response.output_text.delta", response,
            item_id=message_id, delta=answer[offset:offset + 160],
        )
    work_runtime.append_event_once(
        work["id"], user_id=owner, event_type="response.delivered", status="delivered",
        summary="Response produced and awaits acceptance evidence",
        payload={"response_id": response["id"], "message_id": message_id,
                 "retrieval": retrieval_contract},
        snapshot_updates={"response_id": response["id"],
                          "retrieval_contract": retrieval_contract},
        idempotency_key=f"v1-response:{response['id']}:delivered",
    )
    actual_cost = _estimated_cost(
        response["model"], usage["input_tokens"], usage["output_tokens"],
    )
    access_context = admission.get("context") if admission else None
    protocol.settle_usage(
        owner, reservation_id=str(reservation["id"]), run_id=work["id"], step_id="response",
        provider="hashmm", service="llm", model=response["model"],
        quantity=usage["total_tokens"], unit="token",
        estimated_cost=actual_cost,
        currency="CNY", price_version="request-estimate-v1",
        idempotency_key=f"response:{response['id']}:usage",
        metadata={"usage_source": "estimated"},
        access_key_id=access_context.key_id
        if isinstance(access_context, platform_access.AccessContext) else "",
    )
    if admission and admission.get("quota") and isinstance(access_context, platform_access.AccessContext):
        platform_access.settle_quota(
            access_context, str(admission["quota"]["id"]), actual_amount=actual_cost,
        )
    response = protocol.complete_response(
        owner, response["id"], output=output,
        usage={**usage, "retrieval": retrieval_contract},
    ) or response
    return response


def _fail_response(owner: str, response: dict[str, Any], work: dict[str, Any],
                   exc: Exception) -> None:
    """Persist a terminal failure without leaking provider exception text publicly."""
    from hashmm.agent import work_runtime
    code = exc.code if isinstance(exc, protocol.ProtocolError) else "internal_error"
    message = exc.message if isinstance(exc, protocol.ProtocolError) else "response execution failed"
    error = {"code": code, "message": message}
    protocol.release_budget(
        owner, idempotency_key=f"response:{response['id']}:budget",
    )
    failed = protocol.update_response(owner, response["id"], status="failed", error=error)
    if failed:
        _record_event(owner, response["id"], "error", failed, error=error)
    work_runtime.append_event_once(
        work["id"], user_id=owner, event_type="response.failed", status="failed",
        summary=message, idempotency_key=f"v1-response:{response['id']}:failed",
    )


@router.post("/responses")
async def v1_responses(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    denied = _resource_guard(
        request, model=_model_name(body), project_id=str(body.get("project_id") or ""),
    )
    if denied is not None:
        return denied
    owner, route = _owner(request), "POST:/v1/responses"
    _, replay = _begin_write(request, owner, route, body)
    if replay is not None:
        return replay
    admission: dict[str, Any] | None = None
    handed_to_background = False
    try:
        from hashmm.agent import work_runtime
        response, prompt, history, work = _create_response_resources(
            owner, body, _idem_key(request), "responses",
        )
        if not protocol.list_response_events(owner, response["id"]):
            _record_event(owner, response["id"], "response.created", response, status="created")
        maximum_output = max(1, min(int(body.get("max_output_tokens") or 4096), 128_000))
        admission = _admit_execution(
            request,
            amount=_estimated_cost(response["model"], max(1, (len(prompt) + 3) // 4), maximum_output),
            request_id=f"response:{response['id']}",
        )
        if bool(body.get("background")):
            content = protocol.update_response(owner, response["id"], status="queued") or response
            _record_event(owner, response["id"], "run.status.changed", content, status="queued")
            from hashmm.api import jobs

            async def background_worker(progress):
                progress(done=0, total=1, message="Executing public response")
                failed = True
                try:
                    completed = await asyncio.to_thread(
                        _complete_response, owner, body, content, prompt, history, work, admission,
                    )
                    failed = False
                except Exception as exc:
                    try:
                        _fail_response(owner, content, work, exc)
                    except Exception as persist_exc:
                        log_suppressed(logger, persist_exc)
                    raise
                finally:
                    _release_admission(admission, failed=failed)
                progress(done=1, total=1, message="Public response delivered")
                return {"response_id": completed["id"], "status": completed["status"]}

            job_id = jobs.spawn(
                "public_response", background_worker, total=1, owner_id=owner,
                project_id=str(body.get("project_id") or ""), work_run_id=work["id"],
            )
            handed_to_background = True
            _record_event(owner, response["id"], "response.background_queued", content,
                          status="queued", background_job_id=job_id)
            return _finish_write(request, owner, route, 202, content, response["id"])
        response = _complete_response(owner, body, response, prompt, history, work, admission)
        final_events = protocol.list_response_events(owner, response["id"])
        result = _finish_write(request, owner, route, 200, response, response["id"])
        if bool(body.get("stream")):
            return StreamingResponse(
                _sse(final_events), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
            )
        return result
    except (protocol.ProtocolError, platform_access.AccessError) as exc:
        error = {"error": {"code": exc.code, "message": exc.message}}
        try:
            if "response" in locals() and "work" in locals():
                _fail_response(owner, response, work, exc)
        except Exception as persist_exc:
            log_suppressed(logger, persist_exc)
        return _finish_write(request, owner, route, exc.status, error,
                             response.get("id", "") if "response" in locals() else "")
    except Exception as exc:
        logger.exception("public response failed")
        try:
            if "response" in locals() and "work" in locals():
                _fail_response(owner, response, work, exc)
        except Exception as persist_exc:
            log_suppressed(logger, persist_exc)
        error = {"error": {"code": "internal_error", "message": "response execution failed",
                           "details": {"type": type(exc).__name__}}}
        return _finish_write(request, owner, route, 500, error)
    finally:
        if admission is not None and not handed_to_background:
            failed = not ("response" in locals() and isinstance(response, dict)
                          and response.get("status") == "completed")
            _release_admission(admission, failed=failed)


@router.get("/responses/{response_id}")
async def v1_get_response(response_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    value = protocol.get_response(_owner(request), response_id)
    return value if value is not None else _err("not_found", "response not found", status=404)


@router.get("/responses/{response_id}/events")
async def v1_response_events(response_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    owner = _owner(request)
    if protocol.get_response(owner, response_id) is None:
        return _err("not_found", "response not found", status=404)
    events = protocol.list_response_events(owner, response_id, after_sequence=_after_sequence(request))
    if request.headers.get("Accept", "").startswith("text/event-stream"):
        return StreamingResponse(_sse(events), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform"})
    return {"object": "list", "data": events}


@router.delete("/responses/{response_id}")
async def v1_delete_response(response_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    owner, route = _owner(request), f"DELETE:/v1/responses/{response_id}"
    body = {"response_id": response_id}
    _, replay = _begin_write(request, owner, route, body)
    if replay is not None:
        return replay
    deleted = protocol.delete_response(owner, response_id)
    content = {"id": response_id, "object": "response.deleted", "deleted": deleted}
    return _finish_write(request, owner, route, 200 if deleted else 404, content, response_id)


@router.post("/chat/completions")
async def v1_chat_completions(request: Request):
    """OpenAI-style compatibility facade over the canonical Responses API."""
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    response_body = {**body, "input": body.get("messages") or body.get("input")}
    denied = _resource_guard(
        request, model=_model_name(response_body), project_id=str(body.get("project_id") or ""),
    )
    if denied is not None:
        return denied
    # Reuse the exact request object while preserving the caller's key.  A
    # minimal request proxy is unnecessary: call the canonical implementation
    # through a local helper is safer than recursive ASGI dispatch.
    owner, route = _owner(request), "POST:/v1/chat/completions"
    _, replay = _begin_write(request, owner, route, body)
    if replay is not None:
        return replay
    admission: dict[str, Any] | None = None
    reservation: dict[str, Any] | None = None
    completed_ok = False
    try:
        from hashmm.agent import work_runtime
        response, prompt, history, work = _create_response_resources(
            owner, response_body, _idem_key(request), "chat-completions",
        )
        limit_raw = os.environ.get("HASHMM_API_BUDGET_LIMIT_CNY", "").strip()
        budget_limit = float(limit_raw) if limit_raw else None
        maximum_output = max(1, min(int(body.get("max_tokens") or body.get("max_completion_tokens") or 4096), 128_000))
        admission = _admit_execution(
            request,
            amount=_estimated_cost(response["model"], max(1, (len(prompt) + 3) // 4), maximum_output),
            request_id=f"chat:{response['id']}",
        )
        reservation = protocol.reserve_budget(
            owner, run_id=work["id"],
            amount=_estimated_cost(response["model"], max(1, (len(prompt) + 3) // 4), maximum_output),
            currency="CNY", idempotency_key=f"chat:{response['id']}:budget", limit=budget_limit,
        )
        work_runtime.append_event_once(
            work["id"], user_id=owner, event_type="chat.started", status="running",
            summary="Chat completion started", idempotency_key=f"chat:{response['id']}:started",
        )
        sources, retrieval_contract = _sources_for(response_body, prompt, owner)
        answer = _execute_text(prompt, history, sources)
        usage = _usage(prompt, answer)
        output = _public_output(answer, sources)
        response = protocol.complete_response(
            owner, response["id"], output=output,
            usage={**usage, "retrieval": retrieval_contract},
        ) or response
        work_runtime.append_event_once(
            work["id"], user_id=owner, event_type="chat.delivered", status="delivered",
            summary="Chat completion delivered", idempotency_key=f"chat:{response['id']}:delivered",
        )
        actual_cost = _estimated_cost(
            response["model"], usage["input_tokens"], usage["output_tokens"]
        )
        access_context = admission.get("context") if admission else None
        protocol.settle_usage(
            owner, reservation_id=str(reservation["id"]), run_id=work["id"], step_id="chat-completion",
            provider="hashmm", service="llm", model=response["model"], quantity=usage["total_tokens"],
            unit="token", estimated_cost=actual_cost, currency="CNY", price_version="model-price-table-v1",
            idempotency_key=f"chat:{response['id']}:usage", metadata={"usage_source": "estimated"},
            access_key_id=access_context.key_id
            if isinstance(access_context, platform_access.AccessContext) else "",
        )
        if admission and admission.get("quota") and isinstance(access_context, platform_access.AccessContext):
            platform_access.settle_quota(
                access_context, str(admission["quota"]["id"]), actual_amount=actual_cost,
            )
        content = {
            "id": "chatcmpl_" + response["id"].split("_", 1)[-1], "object": "chat.completion",
            "created": int(response["created_at"]), "model": response["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": usage["input_tokens"],
                      "completion_tokens": usage["output_tokens"],
                      "total_tokens": usage["total_tokens"], "source": "estimated"},
            "hashmm": {"response_id": response["id"], "run_id": response["run_id"],
                       "thread_id": response["thread_id"]},
        }
        completed_ok = True
        return _finish_write(request, owner, route, 200, content, response["id"])
    except (protocol.ProtocolError, platform_access.AccessError) as exc:
        if reservation:
            protocol.release_budget(owner, reservation_id=str(reservation.get("id") or ""))
        if "response" in locals() and "work" in locals():
            _fail_response(owner, response, work, exc)
        return _finish_write(request, owner, route, exc.status,
                             {"error": {"code": exc.code, "message": exc.message}})
    except Exception as exc:
        logger.exception("public chat completion failed")
        if reservation:
            protocol.release_budget(owner, reservation_id=str(reservation.get("id") or ""))
        if "response" in locals() and "work" in locals():
            _fail_response(owner, response, work, exc)
        return _finish_write(
            request, owner, route, 500,
            {"error": {"code": "internal_error", "message": "chat completion failed",
                       "details": {"type": type(exc).__name__}}},
        )
    finally:
        _release_admission(admission, failed=not completed_ok)


@router.post("/runs")
async def v1_create_run(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    denied = _resource_guard(request, project_id=str(body.get("project_id") or ""))
    if denied is not None:
        return denied
    owner, route = _owner(request), "POST:/v1/runs"
    _, replay = _begin_write(request, owner, route, body)
    if replay is not None:
        return replay
    from hashmm.agent import work_runtime
    try:
        source_id = "v1-run:" + protocol.fingerprint([owner, _idem_key(request)])[:40]
        run = work_runtime.create_run(
            user_id=owner, kind=str(body.get("kind") or "workflow"), source_id=source_id,
            conv_id=str(body.get("thread_id") or ""), title=str(body.get("title") or body.get("goal") or ""),
            status="queued", project_id=str(body.get("project_id") or ""),
            autonomy_level=int(body.get("autonomy_level") or 0),
            execution_target=body.get("execution_target") if isinstance(body.get("execution_target"), dict) else {},
            snapshot={"goal": str(body.get("goal") or "")[:1000],
                      "acceptance": body.get("acceptance") or [], "public_api": True},
        )
        return _finish_write(request, owner, route, 202, run, run["id"])
    except (ValueError, TypeError) as exc:
        return _finish_write(request, owner, route, 400,
                             {"error": {"code": "invalid_request", "message": str(exc)}})


@router.get("/runs/{run_id}")
async def v1_get_run(run_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    from hashmm.agent import work_runtime
    run = work_runtime.get_run(run_id, _owner(request), after_seq=0, limit=200)
    return run if run is not None else _err("not_found", "run not found", status=404)


@router.get("/runs/{run_id}/events")
async def v1_run_events(run_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    from hashmm.agent import work_runtime
    run = work_runtime.get_run(run_id, _owner(request), after_seq=_after_sequence(request), limit=500)
    if run is None:
        return _err("not_found", "run not found", status=404)
    events = []
    for item in run.get("events") or []:
        events.append({
            "event_id": item.get("id"), "sequence": item.get("seq"),
            "event": str(item.get("type") or "run.event"), "run_id": run_id,
            "turn_id": (run.get("snapshot") or {}).get("turn_id", ""),
            "item_id": "", "timestamp": item.get("created_at"),
            "schema_version": protocol.SCHEMA_VERSION, "data": item,
        })
    if request.headers.get("Accept", "").startswith("text/event-stream"):
        serialised = [{"id": str(item["event_id"]), "event": str(item["event"]), "data": item}
                      for item in events]
        return StreamingResponse(_sse(serialised), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform"})
    return {"object": "list", "data": events}


@router.get("/runs/{run_id}/checkpoints")
async def v1_run_checkpoints(run_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    from hashmm.agent import work_runtime
    owner = _owner(request)
    if work_runtime.get_run(run_id, owner, limit=1) is None:
        return _err("not_found", "run not found", status=404)
    return {"object": "list", "data": work_runtime.list_checkpoints(run_id, owner, limit=100)}


@router.post("/runs/{run_id}/approvals/{approval_id}")
async def v1_run_approval(run_id: str, approval_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    owner, route = _owner(request), f"POST:/v1/runs/{run_id}/approvals/{approval_id}"
    _, replay = _begin_write(request, owner, route, body)
    if replay is not None:
        return replay
    from hashmm.api import database as db
    from hashmm.agent import work_runtime
    run = work_runtime.get_run(run_id, owner, limit=1)
    if run is None:
        return _finish_write(request, owner, route, 404,
                             {"error": {"code": "not_found", "message": "approval not found"}})
    with db._conn() as conn:
        row = conn.execute(
            "SELECT conv_id FROM tool_approval_requests WHERE id=? AND work_run_id=? AND user_id=?",
            (approval_id, run_id, owner),
        ).fetchone()
    if row is None:
        return _finish_write(request, owner, route, 404,
                             {"error": {"code": "not_found", "message": "approval not found"}})
    decision = str(body.get("decision") or "").lower()
    if decision not in {"approve", "decline"}:
        return _finish_write(request, owner, route, 400,
                             {"error": {"code": "invalid_decision", "message": "decision must be approve or decline"}})
    value = db.decide_tool_approval(
        request_id=approval_id, conv_id=str(row["conv_id"]), actor_user_id=owner,
        approve=decision == "approve",
    )
    if value is None:
        return _finish_write(request, owner, route, 404,
                             {"error": {"code": "not_found", "message": "approval not found"}})
    work_runtime.append_event_once(
        run_id, user_id=owner, event_type="approval.resolved",
        summary=f"Approval {decision}d by the owner",
        payload={"approval_id": approval_id, "decision": decision},
        idempotency_key=f"v1-approval:{approval_id}:{decision}",
    )
    return _finish_write(request, owner, route, 200, db.public_tool_approval(value) or {}, approval_id)


@router.post("/runs/{run_id}/commands")
async def v1_run_command(run_id: str, request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    owner, route = _owner(request), f"POST:/v1/runs/{run_id}/commands"
    _, replay = _begin_write(request, owner, route, body)
    if replay is not None:
        return replay
    from hashmm.agent import work_control
    action = str(body.get("action") or "").lower()
    if action not in {"pause", "resume", "cancel", "retry"}:
        return _finish_write(request, owner, route, 400,
                             {"error": {"code": "invalid_action", "message": "unsupported command"}})
    result = await work_control.execute_command(
        run_id, user={"uid": owner}, command_id=_idem_key(request), action=action,
        expected_revision=int(body.get("expected_revision") or 0),
    )
    status = 200 if result.get("ok") else 409
    return _finish_write(request, owner, route, status, result, str((result.get("command") or {}).get("id") or ""))


@router.get("/usage")
async def v1_usage(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    try:
        days = int(request.query_params.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    owner = _owner(request)
    value = protocol.usage_summary(owner, days=days)
    value["by_api_key"] = platform_access.access_key_breakdown(owner, days=days)
    context = _resolve_access(request)
    value["current_api_key_id"] = context.key_id if context.managed else None
    return JSONResponse(value, headers=_rate_headers(request))


@router.post("/search")
async def v1_search(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    query = str(body.get("query") or "").strip()
    if not query:
        return _err("bad_request", "query is required", status=400)
    try:
        top_k = int(body.get("top_k") or 5)
        if not 1 <= top_k <= 100:
            raise ValueError
    except (TypeError, ValueError):
        return _err("bad_request", "top_k must be an integer between 1 and 100", status=400)
    try:
        from hashmm.retriever_bridge import kb_search_bridge
        result = kb_search_bridge(
            {"query": query, "top_k": top_k},
            {"user_id": _owner(request), "doc_filter": body.get("document_scope")}
            if "document_scope" in body else {"user_id": _owner(request)},
        )
        return {"query": query, "results": result.get("results", []),
                "num_results": result.get("num_results", 0), "elapsed_ms": result.get("elapsed_ms"),
                "document_scope": result.get("document_scope", []),
                "retrieval_contract": result.get("retrieval_contract", {})}
    except Exception as exc:
        log_suppressed(logger, exc)
        return _err("retrieval_error", "search failed", details={"type": type(exc).__name__})


@router.post("/rag")
async def v1_rag(request: Request):
    guard = _guard(request)
    if guard is not None:
        return guard
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    denied = _resource_guard(
        request, model=_model_name(body), project_id=str(body.get("project_id") or ""),
    )
    if denied is not None:
        return denied
    query = str(body.get("query") or "").strip()
    if not query:
        return _err("bad_request", "query is required", status=400)
    try:
        top_k = int(body.get("top_k") or 5)
        if not 1 <= top_k <= 100:
            raise ValueError
    except (TypeError, ValueError):
        return _err("bad_request", "top_k must be an integer between 1 and 100", status=400)
    history = body.get("history") or []
    if not isinstance(history, list):
        return _err("bad_request", "history must be a JSON array", status=400)
    owner = _owner(request)
    model = _model_name(body)
    try:
        maximum_output = max(1, min(int(body.get("max_tokens") or 4096), 128_000))
    except (TypeError, ValueError):
        return _err("bad_request", "max_tokens must be an integer", status=400)
    request_key = _idem_key(request) or protocol.fingerprint(
        [owner, "legacy-rag", time.time_ns(), query]
    )[:40]
    estimate = _estimated_cost(model, max(1, (len(query) + 3) // 4), maximum_output)
    admission: dict[str, Any] | None = None
    reservation: dict[str, Any] | None = None
    completed_ok = False
    try:
        admission = _admit_execution(
            request, amount=estimate, request_id=f"rag:{request_key}",
        )
        limit_raw = os.environ.get("HASHMM_API_BUDGET_LIMIT_CNY", "").strip()
        reservation = protocol.reserve_budget(
            owner, run_id=f"rag_{request_key}", amount=estimate, currency="CNY",
            idempotency_key=f"rag:{request_key}:budget",
            limit=float(limit_raw) if limit_raw else None,
        )
        sources, contract = _sources_for(
            {**body, "rag": True, "top_k": top_k}, query, owner,
        )
        answer = _execute_text(query, history, sources)
        usage = _usage(query, answer)
        actual_cost = _estimated_cost(model, usage["input_tokens"], usage["output_tokens"])
        context = admission.get("context") if admission else None
        protocol.settle_usage(
            owner, reservation_id=str(reservation["id"]), run_id=f"rag_{request_key}",
            step_id="rag", provider="hashmm", service="rag", model=model,
            quantity=usage["total_tokens"], unit="token", estimated_cost=actual_cost,
            currency="CNY", price_version="request-estimate-v1",
            idempotency_key=f"rag:{request_key}:usage", metadata={"usage_source": "estimated"},
            access_key_id=context.key_id
            if isinstance(context, platform_access.AccessContext) else "",
        )
        if admission and admission.get("quota") and isinstance(context, platform_access.AccessContext):
            platform_access.settle_quota(
                context, str(admission["quota"]["id"]), actual_amount=actual_cost,
            )
        completed_ok = True
        return JSONResponse({
            "query": query, "answer": answer, "sources": sources,
            "num_sources": len(sources), "retrieval_contract": contract,
            "model": model, "usage": usage,
        }, headers=_rate_headers(request))
    except (protocol.ProtocolError, platform_access.AccessError) as exc:
        if reservation:
            protocol.release_budget(owner, reservation_id=str(reservation.get("id") or ""))
        return _access_error(exc) if isinstance(exc, platform_access.AccessError) else _protocol_error(exc)
    except Exception as exc:
        log_suppressed(logger, exc)
        if reservation:
            protocol.release_budget(owner, reservation_id=str(reservation.get("id") or ""))
        return _err("llm_error", "rag failed", details={"type": type(exc).__name__})
    finally:
        _release_admission(admission, failed=not completed_ok)
