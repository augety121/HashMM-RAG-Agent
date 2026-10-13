"""Terminal-bench custom agent backed by HashMM's OpenAI-compatible model.

This module intentionally depends only on the Python standard library plus the
``terminal-bench`` package that imports it.  The benchmark harness runs in its
own virtual environment, so importing the full HashMM backend here would couple
the harness to unrelated backend dependencies.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from terminal_bench.agents.base_agent import AgentResult, BaseAgent
from terminal_bench.agents.failure_mode import FailureMode
from terminal_bench.terminal.tmux_session import TmuxSession


_SYSTEM_PROMPT = """You are an autonomous terminal agent inside an isolated Docker task.
Complete the user's task by inspecting files, running commands, editing files, and verifying the result.
On every turn output exactly one JSON object and no Markdown:
  {"command": "one shell command to execute"}
or, only after the task is fully verified:
  {"done": true}
Use one command at a time. Read the observation before choosing the next command.
Do not claim success without checking the produced files or command output."""


def _json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response (supports think text)."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("model response did not contain a JSON object")


class HashMMAgent(BaseAgent):
    """Drive a Terminal-bench tmux session through an OpenAI-compatible API."""

    @staticmethod
    def name() -> str:
        return "hashmm-agent"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._base = (os.environ.get("HASHMM_OPENAI_BASE") or "").rstrip("/")
        self._key = os.environ.get("HASHMM_OPENAI_KEY") or "EMPTY"
        self._model = os.environ.get("HASHMM_OPENAI_MODEL") or ""
        self._max_steps = max(1, int(os.environ.get("HASHMM_TB_MAX_STEPS", "60")))
        # 推理型模型可能先消耗 reasoning tokens；2048 容易在 JSON 命令出现前被截断为空。
        self._max_tokens = max(512, int(os.environ.get("HASHMM_TB_MAX_TOKENS", "4096")))
        self._max_retry_tokens = max(
            self._max_tokens, int(os.environ.get("HASHMM_TB_MAX_RETRY_TOKENS", "8192"))
        )
        self._request_retries = max(1, int(os.environ.get("HASHMM_TB_REQUEST_RETRIES", "4")))

    def _chat(self, messages: list[dict[str, str]]) -> tuple[str, int, int]:
        if not self._base or not self._model:
            raise RuntimeError("HASHMM_OPENAI_BASE / HASHMM_OPENAI_MODEL is not configured")
        last_error: Exception | None = None
        token_limit = self._max_tokens
        for attempt in range(self._request_retries):
            payload = json.dumps(
                {
                    "model": self._model,
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": token_limit,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                self._base + "/chat/completions",
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._key}",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
                    result = json.loads(response.read().decode("utf-8", "replace"))
                content = str(
                    ((result.get("choices") or [{}])[0].get("message") or {}).get("content")
                    or ""
                )
                if not content.strip():
                    token_limit = min(self._max_retry_tokens, token_limit * 2)
                    raise ValueError("model returned empty content")
                usage = result.get("usage") or {}
                return content, int(usage.get("prompt_tokens") or 0), int(
                    usage.get("completion_tokens") or 0
                )
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read().decode("utf-8", "replace")[:500]
                except Exception:
                    body = ""
                last_error = RuntimeError(f"HTTP {exc.code}: {body or exc.reason}")
                # 认证/参数类 4xx 重试不会自愈；429 和 5xx 才退避重试。
                if exc.code not in (408, 429) and exc.code < 500:
                    break
                if attempt < self._request_retries - 1:
                    retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                    delay = float(retry_after) if str(retry_after).replace(".", "", 1).isdigit() else 2**attempt
                    time.sleep(min(16.0, max(1.0, delay)))
            except (urllib.error.URLError, TimeoutError, ValueError,
                    KeyError, IndexError, TypeError, AttributeError) as exc:
                last_error = exc
                if attempt < self._request_retries - 1:
                    time.sleep(min(16, 2**attempt))
        raise RuntimeError(
            f"model request failed after {self._request_retries} attempts: {last_error}"
        )

    def perform_task(
        self,
        instruction: str,
        session: TmuxSession,
        logging_dir: Path | None = None,
    ) -> AgentResult:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ]
        transcript: list[dict[str, Any]] = []
        input_tokens = 0
        output_tokens = 0
        parse_failures = 0
        failure = FailureMode.NONE

        try:
            for step in range(1, self._max_steps + 1):
                raw, used_in, used_out = self._chat(messages)
                input_tokens += used_in
                output_tokens += used_out
                try:
                    action = _json_object(raw)
                except ValueError as exc:
                    parse_failures += 1
                    transcript.append({"step": step, "response": raw, "error": str(exc)})
                    if parse_failures >= 3:
                        failure = FailureMode.FATAL_LLM_PARSE_ERROR
                        break
                    messages.extend(
                        [
                            {"role": "assistant", "content": raw},
                            {
                                "role": "user",
                                "content": 'Invalid response. Return only {"command":"..."} or {"done":true}.',
                            },
                        ]
                    )
                    continue

                if action.get("done") is True:
                    transcript.append({"step": step, "response": raw, "done": True})
                    break

                command = action.get("command")
                if not isinstance(command, str) or not command.strip():
                    parse_failures += 1
                    messages.extend(
                        [
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content": "The command must be a non-empty string."},
                        ]
                    )
                    if parse_failures >= 3:
                        failure = FailureMode.FATAL_LLM_PARSE_ERROR
                        break
                    continue

                session.send_keys(
                    [command, "Enter"], block=True, max_timeout_sec=180
                )
                observation = session.get_incremental_output()
                # Preserve the useful tail and keep long-running sessions within model context.
                observation = observation[-12000:]
                transcript.append(
                    {"step": step, "response": raw, "command": command, "observation": observation}
                )
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": observation},
                    ]
                )
                if len(messages) > 26:
                    messages = messages[:2] + messages[-24:]
            else:
                # Step budget exhaustion is not a harness error: Terminal-bench must
                # still run the official verifier against whatever the agent produced.
                transcript.append({"warning": f"step budget exhausted ({self._max_steps})"})
        except Exception as exc:  # Terminal-bench records the typed failure in results.json.
            failure = FailureMode.UNKNOWN_AGENT_ERROR
            transcript.append({"error": f"{type(exc).__name__}: {exc}"})

        if logging_dir is not None:
            logging_dir.mkdir(parents=True, exist_ok=True)
            (logging_dir / "hashmm-transcript.json").write_text(
                json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return AgentResult(
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            failure_mode=failure,
        )
