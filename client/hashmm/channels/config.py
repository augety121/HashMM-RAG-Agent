"""hashmm/channels/config.py — 渠道配置读取（DB → 环境变量 → 默认）。

让微信/飞书的开关与凭证可以**在客户端 UI 里配置并存进 DB**（app_settings 表），不必再写到
AutoDL 启动命令的环境变量里。解析顺序：DB(settings_store，UI 可改) → 环境变量(向后兼容) → 默认。

放在 channels 包内、对 settings_store 的依赖用 try/except 兜底 → 渠道模块仍可脱离 api 层单测，
且永不抛错。
"""
from __future__ import annotations

import os


def _get(store_key: str, env_var: str, default: str = "") -> str:
    """DB(settings_store) → 环境变量 → 默认。settings_store 不可用时退化到纯环境变量。永不抛错。"""
    try:
        from hashmm.api import settings_store
        v = settings_store.get_setting(store_key, "")
        if v:
            return v
    except Exception:
        pass
    try:
        return os.environ.get(env_var, "") or default
    except Exception:
        return default


def feishu(name: str) -> str:
    """读飞书配置项。name ∈ {ENABLE, APP_ID, APP_SECRET, ENCRYPT_KEY, VERIFICATION_TOKEN}。"""
    return _get(f"channel_feishu_{name.lower()}", f"HASHMM_FEISHU_{name}")


def wechat(name: str) -> str:
    """读微信配置项。name ∈ {ENABLE, CHANNEL_VERSION}。"""
    return _get(f"channel_wechat_{name.lower()}", f"HASHMM_WECHAT_{name}")


def feishu_enabled() -> bool:
    return feishu("ENABLE") == "1" and bool(feishu("APP_ID") and feishu("APP_SECRET"))


def wechat_enabled() -> bool:
    return wechat("ENABLE") == "1"
