"""Runtime settings store (V17 Phase 18e).

A simple key-value config table so things like search-API keys can be set from
the admin UI instead of requiring environment variables / restarts.

Resolution order for any setting: DB value (set via UI) → environment variable
→ default. This means env vars still work, but the UI can override at runtime.

Secret values (API keys) are masked when listed.
"""
from __future__ import annotations

import os

from hashmm.utils import get_logger
from hashmm.secrets_crypto import decrypt_secret, encrypt_secret

logger = get_logger("hashmm.settings")

# Known settings: key → (env_var_fallback, is_secret, description)
_KNOWN = {
    "search_backend": ("HASHMM_SEARCH_BACKEND", False, "强制搜索后端 (baidu/brave/exa/gemini/serper/tavily/doubao/duckduckgo，留空=自动)"),
    "serper_api_key": ("HASHMM_SERPER_API_KEY", True, "Serper.dev API Key（推荐，国内可用）"),
    "bing_api_key": ("HASHMM_BING_API_KEY", True, "已退役：仅保留旧密钥迁移读取，不再执行 Bing Search API"),
    "tavily_api_key": ("HASHMM_TAVILY_API_KEY", True, "Tavily API Key"),
    "baidu_search_api_key": ("HASHMM_BAIDU_SEARCH_API_KEY", True, "百度千帆 AI Search API Key"),
    "brave_search_api_key": ("HASHMM_BRAVE_SEARCH_API_KEY", True, "Brave Search API Key"),
    "exa_api_key": ("HASHMM_EXA_API_KEY", True, "Exa API Key"),
    "gemini_api_key": ("HASHMM_GEMINI_API_KEY", True, "Google Gemini Grounding API Key"),
    "web_fallback_enabled": ("HASHMM_WEB_FALLBACK", False, "检索不到时是否联网兜底 (1/0)"),
    "llm_task_routing": ("HASHMM_LLM_TASK_ROUTING", False,
                          "F11 任务→后端路由覆盖(JSON,如 {\"answer\":\"local\",\"keyword\":\"cloud\"}；"
                          "留空=用内置默认。值: local/cloud/auto)"),
    # ── V105+ IM 渠道（可在客户端 UI 配置，免改 AutoDL 启动命令）──
    "channel_feishu_enable": ("HASHMM_FEISHU_ENABLE", False, "飞书机器人启用 (1/0)"),
    "channel_feishu_app_id": ("HASHMM_FEISHU_APP_ID", False, "飞书自建应用 App ID"),
    "channel_feishu_app_secret": ("HASHMM_FEISHU_APP_SECRET", True, "飞书自建应用 App Secret"),
    "channel_feishu_encrypt_key": ("HASHMM_FEISHU_ENCRYPT_KEY", True, "飞书事件订阅 Encrypt Key（可选）"),
    "channel_feishu_verification_token": ("HASHMM_FEISHU_VERIFICATION_TOKEN", True, "飞书事件订阅 Verification Token"),
    "channel_wechat_enable": ("HASHMM_WECHAT_ENABLE", False, "微信 iLink 机器人启用 (1/0)"),
    "channel_wechat_channel_version": ("HASHMM_WECHAT_CHANNEL_VERSION", False, "微信 iLink 客户端版本号（默认 1.0.11）"),
    # ── 统一身份：Supabase 登录（客户端与 App 共用账号）──
    "supabase_url": ("HASHMM_SUPABASE_URL", False, "Supabase 项目 URL（如 https://xxx.supabase.co，配上即启用 Supabase 登录）"),
    "supabase_publishable_key": ("HASHMM_SUPABASE_PUBLISHABLE_KEY", False, "Supabase Publishable Key（sb_publishable_…）"),
    "supabase_admin_emails": ("HASHMM_SUPABASE_ADMIN_EMAILS", False, "Supabase 管理员邮箱白名单（逗号分隔，这些邮箱登录后给 admin 角色）"),
}

_ENC_PREFIX = "enc:"


def _stored_value(key: str, value: str) -> str:
    is_secret = bool(_KNOWN.get(key, (None, False, ""))[1])
    if not is_secret or not value:
        return value
    if value.startswith(_ENC_PREFIX):
        return value
    return _ENC_PREFIX + encrypt_secret(value)


def _resolved_value(key: str, value: str) -> str:
    is_secret = bool(_KNOWN.get(key, (None, False, ""))[1])
    if not is_secret or not value:
        return value
    if value.startswith(_ENC_PREFIX):
        return decrypt_secret(value[len(_ENC_PREFIX):])
    # Existing releases stored these rows as plaintext. Keep them readable and
    # migrate in place on the first authenticated use; listing still masks the
    # resolved value and never exposes the ciphertext.
    try:
        from hashmm.api import database as db
        import time
        with db._conn() as c:
            c.execute(
                "UPDATE app_settings SET value=?,updated_at=? WHERE key=? AND value=?",
                (_stored_value(key, value), time.time(), key, value),
            )
    except Exception as exc:
        logger.warning("secret setting migration deferred for %s: %s", key, exc)
    return value


def _ensure_table():
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT DEFAULT '',
                updated_at DOUBLE PRECISION DEFAULT (strftime('%s','now'))
            )
        """)


def get_setting(key: str, default: str = "") -> str:
    """Resolve a setting: DB → env var → default."""
    try:
        _ensure_table()
        from hashmm.api import database as db
        with db._conn() as c:
            row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        if row and row["value"]:
            return _resolved_value(key, str(row["value"]))
    except Exception as e:
        logger.debug(f"get_setting db miss: {e}")
    env_var = _KNOWN.get(key, (None, False, ""))[0]
    if env_var:
        v = os.environ.get(env_var, "")
        if v:
            return v
    return default


def set_setting(key: str, value: str) -> None:
    _ensure_table()
    import time
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute(
            "INSERT INTO app_settings (key,value,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, _stored_value(key, value), time.time()),
        )


def list_settings() -> list[dict]:
    """List known settings with masked secrets + whether each is configured."""
    out = []
    for key, (env_var, is_secret, desc) in _KNOWN.items():
        raw = get_setting(key)
        configured = bool(raw)
        if is_secret and raw:
            display = raw[:4] + "***" + raw[-2:] if len(raw) > 6 else "***"
        else:
            display = raw
        # where did it come from?
        source = ""
        if configured:
            from hashmm.api import database as db
            try:
                _ensure_table()
                with db._conn() as c:
                    row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
                source = "ui" if (row and row["value"]) else "env"
            except Exception:
                source = "env"
        out.append({
            "key": key, "description": desc, "is_secret": is_secret,
            "configured": configured, "display": display, "source": source,
        })
    return out
