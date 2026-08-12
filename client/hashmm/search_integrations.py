"""Owner-scoped search integrations.

The API key never leaves the server after it is written.  User-facing routes
only return a masked value and a deterministic readiness state; tool execution
resolves the decrypted value just before the outbound request.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any
from urllib.parse import urlsplit

from hashmm.api import database as db
from hashmm.secrets_crypto import decrypt_secret, encrypt_secret

_PROVIDERS = {"doubao", "baidu", "brave", "exa", "gemini", "serper", "tavily"}
_DOUBAO_VERSIONS = {"global", "custom"}
_DEFAULT_DOUBAO_BASE = "https://open.feedcoopapi.com/search_api"

_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "baidu": {"base_url": "https://qianfan.baidubce.com/v2/ai_search/web_search", "count": 8},
    "brave": {"base_url": "https://api.search.brave.com/res/v1/web/search", "count": 8},
    "exa": {"base_url": "https://api.exa.ai/search", "count": 8},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/interactions", "count": 8,
               "model": "gemini-2.5-flash"},
    "serper": {"base_url": "https://google.serper.dev/search", "count": 8},
    "tavily": {"base_url": "https://api.tavily.com/search", "count": 8},
}

_PROVIDER_LABELS = {
    "doubao": "豆包搜索兼容接口（Beta）", "baidu": "百度千帆 AI Search",
    "brave": "Brave Search", "exa": "Exa Neural Search",
    "gemini": "Google Search Grounding", "serper": "Serper Google Results",
    "tavily": "Tavily Agent Search",
}


def _ensure_table() -> None:
    with db._conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_search_integrations (
                owner_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                enc_api_key TEXT NOT NULL DEFAULT '',
                config_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(owner_id, provider)
            )
            """
        )


def _clean_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    if value not in _PROVIDERS:
        raise ValueError("不支持的搜索服务")
    return value


def _clean_doubao_config(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    version = str(source.get("version") or "global").strip().lower()
    if version not in _DOUBAO_VERSIONS:
        raise ValueError("搜索版本只能是 global 或 custom")
    base_url = str(source.get("base_url") or _DEFAULT_DOUBAO_BASE).strip().rstrip("/")
    if not (base_url.startswith("https://") or base_url.startswith("http://127.0.0.1")):
        raise ValueError("搜索服务地址必须使用 HTTPS")
    hostname = (urlsplit(base_url).hostname or "").lower()
    if hostname not in {"open.feedcoopapi.com", "127.0.0.1"} and os.environ.get(
        "HASHMM_ALLOW_CUSTOM_SEARCH_ENDPOINTS", ""
    ).lower() not in {"1", "true", "yes", "on"}:
        raise ValueError("自定义搜索地址需由管理员显式启用")
    snippet_length = max(100, min(int(source.get("snippet_length") or 800), 2000))
    count = max(1, min(int(source.get("count") or 8), 20))
    auth_level = source.get("auth_level")
    if auth_level in ("", None):
        auth_level = None
    else:
        auth_level = max(1, min(int(auth_level), 4))
    return {
        "version": version,
        "base_url": base_url[:500],
        "snippet_length": snippet_length,
        "count": count,
        "auth_level": auth_level,
    }


def _clean_config(provider: str, value: dict[str, Any] | None) -> dict[str, Any]:
    if provider == "doubao":
        return _clean_doubao_config(value)
    source = value if isinstance(value, dict) else {}
    defaults = _PROVIDER_DEFAULTS[provider]
    # Provider endpoints are fixed by default. A custom endpoint is accepted
    # only over HTTPS so a user cannot turn the backend into a plaintext secret
    # forwarder.
    base_url = str(source.get("base_url") or defaults["base_url"]).strip().rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError("搜索服务地址必须使用 HTTPS")
    if base_url != str(defaults["base_url"]).rstrip("/") and os.environ.get(
        "HASHMM_ALLOW_CUSTOM_SEARCH_ENDPOINTS", ""
    ).lower() not in {"1", "true", "yes", "on"}:
        raise ValueError("生产模式只允许该提供商的官方 HTTPS 地址")
    result: dict[str, Any] = {
        "base_url": base_url[:500],
        "count": max(1, min(int(source.get("count") or defaults.get("count") or 8), 20)),
    }
    if provider == "gemini":
        model = str(source.get("model") or defaults["model"]).strip()
        if not model or len(model) > 120:
            raise ValueError("Gemini 模型名称无效")
        result["model"] = model
    return result


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) < 7:
        return secret[:1] + "***"
    return secret[:4] + "…" + secret[-2:]


def default_search_config(provider: str) -> dict[str, Any]:
    name = _clean_provider(provider)
    return _clean_config(name, {})


def list_search_providers() -> list[dict[str, Any]]:
    return [{"provider": name, "label": _PROVIDER_LABELS[name],
             "kind": "compatibility" if name == "doubao" else "official_or_managed"}
            for name in sorted(_PROVIDERS)]


def get_search_integration(
    owner_id: str,
    provider: str,
    *,
    include_secret: bool = False,
) -> dict[str, Any] | None:
    owner = str(owner_id or "").strip()
    name = _clean_provider(provider)
    if not owner:
        return None
    _ensure_table()
    with db._conn() as conn:
        row = conn.execute(
            "SELECT enabled,enc_api_key,config_json,created_at,updated_at "
            "FROM user_search_integrations WHERE owner_id=? AND provider=?",
            (owner, name),
        ).fetchone()
    if not row:
        return None
    try:
        config = json.loads(str(row["config_json"] or "{}"))
    except (TypeError, ValueError):
        config = {}
    secret = decrypt_secret(str(row["enc_api_key"] or "")) if row["enc_api_key"] else ""
    result: dict[str, Any] = {
        "provider": name,
        "enabled": bool(row["enabled"]),
        "configured": bool(secret),
        "masked_api_key": _mask(secret),
        "config": _clean_config(name, config),
        "created_at": float(row["created_at"] or 0),
        "updated_at": float(row["updated_at"] or 0),
    }
    if include_secret:
        result["api_key"] = secret
    return result


def set_search_integration(
    owner_id: str,
    provider: str,
    *,
    api_key: str | None,
    enabled: bool,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    owner = str(owner_id or "").strip()
    name = _clean_provider(provider)
    if not owner:
        raise ValueError("用户身份无效")
    clean_config = _clean_config(name, config)
    existing = get_search_integration(owner, name, include_secret=True)
    secret = str(api_key or "").strip()
    if not secret and existing:
        secret = str(existing.get("api_key") or "")
    if not secret:
        raise ValueError("请填写 API Key")
    now = time.time()
    encrypted = encrypt_secret(secret)
    _ensure_table()
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO user_search_integrations"
            "(owner_id,provider,enabled,enc_api_key,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner_id,provider) DO UPDATE SET "
            "enabled=excluded.enabled,enc_api_key=excluded.enc_api_key,"
            "config_json=excluded.config_json,updated_at=excluded.updated_at",
            (
                owner,
                name,
                1 if enabled else 0,
                encrypted,
                json.dumps(clean_config, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )
    return get_search_integration(owner, name) or {}


def delete_search_integration(owner_id: str, provider: str) -> bool:
    owner = str(owner_id or "").strip()
    name = _clean_provider(provider)
    _ensure_table()
    with db._conn() as conn:
        cursor = conn.execute(
            "DELETE FROM user_search_integrations WHERE owner_id=? AND provider=?",
            (owner, name),
        )
    return cursor.rowcount > 0


def integration_cache_token(owner_id: str, provider: str) -> str:
    item = get_search_integration(owner_id, provider)
    if not item:
        return "none"
    material = (
        f"{item['enabled']}:{item['configured']}:{item['updated_at']}:"
        f"{json.dumps(item['config'], sort_keys=True, ensure_ascii=False)}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
