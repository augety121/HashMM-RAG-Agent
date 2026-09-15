"""HashMM-RAG v29 — Multi-model manager with enhanced tool calling.

Supports OpenAI-compatible APIs: DeepSeek, OpenAI, Qwen, Zhipu, Moonshot, Ollama, etc.
Model configs are loaded from the database. The .env provides a default fallback.

v29 additions:
  - stream_with_tools(messages, tools): Streaming + function calling
  - quick_call(system, user, max_tokens): Fast lightweight call
  - count_tokens(messages): Token count estimation
"""
from __future__ import annotations
import hashlib
import os, time, json, threading
from typing import Callable
from hashmm.api import database as db
from hashmm.model_providers import (
    ProviderConfigError,
    WIRE_ANTHROPIC,
    WIRE_RESPONSES,
    classify_provider_error,
    normalize_model_config,
    provider_capability_profile,
    provider_presets,
    request_options,
)
from hashmm.model_runtime import (
    ModelRequirements,
    current_runtime_mode,
    normalize_usage,
    rank_model_configs,
)
from hashmm.utils import get_logger
from hashmm.llm_timeout import client_timeout

logger = get_logger("hashmm.model_manager")

# Legacy-shaped export retained for existing clients.  The single source of
# truth now lives in model_providers.py and is also returned by /models/providers.
PROVIDER_PRESETS = provider_presets()

_client_cache: dict[str, object] = {}
_model_catalog_cache: tuple[float, list[dict]] = (0.0, [])


def invalidate_model_catalog_cache() -> None:
    """Invalidate request-routing metadata after any model CRUD change."""
    global _model_catalog_cache
    _model_catalog_cache = (0.0, [])


def _fabric_routed_config(
    model_cfg: dict,
    *,
    owner_id: str,
    request_id: str,
    sticky_key: str,
    project_id: str,
    acquire_lease: bool = True,
) -> tuple[dict, dict | None]:
    """Resolve a Provider Fabric model before output starts.

    Legacy models without a fabric receipt keep their exact stored endpoint.
    An owner mismatch fails closed rather than exposing somebody else's
    encrypted upstream credential.
    """
    raw = model_cfg.get("config") or model_cfg.get("config_json") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
    options = raw if isinstance(raw, dict) else {}
    connection_id = str(options.get("provider_connection_id") or "")
    fabric_owner = str(options.get("provider_owner_id") or "")
    if not connection_id:
        return model_cfg, None
    if not owner_id or fabric_owner != owner_id:
        raise ProviderConfigError("Provider Fabric 所有者与当前请求不一致")
    from hashmm.api import provider_fabric
    route = provider_fabric.select_route(
        owner_id,
        str(options.get("model_alias") or model_cfg.get("model_name") or "default"),
        request_id=request_id,
        sticky_key=sticky_key,
        project_id=project_id,
        connection_id=connection_id,
        acquire_lease=acquire_lease,
    )
    channel = route["channel"]
    merged = dict(model_cfg)
    merged.update({
        "base_url": channel["base_url"], "api_key": channel.get("api_key") or "",
        "model_name": channel["upstream_model"], "wire_api": channel["wire_api"],
        "provider": "anthropic" if channel["wire_api"] == WIRE_ANTHROPIC else "custom",
    })
    merged_options = dict(options)
    merged_options["wire_api"] = channel["wire_api"]
    merged_options["provider_route"] = {
        "request_id": route["request_id"], "lease_id": route["lease_id"],
        "channel_id": channel["id"], "retry_boundary": route["retry_boundary"],
    }
    merged["config"] = merged_options
    merged["config_json"] = json.dumps(merged_options, ensure_ascii=False, separators=(",", ":"))
    return merged, route


class _ProviderRoutedCallable:
    """Acquire capacity for every real upstream call and preserve LLM methods."""

    def __init__(self, config: dict, preview: Callable, *, owner_id: str, request_id: str,
                 sticky_key: str, project_id: str):
        self._config = dict(config)
        self._preview = preview
        self._owner_id = owner_id
        self._base_request_id = request_id
        self._sticky_key = sticky_key
        self._project_id = project_id
        self._counter = 0
        self._counter_lock = threading.Lock()
        self.model_name = getattr(preview, "model_name", config.get("model_name", ""))
        self.provider_route: dict[str, object] = {}

    def _request_id(self) -> str:
        with self._counter_lock:
            self._counter += 1
            return f"{self._base_request_id}:call:{self._counter}"

    def _prepare(self):
        call_request_id = self._request_id()
        call_config, route = _fabric_routed_config(
            self._config, owner_id=self._owner_id, request_id=call_request_id,
            sticky_key=self._sticky_key, project_id=self._project_id, acquire_lease=True,
        )
        inner = make_llm_fn_from_model(call_config)
        if inner is None:
            from hashmm.api import provider_fabric as fabric
            fabric.release_lease(self._owner_id, str(route.get("lease_id") or ""), state="failed")
            raise ProviderConfigError("Provider channel could not create an LLM callable")
        self.provider_route = {
            "request_id": call_request_id, "lease_id": "per_call",
            "channel_id": route["channel"]["id"], "retry_boundary": route["retry_boundary"],
        }
        return call_request_id, inner, route, time.monotonic()

    def _finish(self, request_id: str, route: dict, started: float, *, outcome: str,
                first_token_seen: bool, error_code: str = "") -> None:
        from hashmm.api import provider_fabric as fabric
        fabric.record_attempt(
            self._owner_id, request_id, str(route["channel"]["id"]), attempt=1,
            outcome=outcome, error_code=error_code, first_token_seen=first_token_seen,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        fabric.release_lease(
            self._owner_id, str(route.get("lease_id") or ""),
            state="completed" if outcome == "success" else "failed",
        )

    def _invoke(self, method: str, *args, **kwargs):
        request_id, inner, route, started = self._prepare()
        try:
            target = inner if not method else getattr(inner, method)
            output = target(*args, **kwargs)
        except Exception as exc:
            self._finish(request_id, route, started, outcome="failed",
                         first_token_seen=False, error_code=type(exc).__name__)
            raise
        self._finish(request_id, route, started, outcome="success", first_token_seen=bool(output))
        return output

    def _stream(self, method: str, *args, **kwargs):
        # Acquisition happens on first iteration, so an abandoned generator
        # does not consume channel capacity until TTL expiry.
        request_id, inner, route, started = self._prepare()
        seen = False
        try:
            target = getattr(inner, method)
            for item in target(*args, **kwargs):
                seen = True
                yield item
        except Exception as exc:
            self._finish(request_id, route, started, outcome="failed",
                         first_token_seen=seen, error_code=type(exc).__name__)
            raise
        else:
            self._finish(request_id, route, started, outcome="success", first_token_seen=seen)

    def __call__(self, prompt: str):
        return self._invoke("", prompt)

    def chat(self, *args, **kwargs):
        return self._invoke("chat", *args, **kwargs)

    def call_with_tools(self, *args, **kwargs):
        return self._invoke("call_with_tools", *args, **kwargs)

    def quick_call(self, *args, **kwargs):
        return self._invoke("quick_call", *args, **kwargs)

    def stream(self, *args, **kwargs):
        return self._stream("stream", *args, **kwargs)

    def stream_with_tools(self, *args, **kwargs):
        return self._stream("stream_with_tools", *args, **kwargs)

    def count_tokens(self, *args, **kwargs):
        counter = getattr(self._preview, "count_tokens", None)
        return counter(*args, **kwargs) if callable(counter) else 0


def _configured_model_catalog() -> list[dict]:
    """Short-lived local cache; credentials never leave the backend process."""
    global _model_catalog_cache
    expires, cached = _model_catalog_cache
    now = time.monotonic()
    if cached and expires > now:
        return [dict(item) for item in cached]
    rows: list[dict] = []
    for public in db.list_models():
        model_id = str(public.get("id") or "")
        if not model_id:
            continue
        full = db.get_model(model_id)
        if full:
            rows.append(full)
    _model_catalog_cache = (now + 30.0, rows)
    return [dict(item) for item in rows]


def _attach_provider_metadata(call: Callable, model_cfg: dict) -> Callable:
    """Keep the provider contract attached across Chat/Agent call sites."""
    profile = provider_capability_profile(model_cfg)
    call.provider = profile.get("provider", "")       # type: ignore[attr-defined]
    call.provider_profile = profile                    # type: ignore[attr-defined]
    call.wire_api = profile.get("wire_api", "")       # type: ignore[attr-defined]
    call.runtime_mode = current_runtime_mode           # type: ignore[attr-defined]
    call.model_config_id = str(model_cfg.get("id") or "")  # type: ignore[attr-defined]
    return call

def _get_client(base_url: str, api_key: str):
    """Get or create an OpenAI client for the given base_url."""
    # Never key the cache by a short credential prefix: unrelated accounts can
    # share that prefix and accidentally reuse the wrong authenticated client.
    fingerprint = hashlib.sha256(
        f"{base_url}\0{api_key or 'no-key'}".encode("utf-8")
    ).hexdigest()
    cache_key = fingerprint
    if cache_key not in _client_cache:
        try:
            from openai import OpenAI
            _client_cache[cache_key] = OpenAI(api_key=api_key or "no-key", base_url=base_url, timeout=client_timeout())
        except ImportError:
            raise RuntimeError("openai package not installed")
    return _client_cache[cache_key]


class _AnthropicChoice:
    """把 Anthropic Messages 响应包装成 OpenAI choices[0] 的形状，
    使既有 Agent 循环（读 .message.content / .message.tool_calls / .finish_reason）零改动消费。"""
    class _Fn:
        def __init__(self, name, arguments): self.name = name; self.arguments = arguments
    class _ToolCall:
        def __init__(self, id, name, arguments):
            self.id = id; self.type = "function"
            self.function = _AnthropicChoice._Fn(name, arguments)
    class _Msg:
        def __init__(self, content, tool_calls):
            self.content = content; self.role = "assistant"
            self.tool_calls = tool_calls or None

    def __init__(self, resp):
        text_parts: list[str] = []
        tool_calls: list = []
        for b in getattr(resp, "content", []) or []:
            bt = getattr(b, "type", "")
            if bt == "text":
                text_parts.append(getattr(b, "text", "") or "")
            elif bt == "tool_use":
                try:
                    args = json.dumps(getattr(b, "input", {}) or {}, ensure_ascii=False)
                except Exception:
                    args = "{}"
                tool_calls.append(_AnthropicChoice._ToolCall(getattr(b, "id", ""), getattr(b, "name", ""), args))
        self.message = _AnthropicChoice._Msg("".join(text_parts), tool_calls)
        sr = getattr(resp, "stop_reason", "") or ""
        self.finish_reason = "tool_calls" if sr == "tool_use" else ("length" if sr == "max_tokens" else "stop")
        try:
            u = getattr(resp, "usage", None)
            if u is not None:
                detail = normalize_usage(u).as_legacy()
                self._hashmm_usage = {
                    "prompt_tokens": detail["prompt_tokens"],
                    "completion_tokens": detail["completion_tokens"],
                }
                self._hashmm_usage_details = detail
        except Exception:
            pass


def _make_anthropic_fn(model_cfg: dict):
    """原生 Anthropic（Claude）适配器 —— 与 OpenAI 兼容工厂【同样的】挂载方法接口，
    在内部翻译 OpenAI 风格的 messages/tools ↔ Anthropic Messages（system 顶层参数、
    tool_use / tool_result 块、流式事件）。这样 Agent 循环对接 Claude 零改动。

    需 `anthropic` 包（见 requirements-optional.txt）；未安装或缺 key/model 则返回 None。
    """
    api_key = model_cfg.get("api_key", "")
    base_url = model_cfg.get("base_url", "") or "https://api.anthropic.com"
    model_name = model_cfg.get("model_name", "")
    temperature = model_cfg.get("temperature", 0.1)
    max_tokens = int(model_cfg.get("max_tokens", 4096) or 4096)
    provider_options = model_cfg.get("provider_options") or {}
    # 支持逗号分隔多 key（这里取第一个；如需轮换可扩展）
    api_keys = [k.strip() for k in api_key.split(",") if k.strip()] if api_key else []
    api_key = api_keys[0] if api_keys else ""
    if not model_name or not api_key or api_key.startswith("sk-your-"):
        logger.warning(f"Anthropic model {model_name or '(none)'}: API key/model 未配置")
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic 包未安装 —— 请 `pip install anthropic` 后重启后端")
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=client_timeout())
    except Exception as e:
        logger.warning(f"创建 Anthropic 客户端失败：{e}")
        return None

    # ── OpenAI messages → (system_str, anthropic_messages) ──
    def _split(messages: list[dict]):
        system_parts: list[str] = []
        out: list[dict] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                if isinstance(content, str) and content:
                    system_parts.append(content)
                continue
            if role == "tool":
                out.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                }]})
                continue
            if role == "assistant" and m.get("tool_calls"):
                blocks: list[dict] = []
                if isinstance(content, str) and content.strip():
                    blocks.append({"type": "text", "text": content})
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    raw = fn.get("arguments", "{}")
                    try:
                        parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except Exception:
                        parsed = {}
                    blocks.append({"type": "tool_use", "id": tc.get("id", ""),
                                   "name": fn.get("name", ""), "input": parsed})
                out.append({"role": "assistant", "content": blocks})
                continue
            if isinstance(content, list):
                out.append({"role": role or "user", "content": content})   # 多模态块原样透传
            else:
                out.append({"role": role or "user", "content": content or ""})
        # Anthropic 要求首条为 user
        if out and out[0]["role"] == "assistant":
            out.insert(0, {"role": "user", "content": "(开始)"})
        return ("\n".join(system_parts).strip() or None, out)

    def _tools_to_anthropic(tools: list[dict] | None) -> list[dict]:
        res: list[dict] = []
        for t in tools or []:
            fn = t.get("function", t) if isinstance(t, dict) else {}
            res.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return res

    def _text_of(resp) -> str:
        return "".join(getattr(b, "text", "") or "" for b in (getattr(resp, "content", []) or [])
                       if getattr(b, "type", "") == "text")

    def _runtime_kwargs() -> dict:
        """Apply Anthropic thinking only when this exact model opted in."""
        if provider_options.get("supports_reasoning") is not True:
            return {}
        try:
            configured = int(provider_options.get("anthropic_thinking_budget_tokens") or 0)
        except (TypeError, ValueError, OverflowError):
            configured = 0
        if configured < 1024:
            return {}
        mode = current_runtime_mode()
        if mode == "fast":
            return {}
        budget = configured if mode == "deep" else min(configured, 4096)
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    def _apply_runtime(kw: dict) -> dict:
        runtime = _runtime_kwargs()
        if runtime:
            # Anthropic extended-thinking requests do not accept arbitrary
            # sampling settings.  Keep the saved temperature for normal calls.
            kw.pop("temperature", None)
            kw.update(runtime)
        return kw

    def call(prompt: str) -> str:
        sys_str, msgs = _split([{"role": "user", "content": prompt}])
        kw = _apply_runtime(dict(model=model_name, max_tokens=max_tokens, temperature=temperature, messages=msgs))
        if sys_str: kw["system"] = sys_str
        return _text_of(client.messages.create(**kw))

    def chat(messages: list[dict], max_tok: int | None = None) -> str:
        sys_str, msgs = _split(messages)
        kw = _apply_runtime(dict(model=model_name, max_tokens=max_tok or max_tokens, temperature=temperature, messages=msgs))
        if sys_str: kw["system"] = sys_str
        return _text_of(client.messages.create(**kw))

    def quick_call(system_prompt: str, user_prompt: str, max_tok: int = 200) -> str:
        kw = dict(model=model_name, max_tokens=max_tok, temperature=0.0,
                  messages=[{"role": "user", "content": user_prompt}])
        if system_prompt: kw["system"] = system_prompt
        text = _text_of(client.messages.create(**kw))
        if not str(text or "").strip():
            # V281：空内容兜底——放大 max_tokens 重试一次（对齐 OpenAI 版行为）
            kw["max_tokens"] = max(1200, max_tok * 6)
            try:
                text = _text_of(client.messages.create(**kw))
            except Exception:
                pass
        return text

    def stream(messages: list[dict], *, temp_override: float | None = None):
        sys_str, msgs = _split(messages)
        kw = _apply_runtime(dict(model=model_name, max_tokens=max_tokens,
                  temperature=temp_override if temp_override is not None else temperature, messages=msgs))
        if sys_str: kw["system"] = sys_str
        with client.messages.stream(**kw) as s:
            for text in s.text_stream:
                if text:
                    yield text

    def call_with_tools(messages: list[dict], tools: list[dict] | None = None,
                        tool_choice: str = "auto") -> object:
        sys_str, msgs = _split(messages)
        kw = _apply_runtime(dict(model=model_name, max_tokens=max_tokens, temperature=temperature, messages=msgs))
        if sys_str: kw["system"] = sys_str
        if tools and tool_choice != "none":
            kw["tools"] = _tools_to_anthropic(tools)
            kw["tool_choice"] = {"type": "auto"}
        return _AnthropicChoice(client.messages.create(**kw))

    def stream_with_tools(messages: list[dict], tools: list[dict] | None = None):
        sys_str, msgs = _split(messages)
        kw = _apply_runtime(dict(model=model_name, max_tokens=max_tokens, temperature=temperature, messages=msgs))
        if sys_str: kw["system"] = sys_str
        if tools:
            kw["tools"] = _tools_to_anthropic(tools)
            kw["tool_choice"] = {"type": "auto"}
        cur: dict = {}
        had_tool = False
        with client.messages.stream(**kw) as s:
            for ev in s:
                et = getattr(ev, "type", "")
                if et == "content_block_start":
                    blk = getattr(ev, "content_block", None)
                    if getattr(blk, "type", "") == "tool_use":
                        had_tool = True
                        cur = {"id": getattr(blk, "id", ""), "name": getattr(blk, "name", ""), "args": ""}
                        yield {"type": "tool_call_start", "id": cur["id"], "name": cur["name"]}
                elif et == "content_block_delta":
                    d = getattr(ev, "delta", None)
                    dt = getattr(d, "type", "")
                    if dt == "text_delta":
                        yield {"type": "text", "content": getattr(d, "text", "") or ""}
                    elif dt == "input_json_delta":
                        pj = getattr(d, "partial_json", "") or ""
                        if cur:
                            cur["args"] = cur.get("args", "") + pj
                            yield {"type": "tool_call_delta", "id": cur.get("id", ""), "args_partial": pj}
                elif et == "content_block_stop":
                    if cur:
                        yield {"type": "tool_call_end", "id": cur["id"], "name": cur["name"], "args": cur.get("args") or "{}"}
                        cur = {}
        yield {"type": "done", "finish_reason": "tool_calls" if had_tool else "stop"}

    def count_tokens(messages: list[dict]) -> int:
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content) // 3 + 4
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(part.get("text", "")) // 3 + 4
        return total

    call.chat = chat                                  # type: ignore
    call.stream = stream                              # type: ignore
    call.call_with_tools = call_with_tools            # type: ignore
    call.stream_with_tools = stream_with_tools        # type: ignore
    call.quick_call = quick_call                      # type: ignore
    call.count_tokens = count_tokens                  # type: ignore
    call.client = client                              # type: ignore
    call.model_name = model_name                      # type: ignore
    return _attach_provider_metadata(call, model_cfg)


class _ResponsesChoice:
    """Adapt an OpenAI Responses result to the choice shape used by AgentLoop."""

    class _Fn:
        def __init__(self, name: str, arguments: str):
            self.name = name
            self.arguments = arguments

    class _ToolCall:
        def __init__(self, call_id: str, name: str, arguments: str):
            self.id = call_id
            self.type = "function"
            self.function = _ResponsesChoice._Fn(name, arguments)

    class _Msg:
        def __init__(self, content: str, tool_calls: list):
            self.content = content
            self.role = "assistant"
            self.tool_calls = tool_calls or None

    def __init__(self, resp):
        text = str(getattr(resp, "output_text", "") or "")
        tool_calls: list = []
        for item in getattr(resp, "output", []) or []:
            item_type = getattr(item, "type", "")
            if item_type == "function_call":
                tool_calls.append(_ResponsesChoice._ToolCall(
                    str(getattr(item, "call_id", "") or getattr(item, "id", "") or ""),
                    str(getattr(item, "name", "") or ""),
                    str(getattr(item, "arguments", "") or "{}"),
                ))
            elif item_type == "message" and not text:
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", "") in {"output_text", "text"}:
                        text += str(getattr(part, "text", "") or "")
        self.message = _ResponsesChoice._Msg(text, tool_calls)
        status = str(getattr(resp, "status", "") or "")
        self.finish_reason = "tool_calls" if tool_calls else (
            "length" if status == "incomplete" else "stop"
        )
        try:
            usage = getattr(resp, "usage", None)
            if usage is not None:
                detail = normalize_usage(usage).as_legacy()
                self._hashmm_usage = {
                    "prompt_tokens": detail["prompt_tokens"],
                    "completion_tokens": detail["completion_tokens"],
                }
                self._hashmm_usage_details = detail
        except Exception:
            pass


def _messages_to_responses(messages: list[dict]) -> list[dict]:
    """Translate AgentLoop's Chat-Completions history to Responses input items."""
    items: list[dict] = []
    for message in messages or []:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if role == "tool":
            output = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            items.append({
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id") or ""),
                "output": output,
            })
            continue
        if role == "assistant" and message.get("tool_calls"):
            if content:
                items.append({"role": "assistant", "content": content})
            for tool_call in message.get("tool_calls") or []:
                fn = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
                items.append({
                    "type": "function_call",
                    "call_id": str(tool_call.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "arguments": str(fn.get("arguments") or "{}"),
                })
            continue
        if isinstance(content, list):
            converted: list[dict] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                kind = str(part.get("type") or "")
                if kind in {"text", "input_text", "output_text"}:
                    converted.append({
                        "type": "output_text" if role == "assistant" else "input_text",
                        "text": str(part.get("text") or ""),
                    })
                elif kind in {"image_url", "input_image"}:
                    image = part.get("image_url")
                    if isinstance(image, dict):
                        image = image.get("url")
                    converted.append({"type": "input_image", "image_url": str(image or "")})
            content = converted
        items.append({"role": role, "content": content})
    return items


def _tools_to_responses(tools: list[dict] | None) -> list[dict]:
    converted: list[dict] = []
    for tool in tools or []:
        fn = tool.get("function", tool) if isinstance(tool, dict) else {}
        item = {
            "type": "function",
            "name": str(fn.get("name") or ""),
            "description": str(fn.get("description") or ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        }
        if "strict" in fn:
            item["strict"] = bool(fn.get("strict"))
        converted.append(item)
    return converted


def _make_responses_fn(model_cfg: dict):
    """OpenAI-compatible Responses adapter with the same AgentLoop contract."""
    api_keys = [
        key.strip() for key in str(model_cfg.get("api_key") or "").split(",") if key.strip()
    ]
    model_name = str(model_cfg.get("model_name") or "")
    base_url = str(model_cfg.get("base_url") or "")
    provider = str(model_cfg.get("provider") or "custom")
    max_tokens = int(model_cfg.get("max_tokens", 16384) or 16384)
    temperature = float(model_cfg.get("temperature", 0.1) or 0.0)
    if not model_name or not base_url:
        return None
    spec = model_cfg.get("provider_spec")
    if not api_keys and not bool(getattr(spec, "local", False)):
        logger.warning("Responses model %s: API key 未配置", model_name)
        return None
    key_index = [0]

    def active_key() -> str:
        return api_keys[key_index[0] % len(api_keys)] if api_keys else ""

    client = _get_client(base_url, active_key())

    def create_kwargs(messages: list[dict], *, tools: list[dict] | None = None,
                      tool_choice: str = "auto", stream: bool = False,
                      temp: float | None = None, token_limit: int | None = None) -> dict:
        options = request_options(
            model_cfg, messages=messages, tools=tools, tool_choice=tool_choice,
            stream=stream, temperature=temp, max_tokens=token_limit,
        )
        options["input"] = _messages_to_responses(options.pop("messages"))
        if tools and tool_choice != "none":
            options["tools"] = _tools_to_responses(tools)
        return options

    def create_with_rotation(**kwargs):
        nonlocal client
        last_error = None
        for attempt in range(max(1, len(api_keys))):
            try:
                return client.responses.create(**kwargs)
            except Exception as exc:
                last_error = exc
                info = classify_provider_error(provider, exc)
                if len(api_keys) > 1 and info["code"] in {"authentication", "rate_limit"}:
                    key_index[0] = (key_index[0] + 1) % len(api_keys)
                    client = _get_client(base_url, active_key())
                    logger.warning("Responses request failed (attempt %s), rotating key", attempt + 1)
                    continue
                raise
        if last_error:
            raise last_error

    def call(prompt: str) -> str:
        resp = create_with_rotation(**create_kwargs(
            [{"role": "user", "content": prompt}], temp=temperature,
            token_limit=max_tokens,
        ))
        return str(getattr(resp, "output_text", "") or _ResponsesChoice(resp).message.content or "")

    def chat(messages: list[dict], max_tok: int | None = None) -> str:
        resp = create_with_rotation(**create_kwargs(
            messages, temp=temperature, token_limit=max_tok or max_tokens,
        ))
        return str(getattr(resp, "output_text", "") or _ResponsesChoice(resp).message.content or "")

    def call_with_tools(messages: list[dict], tools: list[dict] | None = None,
                        tool_choice: str = "auto") -> object:
        resp = create_with_rotation(**create_kwargs(
            messages, tools=tools, tool_choice=tool_choice,
            temp=temperature, token_limit=max_tokens,
        ))
        return _ResponsesChoice(resp)

    def _stream_events(messages: list[dict], tools: list[dict] | None = None):
        # SDK streams are iterators returned directly by create(stream=True).
        return create_with_rotation(**create_kwargs(
            messages, tools=tools, stream=True, temp=temperature,
            token_limit=max_tokens,
        ))

    def stream(messages: list[dict], *, temp_override: float | None = None):
        events = create_with_rotation(**create_kwargs(
            messages, stream=True,
            temp=temp_override if temp_override is not None else temperature,
            token_limit=max_tokens,
        ))
        for event in events:
            if getattr(event, "type", "") == "response.output_text.delta":
                delta = str(getattr(event, "delta", "") or "")
                if delta:
                    yield delta

    def stream_with_tools(messages: list[dict], tools: list[dict] | None = None):
        calls: dict[str, dict] = {}
        had_text = False
        had_tool = False
        for event in _stream_events(messages, tools):
            event_type = str(getattr(event, "type", "") or "")
            if event_type == "response.output_text.delta":
                delta = str(getattr(event, "delta", "") or "")
                if delta:
                    had_text = True
                    yield {"type": "text", "content": delta}
            elif event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if getattr(item, "type", "") == "function_call":
                    had_tool = True
                    item_id = str(getattr(item, "id", "") or getattr(item, "call_id", "") or "")
                    calls[item_id] = {
                        "id": str(getattr(item, "call_id", "") or item_id),
                        "name": str(getattr(item, "name", "") or ""),
                        "args": str(getattr(item, "arguments", "") or ""),
                    }
                    yield {"type": "tool_call_start", "id": calls[item_id]["id"],
                           "name": calls[item_id]["name"]}
            elif event_type == "response.function_call_arguments.delta":
                item_id = str(getattr(event, "item_id", "") or "")
                delta = str(getattr(event, "delta", "") or "")
                call_state = calls.setdefault(item_id, {"id": item_id, "name": "", "args": ""})
                call_state["args"] += delta
                if delta:
                    yield {"type": "tool_call_delta", "id": call_state["id"],
                           "args_partial": delta}
            elif event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if getattr(item, "type", "") == "function_call":
                    had_tool = True
                    item_id = str(getattr(item, "id", "") or getattr(item, "call_id", "") or "")
                    state = calls.pop(item_id, None) or {
                        "id": str(getattr(item, "call_id", "") or item_id),
                        "name": str(getattr(item, "name", "") or ""),
                        "args": str(getattr(item, "arguments", "") or "{}"),
                    }
                    if not state["name"]:
                        state["name"] = str(getattr(item, "name", "") or "")
                    if not state["args"]:
                        state["args"] = str(getattr(item, "arguments", "") or "{}")
                    yield {"type": "tool_call_end", "id": state["id"],
                           "name": state["name"], "args": state["args"] or "{}"}
        # Defensive flush for providers that omit output_item.done.
        for state in calls.values():
            yield {"type": "tool_call_end", "id": state["id"],
                   "name": state["name"], "args": state["args"] or "{}"}
        yield {"type": "done", "finish_reason": "tool_calls" if had_tool else "stop"}

    def quick_call(system_prompt: str, user_prompt: str, max_tok: int = 200) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        result = chat(messages, max_tok)
        return result or chat(messages, max(1200, max_tok * 6))

    def count_tokens(messages: list[dict]) -> int:
        return sum(
            len(str(message.get("content") or "")) // 3 + 4
            for message in messages or []
        )

    call.chat = chat                                  # type: ignore
    call.stream = stream                              # type: ignore
    call.call_with_tools = call_with_tools            # type: ignore
    call.stream_with_tools = stream_with_tools        # type: ignore
    call.quick_call = quick_call                      # type: ignore
    call.count_tokens = count_tokens                  # type: ignore
    call.client = client                              # type: ignore
    call.model_name = model_name                      # type: ignore
    return _attach_provider_metadata(call, model_cfg)


def make_llm_fn_from_model(model_cfg: dict) -> Callable[[str], str] | None:
    """Build a callable from a model config dict (from database).

    The returned function has these attached methods:
      .stream(messages)          → Generator[str] — streaming tokens
      .call_with_tools(messages, tools) → ChatCompletionMessage — full response with possible tool_calls
      .client                    → raw OpenAI client
      .model_name                → str
    """
    try:
        model_cfg = normalize_model_config(model_cfg)
    except ProviderConfigError as exc:
        logger.warning("模型连接配置无效：%s", exc)
        return None

    wire_api = model_cfg.get("wire_api")
    if wire_api == WIRE_ANTHROPIC:
        return _make_anthropic_fn(model_cfg)
    if wire_api == WIRE_RESPONSES:
        try:
            return _make_responses_fn(model_cfg)
        except Exception as exc:
            logger.warning("创建 Responses 客户端失败：%s", exc)
            return None

    api_key = model_cfg.get("api_key", "")
    base_url = model_cfg.get("base_url", "")
    model_name = model_cfg.get("model_name", "")
    temperature = model_cfg.get("temperature", 0.1)
    max_tokens = model_cfg.get("max_tokens", 16384)

    # v6.0: API key rotation — support comma-separated keys
    api_keys = [k.strip() for k in api_key.split(",") if k.strip()] if api_key else []
    _current_key_idx = [0]  # mutable for closure

    def _get_active_key() -> str:
        if not api_keys:
            return ""
        return api_keys[_current_key_idx[0] % len(api_keys)]

    def _rotate_key():
        if len(api_keys) > 1:
            _current_key_idx[0] = (_current_key_idx[0] + 1) % len(api_keys)
            logger.info(f"API key rotated to key #{_current_key_idx[0] + 1}/{len(api_keys)}")

    api_key = _get_active_key()

    if not base_url or not model_name:
        return None
    # For local models (Ollama, LM Studio), API key is optional
    provider = model_cfg.get("provider", "")
    spec = model_cfg.get("provider_spec")
    if not bool(getattr(spec, "local", False)) and (not api_key or api_key.startswith("sk-your-")):
        logger.warning(f"Model {model_name}: API key not configured")
        return None

    try:
        client = _get_client(base_url, api_key)
    except Exception as e:
        logger.warning(f"Failed to create client for {model_name}: {e}")
        return None

    # ── Simple call (str → str) ──
    def call(prompt: str) -> str:
        resp = client.chat.completions.create(**request_options(
            model_cfg, messages=[{"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=max_tokens,
        ))
        return resp.choices[0].message.content or ""

    # ── Streaming text generation ──
    def stream(messages: list[dict], *, temp_override: float | None = None):
        """Yield tokens from streaming response. Auto-rotates API key on auth errors.

        Args:
            messages: Chat messages.
            temp_override: If set, override the model's default temperature.
        """
        nonlocal client
        _temp = temp_override if temp_override is not None else temperature
        last_error = None
        for attempt in range(max(len(api_keys), 1)):
            try:
                resp = client.chat.completions.create(**request_options(
                    model_cfg, messages=messages, stream=True,
                    temperature=_temp, max_tokens=max_tokens,
                ))
                for chunk in resp:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return  # Success, exit retry loop
            except Exception as e:
                last_error = e
                err_str = str(e)
                if ("401" in err_str or "403" in err_str or "429" in err_str) and len(api_keys) > 1:
                    _rotate_key()
                    client = _get_client(base_url, _get_active_key())
                    logger.warning(f"Stream failed (attempt {attempt+1}), rotating key")
                else:
                    raise
        if last_error:
            raise last_error

    # ── Non-streaming call WITH tools (for Agent Loop) ──
    def call_with_tools(messages: list[dict], tools: list[dict] | None = None,
                        tool_choice: str = "auto") -> object:
        """Call the LLM with tool definitions. Returns the full Choice object.

        The Choice.message may contain .tool_calls or .content.
        When tool_choice="none", tools are omitted to force a text response.
        """
        kwargs = request_options(
            model_cfg, messages=messages, tools=tools,
            tool_choice=tool_choice, temperature=temperature,
            max_tokens=max_tokens,
        )
        # Don't pass tools when tool_choice=none — cleaner API call
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        # V52: 把 token 用量挂到返回对象上（choices[0] 本身不带 usage），
        # Agent 循环据此做每轮/全程的 token 核算。挂失败不影响主流程。
        try:
            u = getattr(resp, "usage", None)
            if u is not None:
                detail = normalize_usage(u).as_legacy()
                choice._hashmm_usage = {
                    "prompt_tokens": detail["prompt_tokens"],
                    "completion_tokens": detail["completion_tokens"],
                }
                choice._hashmm_usage_details = detail
        except Exception:
            pass
        return choice

    # ── v29: Streaming with tool calls (for Agent Loop) ──
    def stream_with_tools(messages: list[dict], tools: list[dict] | None = None):
        """Stream tokens AND detect tool_calls incrementally.

        Yields dicts:
          {"type": "reasoning", "content": "..."}
          {"type": "text", "content": "..."}
          {"type": "tool_call_start", "id": "...", "name": "..."}
          {"type": "tool_call_delta", "id": "...", "args_partial": "..."}
          {"type": "tool_call_end", "id": "...", "name": "...", "args": "{...}"}
          {"type": "done", "finish_reason": "stop"|"tool_calls"}
        """
        kwargs_st = request_options(
            model_cfg, messages=messages, tools=tools, stream=True,
            temperature=temperature, max_tokens=max_tokens,
        )

        resp = client.chat.completions.create(**kwargs_st)
        pending_calls: dict[int, dict] = {}

        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish = chunk.choices[0].finish_reason

            # DeepSeek and other OpenAI-compatible thinking models return the
            # assistant's hidden round-trip payload separately from content.
            # It must be retained and sent back with a subsequent tool result;
            # dropping it makes the next request fail with HTTP 400.  The
            # AgentLoop stores this event privately and never renders the raw
            # chain of thought as ordinary answer text.
            if delta:
                reasoning_delta = (
                    getattr(delta, "reasoning_content", None)
                    or getattr(delta, "reasoning", None)
                )
                if reasoning_delta:
                    yield {"type": "reasoning", "content": str(reasoning_delta)}

            # Text delta
            if delta and delta.content:
                yield {"type": "text", "content": delta.content}

            # Tool call deltas
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in pending_calls:
                        pending_calls[idx] = {
                            "id": tc.id or f"call_{idx}",
                            "name": tc.function.name if tc.function else "",
                            "args": "",
                        }
                        if tc.function and tc.function.name:
                            yield {
                                "type": "tool_call_start",
                                "id": pending_calls[idx]["id"],
                                "name": tc.function.name,
                            }
                    if tc.function and tc.function.arguments:
                        pending_calls[idx]["args"] += tc.function.arguments
                        yield {
                            "type": "tool_call_delta",
                            "id": pending_calls[idx]["id"],
                            "args_partial": tc.function.arguments,
                        }

            # Finish
            if finish == "tool_calls":
                for _idx, call in pending_calls.items():
                    yield {
                        "type": "tool_call_end",
                        "id": call["id"],
                        "name": call["name"],
                        "args": call["args"],
                    }
                pending_calls.clear()
                yield {"type": "done", "finish_reason": "tool_calls"}
            elif finish == "stop":
                yield {"type": "done", "finish_reason": "stop"}

        # V58: 兜底 flush——部分 provider 在工具调用流后不发 finish_reason="tool_calls"
        # （或流被截断），pending 的调用此前会被【静默丢弃】（真机实测：模型说"先创建
        # 核心模型文件"然后什么都没发生）。流自然结束时统一补发 tool_call_end。
        if pending_calls:
            for _idx, call in pending_calls.items():
                yield {"type": "tool_call_end", "id": call["id"],
                       "name": call["name"], "args": call["args"]}
            pending_calls.clear()
            yield {"type": "done", "finish_reason": "tool_calls"}

    # ── Chat with structured messages (non-streaming) ──
    def chat(messages: list[dict], max_tok: int | None = None) -> str:
        """Non-streaming call with structured messages. Returns text content."""
        resp = client.chat.completions.create(**request_options(
            model_cfg, messages=messages, temperature=temperature,
            max_tokens=max_tok or max_tokens,
        ))
        return resp.choices[0].message.content or ""

    # ── v29: Quick lightweight call ──
    def quick_call(system_prompt: str, user_prompt: str,
                   max_tok: int = 200) -> str:
        """Fast call with low max_tokens. For intent, titles, etc.

        V281：推理型模型（deepseek-v4/R 系、qwen-думq 等）会把小额 max_tokens 全部
        花在思维链上，content 返回空——HyDE 等"轻量小调用"因此拿到 0 字。
        兜底：content 为空且疑似被长度截断时，用放大后的 max_tokens 重试一次。"""
        def _once(mt: int):
            return client.chat.completions.create(**request_options(
                model_cfg,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0, max_tokens=mt,
            ))
        resp = _once(max_tok)
        text = resp.choices[0].message.content or ""
        if not text.strip():
            fr = getattr(resp.choices[0], "finish_reason", "") or ""
            # 空内容：无论 length（思考耗尽）还是其它空返回，都值得放大重试一次
            retry_tok = max(1200, max_tok * 6)
            try:
                resp2 = _once(retry_tok)
                text = resp2.choices[0].message.content or ""
            except Exception:
                pass
        return text

    # ── v29: Token count estimation ──
    def count_tokens(messages: list[dict]) -> int:
        """Rough token count (~3.5 chars per token for Chinese/English mix)."""
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += len(content) // 3 + 4  # +4 for role overhead
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(part.get("text", "")) // 3 + 4
        return total

    # Attach methods to the call function
    call.chat = chat                                  # type: ignore
    call.stream = stream                              # type: ignore
    call.call_with_tools = call_with_tools            # type: ignore
    call.stream_with_tools = stream_with_tools        # type: ignore
    call.quick_call = quick_call                      # type: ignore
    call.count_tokens = count_tokens                  # type: ignore
    call.client = client                              # type: ignore
    call.model_name = model_name                      # type: ignore
    return _attach_provider_metadata(call, model_cfg)


def get_active_llm_fn() -> tuple[Callable[[str], str] | None, dict | None]:
    """Get the LLM function for the current default model. Returns (fn, model_info).
    V249: 出口处套模型容灾链（settings『model_fallbacks』非空才激活；见 hashmm/llm_failover.py，
    借鉴 OmniRoute 的 auto-fallback + 熔断器）——所有调用点零改动自动获得多模型容灾。"""
    model = db.get_default_model()
    if model:
        fn = make_llm_fn_from_model(model)
        if fn:
            try:
                from hashmm.llm_failover import wrap_with_failover
                return wrap_with_failover(fn, model)
            except Exception:
                return fn, model
    # Fallback to env vars
    api_key = os.environ.get("LLM_API_KEY", "")
    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    model_name = os.environ.get("LLM_MODEL", "deepseek-chat")
    if api_key and not api_key.startswith("sk-your-"):
        env_model = {"api_key": api_key, "base_url": base_url, "model_name": model_name,
                     "provider": "env", "temperature": 0.1, "max_tokens": 16384}
        fn = make_llm_fn_from_model(env_model)
        return fn, env_model
    return None, None


def get_llm_for_request(
    requirements: ModelRequirements,
    *,
    preferred_model_id: str = "",
    user_mode: str = "auto",
    owner_subject: str = "",
    owner_id: str = "",
    request_id: str = "",
    sticky_key: str = "",
    project_id: str = "",
) -> tuple[Callable[[str], str] | None, dict | None, str]:
    """Select one compatible configured model before a request starts.

    This does not retry or switch after output has begun.  It also does not
    probe provider model names: administrators configure exact account-visible
    IDs, while HashMM routes only on explicit capability contracts.
    """

    configs: list[dict] = []
    for row in _configured_model_catalog():
        model_id = str(row.get("id") or "")
        if not model_id:
            continue
        is_global_default = bool(int(row.get("is_default") or 0))
        is_owned = bool(
            owner_subject
            and str(row.get("created_by") or "") == str(owner_subject)
        )
        if not (is_global_default or is_owned):
            continue
        # A stale/tampered preference must never turn another user's encrypted
        # provider credential into a request candidate.
        if preferred_model_id and model_id == preferred_model_id and not (
            is_global_default or is_owned
        ):
            continue
        configs.append(row)
    ranked = rank_model_configs(
        configs,
        requirements,
        user_mode=user_mode,
        preferred_id=preferred_model_id,
    )
    for cfg in ranked:
        try:
            resolved, route = _fabric_routed_config(
                cfg, owner_id=owner_id, request_id=request_id or f"model:{time.time_ns()}",
                sticky_key=sticky_key, project_id=project_id, acquire_lease=False,
            )
        except (ProviderConfigError, RuntimeError, ValueError) as exc:
            logger.warning("Provider Fabric 路由跳过模型 %s：%s", cfg.get("id", ""), exc)
            continue
        fn = make_llm_fn_from_model(resolved)
        if fn:
            reason = "preferred" if str(cfg.get("id") or "") == preferred_model_id else "capability_route"
            if route:
                base_request_id = str(request_id or f"model:{time.time_ns()}")
                call_number = 0

                def provider_call(prompt: str, *, _cfg=cfg) -> str:
                    nonlocal call_number
                    from hashmm.api import provider_fabric as _provider_fabric
                    call_number += 1
                    call_request_id = f"{base_request_id}:call:{call_number}"
                    started = time.monotonic()
                    call_route = None
                    lease_state = "failed"
                    try:
                        call_config, call_route = _fabric_routed_config(
                            _cfg,
                            owner_id=owner_id,
                            request_id=call_request_id,
                            sticky_key=sticky_key,
                            project_id=project_id,
                            acquire_lease=True,
                        )
                        inner = make_llm_fn_from_model(call_config)
                        if inner is None:
                            raise ProviderConfigError("Provider 通道无法创建模型调用")
                        output = inner(prompt)
                        _provider_fabric.record_attempt(
                            owner_id,
                            call_request_id,
                            str(call_route["channel"]["id"]),
                            attempt=1,
                            outcome="success",
                            first_token_seen=bool(output),
                            latency_ms=int((time.monotonic() - started) * 1000),
                        )
                        lease_state = "completed"
                        return output
                    except Exception as exc:
                        if call_route:
                            _provider_fabric.record_attempt(
                                owner_id,
                                call_request_id,
                                str(call_route["channel"]["id"]),
                                attempt=1,
                                outcome="failed",
                                error_code=type(exc).__name__,
                                first_token_seen=False,
                                latency_ms=int((time.monotonic() - started) * 1000),
                            )
                        raise
                    finally:
                        if call_route and call_route.get("lease_id"):
                            _provider_fabric.release_lease(
                                owner_id,
                                str(call_route["lease_id"]),
                                state=lease_state,
                            )

                fn = _ProviderRoutedCallable(
                    cfg, fn, owner_id=owner_id,
                    request_id=str(request_id or f"model:{time.time_ns()}"),
                    sticky_key=sticky_key, project_id=project_id,
                )
                fn.provider_route = {  # type: ignore[attr-defined]
                    "request_id": route["request_id"], "lease_id": "per_call",
                    "channel_id": route["channel"]["id"],
                    "retry_boundary": route["retry_boundary"],
                }
                reason = "provider_fabric"
            return fn, resolved, reason
    return None, None, "no_compatible_model"


def test_model_connection(model_cfg: dict) -> dict:
    """Test if a model config can connect and respond. Returns {ok, message, latency_ms}."""
    t0 = time.time()
    try:
        try:
            normalized = normalize_model_config(model_cfg)
        except ProviderConfigError as exc:
            return {"ok": False, "message": str(exc), "code": "invalid_config", "latency_ms": 0}
        fn = make_llm_fn_from_model(normalized)
        if not fn:
            return {"ok": False, "message": "无法创建连接（API Key 或 URL 配置错误）", "latency_ms": 0}
        resp = fn("回复'OK'即可。")
        ms = (time.time() - t0) * 1000
        if resp and len(resp.strip()) > 0:
            return {
                "ok": True,
                "message": f"连接成功，模型回复：{resp.strip()[:50]}",
                "provider": normalized.get("provider"),
                "wire_api": normalized.get("wire_api"),
                "latency_ms": round(ms),
            }
        return {"ok": False, "message": "模型未返回有效响应", "latency_ms": round(ms)}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        info = classify_provider_error(model_cfg.get("provider", "custom"), e)
        return {
            "ok": False, "message": info["message"], "code": info["code"],
            "retryable": info["retryable"], "latency_ms": round(ms),
        }


def discover_provider_models(model_cfg: dict, *, limit: int = 200) -> dict:
    """Read exact model ids from a provider's OpenAI-compatible model endpoint.

    Discovery is advisory and bounded. It never invents ids and never turns a
    discovered id into a capability claim.
    """

    t0 = time.time()
    try:
        normalized = normalize_model_config(model_cfg)
        spec = normalized["provider_spec"]
        if not spec.model_discovery:
            return {
                "ok": True,
                "supported": False,
                "provider": spec.id,
                "models": [],
                "message": spec.endpoint_note or "该服务商需从控制台复制模型或部署 ID。",
            }
        api_key = str(normalized.get("api_key") or "")
        if spec.auth != "optional" and not api_key:
            return {
                "ok": False, "supported": True, "provider": spec.id,
                "models": [], "code": "authentication", "message": "请先填写 API Key。",
            }
        client = _get_client(str(normalized.get("base_url") or ""), api_key)
        page = client.models.list()
        rows = getattr(page, "data", page)
        ids: list[str] = []
        for item in list(rows or [])[:max(1, min(int(limit or 200), 500))]:
            model_id = str(
                item.get("id") if isinstance(item, dict) else getattr(item, "id", "")
            ).strip()
            if model_id and len(model_id) <= 200 and model_id not in ids:
                ids.append(model_id)
        ids.sort(key=str.casefold)
        return {
            "ok": True, "supported": True, "provider": spec.id,
            "models": ids, "count": len(ids),
            "latency_ms": round((time.time() - t0) * 1000),
            "message": "模型 ID 来自当前账号接口；能力仍以单个模型配置为准。",
        }
    except Exception as exc:
        info = classify_provider_error(model_cfg.get("provider", "custom"), exc)
        return {
            "ok": False, "supported": True, "provider": info["provider"],
            "models": [], "code": info["code"], "retryable": info["retryable"],
            "message": info["message"],
            "latency_ms": round((time.time() - t0) * 1000),
        }
