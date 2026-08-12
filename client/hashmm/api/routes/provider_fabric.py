"""Owner-scoped Provider Fabric and authorised Sub2API connection API."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.api import provider_fabric as fabric

router = APIRouter(prefix="/api/provider-fabric", tags=["provider-fabric"])


class ConnectionCreate(BaseModel):
    name: str
    kind: str = "openai_compatible"
    base_url: str
    wire_api: str = "chat_completions"
    api_key: str = ""
    model_alias: str = "default"
    upstream_model: str
    priority: int = 100
    weight: int = 100
    max_concurrency: int = Field(4, ge=1, le=128)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class ChannelCreate(BaseModel):
    model_alias: str = "default"
    upstream_model: str
    priority: int = Field(100, ge=0, le=10_000)
    weight: int = Field(100, ge=1, le=10_000)
    max_concurrency: int = Field(4, ge=1, le=128)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class RoutingPolicyUpdate(BaseModel):
    project_id: str = ""
    strategy: str = "sticky_health"
    retry_before_first_token: bool = True
    max_attempts: int = Field(2, ge=1, le=5)
    config: dict[str, Any] = Field(default_factory=dict)


def _owner(request: Request) -> tuple[dict, str]:
    user = require_auth(request)
    return user, str(user["uid"])


@router.get("")
async def provider_overview(request: Request):
    _, owner_id = _owner(request)
    return fabric.overview(owner_id)


@router.post("", status_code=201)
async def add_provider_connection(body: ConnectionCreate, request: Request):
    user, owner_id = _owner(request)
    try:
        item = fabric.create_connection(owner_id, body.model_dump())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "同名 Provider 连接已存在") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(user["uid"], user.get("sub", ""), "provider_connection_create", item.get("id", ""))
    return {"ok": True, "connection": item}


@router.delete("/{connection_id}")
async def remove_provider_connection(connection_id: str, request: Request):
    user, owner_id = _owner(request)
    if not fabric.delete_connection(owner_id, connection_id):
        # Deliberately indistinguishable from somebody else's object.
        raise HTTPException(404, "Provider 连接不存在")
    db.audit(user["uid"], user.get("sub", ""), "provider_connection_delete", connection_id)
    return {"ok": True}


@router.post("/{connection_id}/channels", status_code=201)
async def add_provider_channel(connection_id: str, body: ChannelCreate, request: Request):
    user, owner_id = _owner(request)
    try:
        channel = fabric.add_channel(owner_id, connection_id, body.model_dump())
    except LookupError as exc:
        raise HTTPException(404, "Provider 连接不存在") from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "该模型通道已经存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(user["uid"], user.get("sub", ""), "provider_channel_create", channel.get("id", ""))
    return {"ok": True, "channel": channel}


@router.put("/policies/{model_alias}")
async def update_provider_policy(model_alias: str, body: RoutingPolicyUpdate, request: Request):
    user, owner_id = _owner(request)
    try:
        policy = fabric.set_routing_policy(owner_id, model_alias, body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.audit(user["uid"], user.get("sub", ""), "provider_policy_update", model_alias[:100])
    return {"ok": True, "policy": policy}


@router.get("/routes/{model_alias}/preview")
async def preview_provider_route(model_alias: str, request: Request, project_id: str = ""):
    """Exercise the real selector without consuming capacity or exposing credentials."""
    _, owner_id = _owner(request)
    try:
        route = fabric.select_route(
            owner_id, model_alias,
            request_id=f"preview:{getattr(request.state, 'request_id', '') or 'request'}",
            project_id=project_id, acquire_lease=False,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    route["channel"].pop("api_key", None)
    route["preview"] = True
    return route


@router.post("/{connection_id}/probe")
async def probe_provider_connection(connection_id: str, request: Request):
    user, owner_id = _owner(request)
    config = fabric.connection_as_model(owner_id, connection_id)
    if not config:
        raise HTTPException(404, "Provider 连接不存在或没有可用通道")
    try:
        fabric.validate_endpoint(config["base_url"],
                                 (fabric.get_connection(owner_id, connection_id) or {}).get("kind", ""),
                                 resolve_dns=True)
    except ValueError as exc:
        fabric.record_probe(owner_id, connection_id, ok=False, latency_ms=0, error_code="unsafe_endpoint")
        raise HTTPException(400, str(exc)) from exc
    from hashmm.api.model_manager import test_model_connection
    result = test_model_connection(config)
    fabric.record_probe(owner_id, connection_id, ok=bool(result.get("ok")),
                        latency_ms=int(result.get("latency_ms") or 0),
                        error_code=str(result.get("code") or ""))
    db.audit(user["uid"], user.get("sub", ""), "provider_connection_probe",
             f"{connection_id}:{'ok' if result.get('ok') else 'failed'}")
    return {**result, "connection_id": connection_id}


@router.post("/{connection_id}/activate")
async def activate_provider_connection(connection_id: str, request: Request):
    """Materialise the selected channel into the existing Chat model plane.

    This is intentionally explicit: storing a provider connection must never
    silently change which paid endpoint receives a user's next prompt.
    """
    user, owner_id = _owner(request)
    config = fabric.connection_as_model(owner_id, connection_id)
    if not config:
        raise HTTPException(404, "Provider 连接不存在或没有可用通道")
    existing_id = ""
    for model in db.list_models():
        if model.get("created_by") != user.get("sub", ""):
            continue
        try:
            private = db.get_model(str(model.get("id") or "")) or {}
            options = private.get("config") or private.get("config_json") or {}
            if isinstance(options, str):
                import json
                options = json.loads(options or "{}")
            if options.get("provider_connection_id") == connection_id:
                existing_id = str(model["id"])
                break
        except Exception:
            continue
    if not existing_id:
        created = db.create_model(
            config["name"], config["provider"], config["base_url"], config["api_key"],
            config["model_name"], created_by=user.get("sub", ""),
            temperature=config["temperature"], max_tokens=config["max_tokens"],
            config=config["config"],
        )
        existing_id = str(created["id"])
    else:
        db.update_model(
            existing_id, name=config["name"], provider=config["provider"],
            base_url=config["base_url"], api_key=config["api_key"],
            model_name=config["model_name"], temperature=config["temperature"],
            max_tokens=config["max_tokens"], config=config["config"],
        )
    from hashmm.api.settings_store import set_setting
    set_setting(f"user_model:{owner_id}", existing_id)
    from hashmm.api.model_manager import invalidate_model_catalog_cache
    invalidate_model_catalog_cache()
    db.audit(user["uid"], user.get("sub", ""), "provider_connection_activate", connection_id)
    return {"ok": True, "connection_id": connection_id, "model_id": existing_id, "preferred": True}
