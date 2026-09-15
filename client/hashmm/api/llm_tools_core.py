"""llm_tools_core — 原生 tools 调用的纯函数核心（V92，零 fastapi 依赖）。
路由薄壳在 routes/llm_raw.py；这里可在无 web 栈环境单测（依赖全可注入）。
"""
from __future__ import annotations

import os

from hashmm.api import database as db

MAX_TOKENS = 8192   # V103.90 兜底默认（原 2048 太小：写大文件时 tool_call 的 arguments 被截断 → JSON 残缺 → 前端解析失败 → write_file 收到 undefined path）


def default_model_getter() -> dict | None:
    """与 model_manager.get_active_llm_fn 同源：默认模型 -> env 回退。"""
    model = db.get_default_model()
    if model:
        return model
    api_key = os.environ.get("LLM_API_KEY", "")
    if api_key and not api_key.startswith("sk-your-"):
        return {"api_key": api_key,
                "base_url": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
                "model_name": os.environ.get("LLM_MODEL", "deepseek-chat")}
    return None


def default_client_factory(model: dict):
    from openai import OpenAI
    return OpenAI(api_key=model.get("api_key") or "no-key",
                  base_url=model.get("base_url") or None, timeout=120)


_IMG_PLACEHOLDER = "[此处原为截图/图片；当前对话模型不处理图片，已省略——视觉理解由独立的 vision_model 通路单独处理]"


def strip_image_content(messages: list) -> list:
    """把多模态消息里的 image_url 块剥成文本占位，再发给【纯文本】主模型。

    架构上主对话/tools 模型是文本模型（视觉走独立 vision_model 预分析后注入文本），
    因此 tools 通路里的 image_url 一律不应直达主模型——否则像 deepseek 这类文本模型会
    报 400 `unknown variant image_url`（一条历史截图消息就能让整轮 tools 调用 502）。
    无条件剥离，幂等；content 为 str 时原样返回。
    """
    if not messages:
        return messages
    out = []
    for msg in messages:
        c = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(c, list):
            texts, had_img = [], False
            for blk in c:
                if isinstance(blk, dict) and blk.get("type") in ("image_url", "image", "input_image"):
                    had_img = True
                    continue
                if isinstance(blk, dict) and blk.get("type") == "text":
                    texts.append(str(blk.get("text") or ""))
                elif isinstance(blk, str):
                    texts.append(blk)
            joined = "\n".join(t for t in texts if t)
            if had_img:
                joined = (joined + "\n" + _IMG_PLACEHOLDER).strip() if joined else _IMG_PLACEHOLDER
            nm = dict(msg)
            nm["content"] = joined
            out.append(nm)
        else:
            out.append(msg)
    return out


def run_tools_chat(messages: list, tools: list,
                   model_getter=None, client_factory=None) -> dict:
    """一次带 tools 的 LLM 调用，返回 OpenAI 风格 assistant message dict。"""
    model = (model_getter or default_model_getter)()
    if not model:
        raise RuntimeError("后端未配置任何可用模型（管理后台-模型管理里添加）")
    # V173：剥离 image_url（主模型为文本模型，截图直达会 400→502）。详见 strip_image_content。
    messages = strip_image_content(messages)
    client = (client_factory or default_client_factory)(model)
    # V103.90 优先用模型自身配置的 max_tokens（管理后台通常设了 16384），缺失才用兜底默认。
    # 这样写大文件时 tool_call 的 arguments(整份内容)不会被 2048 截断成残缺 JSON。
    try:
        _mt = int(model.get("max_tokens") or 0)
    except (TypeError, ValueError):
        _mt = 0
    max_tokens = _mt if _mt > 0 else MAX_TOKENS
    resp = client.chat.completions.create(
        model=model.get("model_name") or model.get("name") or "deepseek-chat",
        messages=messages, tools=tools or None,
        temperature=0.2, max_tokens=max_tokens)
    m = resp.choices[0].message
    out: dict = {"role": "assistant", "content": m.content or ""}
    calls = getattr(m, "tool_calls", None)
    if calls:
        out["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls]
    return out
