"""LLM Provider — unified interface for all major LLM API providers.

Supports:
  - OpenAI-compatible: DeepSeek, Moonshot, 智谱, MiniMax, 零一万物, Groq, Together
  - OpenAI: GPT-4o, GPT-4o-mini
  - Anthropic: Claude Sonnet/Opus
  - Google: Gemini 2.5 Pro/Flash

All providers expose the same interface:
  provider.chat(messages) → str
  provider.stream(messages) → Iterator[str]
  provider.test() → bool

Usage:
  provider = LLMProvider.create("deepseek", api_key="sk-...", model="deepseek-v4-pro")
  response = provider.chat([{"role": "user", "content": "Hello"}])
"""
from __future__ import annotations
from hashmm.llm_timeout import client_timeout
import json
from typing import Iterator, Any
from dataclasses import dataclass
from hashmm.utils import get_logger

logger = get_logger("hashmm.llm_provider")


# ── Provider Registry ──

PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "sdk": "openai",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-v4-flash",
        "reasoning_models": {"deepseek-v4-pro", "deepseek-reasoner"},
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "sdk": "openai",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini", "o3-mini"],
        "default_model": "gpt-4o-mini",
        "reasoning_models": {"o1", "o1-mini", "o3-mini"},
    },
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "sdk": "anthropic",
        "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-5-20251001"],
        "default_model": "claude-sonnet-4-20250514",
        "reasoning_models": set(),
    },
    "google": {
        "name": "Google",
        "base_url": "https://generativelanguage.googleapis.com",
        "sdk": "google",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
        "default_model": "gemini-2.5-flash",
        "reasoning_models": set(),
    },
    "zhipu": {
        "name": "智谱AI",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "sdk": "openai",
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-long"],
        "default_model": "glm-4-flash",
        "reasoning_models": set(),
    },
    "moonshot": {
        "name": "Moonshot (月之暗面)",
        "base_url": "https://api.moonshot.cn/v1",
        "sdk": "openai",
        "models": ["moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
        "default_model": "moonshot-v1-8k",
        "reasoning_models": set(),
    },
    "yi": {
        "name": "零一万物",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "sdk": "openai",
        "models": ["yi-lightning", "yi-large", "yi-medium"],
        "default_model": "yi-lightning",
        "reasoning_models": set(),
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "sdk": "openai",
        "models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
        "default_model": "llama-3.3-70b-versatile",
        "reasoning_models": set(),
    },
}


@dataclass
class LLMResponse:
    """Standardized LLM response."""
    content: str
    model: str
    finish_reason: str = "stop"
    usage: dict | None = None
    raw: Any = None


class LLMProvider:
    """Unified LLM provider interface."""

    def __init__(self, provider: str, api_key: str, model: str = "",
                 base_url: str = "", temperature: float = 0.1,
                 max_tokens: int = 4096, **kwargs):
        self.provider_name = provider
        self.api_key = api_key
        self.model = model or PROVIDERS.get(provider, {}).get("default_model", "")
        self.base_url = base_url or PROVIDERS.get(provider, {}).get("base_url", "")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.is_reasoning = model in PROVIDERS.get(provider, {}).get("reasoning_models", set())

        # Auto-fix: strip /v1 for providers where SDK adds it
        sdk = PROVIDERS.get(provider, {}).get("sdk", "openai")
        if sdk == "openai" and self.base_url.rstrip("/").endswith("/v1"):
            if provider not in ("openai", "zhipu", "moonshot", "yi", "groq"):
                self.base_url = self.base_url.rstrip("/")[:-3]

        self._client = None
        self._sdk = sdk

    @classmethod
    def create(cls, provider: str, api_key: str, model: str = "", **kwargs) -> "LLMProvider":
        """Factory method — create provider by name."""
        if provider not in PROVIDERS:
            # Treat unknown providers as OpenAI-compatible
            logger.warning(f"Unknown provider '{provider}', treating as OpenAI-compatible")
        # V273: 用户填的 base_url 先按厂归一（少 /v1、少 compatible-mode 之类当场补齐）
        if kwargs.get("base_url"):
            kwargs["base_url"] = normalize_base_url(provider, kwargs["base_url"])
        return cls(provider=provider, api_key=api_key, model=model, **kwargs)

    @classmethod
    def list_providers(cls) -> list[dict]:
        """List all supported providers and their models."""
        return [
            {"id": k, "name": v["name"], "models": v["models"],
             "default_model": v["default_model"]}
            for k, v in PROVIDERS.items()
        ]

    def _get_client(self):
        """Lazy-initialize the API client."""
        if self._client is not None:
            return self._client

        if self._sdk == "anthropic":
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError("pip install anthropic")
        elif self._sdk == "google":
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self._client = genai.GenerativeModel(self.model)
            except ImportError:
                raise ImportError("pip install google-generativeai")
        else:
            # OpenAI-compatible
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=client_timeout())
            except ImportError:
                raise ImportError("pip install openai")

        return self._client

    def chat(self, messages: list[dict], **kwargs) -> str:
        """Send a chat request and return the response text.

        Args:
            messages: [{"role": "user", "content": "..."}]

        Returns:
            Response text string.
        """
        client = self._get_client()
        temp = kwargs.get("temperature", self.temperature)
        max_tok = kwargs.get("max_tokens", self.max_tokens)

        try:
            if self._sdk == "anthropic":
                return self._chat_anthropic(client, messages, temp, max_tok)
            elif self._sdk == "google":
                return self._chat_google(client, messages, temp, max_tok)
            else:
                return self._chat_openai(client, messages, temp, max_tok)
        except Exception as e:
            logger.error(f"[{self.provider_name}/{self.model}] Chat failed: {type(e).__name__}: {e}")
            raise

    def stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """Stream a chat response, yielding text chunks."""
        client = self._get_client()
        temp = kwargs.get("temperature", self.temperature)
        max_tok = kwargs.get("max_tokens", self.max_tokens)

        try:
            if self._sdk == "anthropic":
                yield from self._stream_anthropic(client, messages, temp, max_tok)
            elif self._sdk == "google":
                yield from self._stream_google(client, messages, temp, max_tok)
            else:
                yield from self._stream_openai(client, messages, temp, max_tok)
        except Exception as e:
            logger.error(f"[{self.provider_name}/{self.model}] Stream failed: {e}")
            raise

    def test(self) -> tuple[bool, str]:
        """Test API connectivity. Returns (success, message)."""
        try:
            response = self.chat(
                [{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=50,
            )
            if response and response.strip():
                return True, f"Connected: {response.strip()[:30]}"
            return False, "Empty response"
        except Exception as e:
            # V273: 测试连接的失败信息直接给"人话+建议"（map_provider_error）
            return False, map_provider_error(self.provider_name, e)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "base_url": self.base_url,
            "is_reasoning": self.is_reasoning,
        }

    # ── SDK-specific implementations ──

    def _chat_openai(self, client, messages, temperature, max_tokens) -> str:
        resp = client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        content = resp.choices[0].message.content or ""
        # Reasoning models may put content in reasoning_content
        if not content and self.is_reasoning:
            rc = getattr(resp.choices[0].message, 'reasoning_content', None)
            if rc:
                content = rc
        return content

    def _stream_openai(self, client, messages, temperature, max_tokens) -> Iterator[str]:
        stream = client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def _chat_anthropic(self, client, messages, temperature, max_tokens) -> str:
        # Anthropic requires system message separately
        system = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                user_messages.append(m)
        if not user_messages:
            user_messages = [{"role": "user", "content": "Hello"}]

        kwargs = {"model": self.model, "messages": user_messages,
                  "max_tokens": max_tokens, "temperature": temperature}
        if system:
            kwargs["system"] = system

        resp = client.messages.create(**kwargs)
        return resp.content[0].text if resp.content else ""

    def _stream_anthropic(self, client, messages, temperature, max_tokens) -> Iterator[str]:
        system = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                user_messages.append(m)
        if not user_messages:
            user_messages = [{"role": "user", "content": "Hello"}]

        kwargs = {"model": self.model, "messages": user_messages,
                  "max_tokens": max_tokens, "temperature": temperature}
        if system:
            kwargs["system"] = system

        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text

    def _chat_google(self, client, messages, temperature, max_tokens) -> str:
        # Convert messages to Gemini format
        parts = []
        for m in messages:
            parts.append(m["content"])
        prompt = "\n\n".join(parts)

        config = {"temperature": temperature, "max_output_tokens": max_tokens}
        resp = client.generate_content(prompt, generation_config=config)
        return resp.text if resp.text else ""

    def _stream_google(self, client, messages, temperature, max_tokens) -> Iterator[str]:
        parts = [m["content"] for m in messages]
        prompt = "\n\n".join(parts)
        config = {"temperature": temperature, "max_output_tokens": max_tokens}
        resp = client.generate_content(prompt, generation_config=config, stream=True)
        for chunk in resp:
            if chunk.text:
                yield chunk.text

# ══════════ V273 适配加固：用户接入各大厂 API 时"不需要关心细节" ══════════
# 两类高频翻车点的系统性兜底：
# ① base_url 少写/多写路径段（OpenAI 兼容口普遍要 /v1；DashScope 要 compatible-mode/v1；
#    智谱是 /api/paas/v4）——normalize_base_url 按厂补齐/纠正，用户抄官网域名就能用。
# ② 错误码天书（401/402/429/404 各家话术不同）——map_provider_error 翻成人话+可操作建议。
_BASE_RULES: dict[str, str] = {
    "openai": "/v1", "deepseek": "/v1", "moonshot": "/v1", "kimi": "/v1",
    "zhipu": "/api/paas/v4", "glm": "/api/paas/v4",
    "dashscope": "/compatible-mode/v1", "qwen": "/compatible-mode/v1", "aliyun": "/compatible-mode/v1",
    "doubao": "/api/v3", "volc": "/api/v3", "ark": "/api/v3",
    "siliconflow": "/v1", "openrouter": "/api/v1",
}


def normalize_base_url(provider: str, base_url: str) -> str:
    """按厂补齐 OpenAI 兼容路径段；已含正确段/非兼容 SDK（anthropic/google）原样返回。"""
    try:
        u = str(base_url or "").strip().rstrip("/")
        if not u:
            return u
        pid = str(provider or "").lower()
        want = ""
        for k, seg in _BASE_RULES.items():
            if k in pid or k in u:
                want = seg
                break
        if not want:
            # 未知厂但明显是裸域名的 OpenAI 兼容口 → 补 /v1（最常见约定）
            from urllib.parse import urlparse
            if urlparse(u).path in ("", "/"):
                want = "/v1"
        if want and not u.endswith(want):
            # 已带其它已知段（如手填了 /v1）就不重复叠加
            if not any(u.endswith(seg) for seg in _BASE_RULES.values()):
                u = u + want
        return u
    except Exception:  # noqa: BLE001
        return base_url


def map_provider_error(provider: str, err: Exception | str) -> str:
    """把各厂错误翻成'人话 + 建议'。原始信息保留在末尾便于排查。"""
    raw = str(err or "")
    low = raw.lower()
    pid = str(provider or "厂商")
    if "401" in low or "invalid api key" in low or "unauthorized" in low or "invalid_api_key" in low:
        hint = f"{pid} 拒绝了这把 API Key：多为 Key 抄错/已删除/项目不对。去该厂控制台重新生成并整串粘贴。"
    elif "402" in low or "insufficient" in low and "balance" in low or "arrears" in low or "余额" in raw:
        hint = f"{pid} 账户余额不足或未开通付费：充值/开通后即可恢复。"
    elif "429" in low or "rate" in low and "limit" in low or "tpm" in low or "rpm" in low:
        hint = f"{pid} 触发限流（免费档常见）：稍等重试，或在该厂控制台提升配额/换更低负载的模型。"
    elif "404" in low and ("model" in low or "not found" in low or "no such" in low):
        hint = f"模型名不存在或未开通：核对 {pid} 官方模型列表的准确 ID（大小写与日期后缀都要一致）。"
    elif "timeout" in low or "timed out" in low:
        hint = "请求超时：网络到该厂不稳或模型响应慢。可重试、换更快的模型，或检查代理设置。"
    elif "connection" in low or "resolve" in low or "ssl" in low or "certificate" in low:
        hint = f"连不上 {pid} 服务：检查 Base URL 是否抄对（本项目会自动补路径段）、本机网络/代理是否可达。"
    elif "context" in low and ("length" in low or "window" in low or "exceed" in low):
        hint = "内容超过模型上下文窗口：换长上下文模型，或开启对话压缩后重试。"
    else:
        hint = f"{pid} 返回了未归类错误，建议核对 Key/模型名/Base URL 三件套。"
    return f"{hint}\n[原始错误] {raw[:300]}"

