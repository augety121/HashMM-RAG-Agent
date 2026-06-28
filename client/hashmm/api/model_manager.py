"""HashMM-RAG v29 — Multi-model manager with enhanced tool calling.

Supports OpenAI-compatible APIs: DeepSeek, OpenAI, Qwen, Zhipu, Moonshot, Ollama, etc.
Model configs are loaded from the database. The .env provides a default fallback.

v29 additions:
  - stream_with_tools(messages, tools): Streaming + function calling
  - quick_call(system, user, max_tokens): Fast lightweight call
  - count_tokens(messages): Token count estimation
"""
from __future__ import annotations
import os, time, json
from typing import Callable
from hashmm.api import database as db
from hashmm.utils import get_logger
from hashmm.llm_timeout import client_timeout

logger = get_logger("hashmm.model_manager")

# Provider presets for common services
PROVIDER_PRESETS = {
    "openai":    {"base_url": "https://api.openai.com/v1",       "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]},
    "anthropic": {"base_url": "https://api.anthropic.com",       "models": ["claude-3-7-sonnet-latest", "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"]},
    "deepseek":  {"base_url": "https://api.deepseek.com/v1",     "models": ["deepseek-chat", "deepseek-reasoner"]},
    "qwen":      {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "models": ["qwen-plus", "qwen-turbo", "qwen-max"]},
    "zhipu":     {"base_url": "https://open.bigmodel.cn/api/paas/v4", "models": ["glm-4-plus", "glm-4-flash"]},
    "moonshot":  {"base_url": "https://api.moonshot.cn/v1",      "models": ["moonshot-v1-8k", "moonshot-v1-32k"]},
    "gemini":    {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"]},
    "mistral":   {"base_url": "https://api.mistral.ai/v1",       "models": ["mistral-large-latest", "mistral-small-latest", "open-mistral-nemo"]},
    "xai":       {"base_url": "https://api.x.ai/v1",             "models": ["grok-2-latest", "grok-beta"]},
    "groq":      {"base_url": "https://api.groq.com/openai/v1",  "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]},
    "openrouter":{"base_url": "https://openrouter.ai/api/v1",    "models": ["openai/gpt-4o", "anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash-001", "deepseek/deepseek-chat"]},
    "siliconflow":{"base_url": "https://api.siliconflow.cn/v1",  "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen2.5-7B-Instruct"]},
    "together":  {"base_url": "https://api.together.xyz/v1",     "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"]},
    "ollama":    {"base_url": "http://localhost:11434/v1",        "models": ["qwen2.5:7b", "llama3.1:8b", "deepseek-r1:8b"]},
    "lmstudio":  {"base_url": "http://localhost:1234/v1",        "models": ["local-model"]},
    "vllm":      {"base_url": "http://localhost:8000/v1",        "models": ["served-model"]},
}

_client_cache: dict[str, object] = {}

def _get_client(base_url: str, api_key: str):
    """Get or create an OpenAI client for the given base_url."""
    cache_key = f"{base_url}:{api_key[:8] if api_key else 'no-key'}"
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
                self._hashmm_usage = {
                    "prompt_tokens": int(getattr(u, "input_tokens", 0) or 0),
                    "completion_tokens": int(getattr(u, "output_tokens", 0) or 0),
                }
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

    def call(prompt: str) -> str:
        sys_str, msgs = _split([{"role": "user", "content": prompt}])
        kw = dict(model=model_name, max_tokens=max_tokens, temperature=temperature, messages=msgs)
        if sys_str: kw["system"] = sys_str
        return _text_of(client.messages.create(**kw))

    def chat(messages: list[dict], max_tok: int | None = None) -> str:
        sys_str, msgs = _split(messages)
        kw = dict(model=model_name, max_tokens=max_tok or max_tokens, temperature=temperature, messages=msgs)
        if sys_str: kw["system"] = sys_str
        return _text_of(client.messages.create(**kw))

    def quick_call(system_prompt: str, user_prompt: str, max_tok: int = 200) -> str:
        kw = dict(model=model_name, max_tokens=max_tok, temperature=0.0,
                  messages=[{"role": "user", "content": user_prompt}])
        if system_prompt: kw["system"] = system_prompt
        return _text_of(client.messages.create(**kw))

    def stream(messages: list[dict], *, temp_override: float | None = None):
        sys_str, msgs = _split(messages)
        kw = dict(model=model_name, max_tokens=max_tokens,
                  temperature=temp_override if temp_override is not None else temperature, messages=msgs)
        if sys_str: kw["system"] = sys_str
        with client.messages.stream(**kw) as s:
            for text in s.text_stream:
                if text:
                    yield text

    def call_with_tools(messages: list[dict], tools: list[dict] | None = None,
                        tool_choice: str = "auto") -> object:
        sys_str, msgs = _split(messages)
        kw = dict(model=model_name, max_tokens=max_tokens, temperature=temperature, messages=msgs)
        if sys_str: kw["system"] = sys_str
        if tools and tool_choice != "none":
            kw["tools"] = _tools_to_anthropic(tools)
            kw["tool_choice"] = {"type": "auto"}
        return _AnthropicChoice(client.messages.create(**kw))

    def stream_with_tools(messages: list[dict], tools: list[dict] | None = None):
        sys_str, msgs = _split(messages)
        kw = dict(model=model_name, max_tokens=max_tokens, temperature=temperature, messages=msgs)
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
    return call


def make_llm_fn_from_model(model_cfg: dict) -> Callable[[str], str] | None:
    """Build a callable from a model config dict (from database).

    The returned function has these attached methods:
      .stream(messages)          → Generator[str] — streaming tokens
      .call_with_tools(messages, tools) → ChatCompletionMessage — full response with possible tool_calls
      .client                    → raw OpenAI client
      .model_name                → str
    """
    # Anthropic（Claude）走原生适配器（不同的 API 形状），其余 provider 走下面 OpenAI 兼容路径。
    if (model_cfg.get("provider", "") or "").lower() == "anthropic":
        return _make_anthropic_fn(model_cfg)

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
    if provider not in ("ollama", "lmstudio", "vllm") and (not api_key or api_key.startswith("sk-your-")):
        logger.warning(f"Model {model_name}: API key not configured")
        return None

    try:
        client = _get_client(base_url, api_key)
    except Exception as e:
        logger.warning(f"Failed to create client for {model_name}: {e}")
        return None

    # ── Simple call (str → str) ──
    def call(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
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
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=_temp,
                    max_tokens=max_tokens,
                    stream=True,
                )
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
        kwargs = dict(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools and tool_choice != "none":
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        # Don't pass tools when tool_choice=none — cleaner API call
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        # V52: 把 token 用量挂到返回对象上（choices[0] 本身不带 usage），
        # Agent 循环据此做每轮/全程的 token 核算。挂失败不影响主流程。
        try:
            u = getattr(resp, "usage", None)
            if u is not None:
                choice._hashmm_usage = {
                    "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                }
        except Exception:
            pass
        return choice

    # ── v29: Streaming with tool calls (for Agent Loop) ──
    def stream_with_tools(messages: list[dict], tools: list[dict] | None = None):
        """Stream tokens AND detect tool_calls incrementally.

        Yields dicts:
          {"type": "text", "content": "..."}
          {"type": "tool_call_start", "id": "...", "name": "..."}
          {"type": "tool_call_delta", "id": "...", "args_partial": "..."}
          {"type": "tool_call_end", "id": "...", "name": "...", "args": "{...}"}
          {"type": "done", "finish_reason": "stop"|"tool_calls"}
        """
        kwargs_st = dict(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        if tools:
            kwargs_st["tools"] = tools
            kwargs_st["tool_choice"] = "auto"

        resp = client.chat.completions.create(**kwargs_st)
        pending_calls: dict[int, dict] = {}

        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish = chunk.choices[0].finish_reason

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
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tok or max_tokens,
        )
        return resp.choices[0].message.content or ""

    # ── v29: Quick lightweight call ──
    def quick_call(system_prompt: str, user_prompt: str,
                   max_tok: int = 200) -> str:
        """Fast call with low max_tokens. For intent, titles, etc."""
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=max_tok,
        )
        return resp.choices[0].message.content or ""

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
    return call


def get_active_llm_fn() -> tuple[Callable[[str], str] | None, dict | None]:
    """Get the LLM function for the current default model. Returns (fn, model_info)."""
    model = db.get_default_model()
    if model:
        fn = make_llm_fn_from_model(model)
        if fn:
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


def test_model_connection(model_cfg: dict) -> dict:
    """Test if a model config can connect and respond. Returns {ok, message, latency_ms}."""
    t0 = time.time()
    try:
        fn = make_llm_fn_from_model(model_cfg)
        if not fn:
            return {"ok": False, "message": "无法创建连接（API Key 或 URL 配置错误）", "latency_ms": 0}
        resp = fn("回复'OK'即可。")
        ms = (time.time() - t0) * 1000
        if resp and len(resp.strip()) > 0:
            return {"ok": True, "message": f"连接成功，模型回复：{resp.strip()[:50]}", "latency_ms": round(ms)}
        return {"ok": False, "message": "模型未返回有效响应", "latency_ms": round(ms)}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"ok": False, "message": f"连接失败：{str(e)[:200]}", "latency_ms": round(ms)}
