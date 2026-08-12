"""Compatibility facades for the historical SmartAgent API.

The product runtime uses :class:`hashmm.react_agent.ReactAgent`.  Older
plugins and desktop clients still import ``SmartAgent`` and
``MultiAgentExecutor`` though, so leaving a ``NotImplementedError`` stub here
made those features appear enabled while failing only after a user started a
task.  This module keeps the old import surface but routes all execution
through the same governed ReAct executor and a bounded, observable fan-out
for multiple workers.

The adapter intentionally does not accept a raw two-argument tool callback.
That callback shape predates execution scopes and would bypass owner,
permission, hook and receipt enforcement.  Callers must provide the current
three-argument governed callback ``(tool, args, execution_context)``.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import inspect
import threading
from typing import Any, Callable, Iterable, Iterator

from hashmm.react_agent import ReactAgent


def _copy_messages(messages: Iterable[dict[str, Any]] | None,
                   query: str) -> list[dict[str, Any]]:
    """Return a private message list without mutating the caller's history."""
    if messages is None:
        return [{"role": "user", "content": str(query or "")}]
    copied: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        copied.append({
            "role": str(item.get("role") or "user"),
            "content": str(item.get("content") or ""),
        })
    if not copied:
        copied.append({"role": "user", "content": str(query or "")})
    return copied


def _governed_tool_callback(
    callback: Callable[..., Any] | None,
) -> Callable[[str, dict, dict], Any] | None:
    """Validate and normalize the only callback shape allowed by the adapter."""
    if callback is None:
        return None
    if not callable(callback):
        raise TypeError("tool_exec_fn must be a callable governed executor")
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "tool_exec_fn must expose the governed (tool, args, context) contract"
        ) from exc
    # ``bind`` catches bound methods, positional-only parameters and keyword
    # defaults without invoking an untrusted callback.
    try:
        signature.bind("tool", {}, {})
    except TypeError as exc:
        raise TypeError(
            "raw tool callbacks are rejected; use (tool, args, execution_context)"
        ) from exc
    return callback


class SmartAgent:
    """Backwards-compatible facade over the real governed ``ReactAgent``."""

    def __init__(
        self,
        llm_fn: Any = None,
        tool_exec_fn: Callable[..., Any] | None = None,
        kb_search_fn: Callable[..., Any] | None = None,
        exec_context: dict[str, Any] | None = None,
        *,
        llm: Any = None,
        system_prompt: str = "",
        **_: Any,
    ) -> None:
        self.llm_fn = llm_fn if llm_fn is not None else llm
        if self.llm_fn is None:
            raise TypeError("SmartAgent requires llm_fn (or the llm keyword)")
        self.system_prompt = str(system_prompt or "")
        self.exec_context = dict(exec_context or {})
        self._react = ReactAgent(
            llm_fn=self.llm_fn,
            tool_exec_fn=_governed_tool_callback(tool_exec_fn),
            # ReactAgent deliberately refuses to call this legacy callback
            # directly; it is retained only for source compatibility.
            kb_search_fn=kb_search_fn,
            exec_context=self.exec_context,
        )

    @property
    def tool_calls(self) -> int:
        return int(getattr(self._react, "tool_calls", 0))

    def run(
        self,
        query: str,
        messages: Iterable[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        **_: Any,
    ) -> str:
        return self._react.run(
            str(query or ""),
            _copy_messages(messages, query),
            system_prompt=self.system_prompt if system_prompt is None else str(system_prompt),
        )

    def run_streaming(
        self,
        query: str,
        messages: Iterable[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        **_: Any,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        # ReactAgent's stream method has no separate system_prompt argument;
        # put the adapter prompt in a private system message instead.
        history = _copy_messages(messages, query)
        prompt = self.system_prompt if system_prompt is None else str(system_prompt or "")
        if prompt:
            if history and history[0]["role"] == "system":
                history[0] = {**history[0], "content": prompt + "\n\n" + history[0]["content"]}
            else:
                history.insert(0, {"role": "system", "content": prompt})
        yield from self._react.run_streaming(str(query or ""), history)


@dataclass(frozen=True)
class WorkerResult:
    """Bounded multi-agent result with explicit success/error state."""

    worker_id: str
    status: str
    answer: str = ""
    error: str = ""

    def public(self) -> dict[str, str]:
        return {
            "worker_id": self.worker_id,
            "status": self.status,
            "answer": self.answer[:20_000],
            "error": self.error[:2_000],
        }


class MultiAgentExecutor:
    """Small compatibility executor for independently scoped worker agents.

    It is intentionally a fan-out primitive, not an unverifiable "consensus"
    shortcut.  Workers run with their own message copy and the caller may
    provide a synthesizer.  Without one, the returned result is ``partial`` or
    ``completed`` with the individual bounded answers, never a fabricated
    aggregate.
    """

    def __init__(
        self,
        llm_fn: Any = None,
        conv_id: str = "",
        *,
        agents: Iterable[Any] | None = None,
        max_workers: int = 4,
        synthesizer: Callable[[list[dict[str, str]]], str] | None = None,
        **kwargs: Any,
    ) -> None:
        self.conv_id = str(conv_id or "")
        self.max_workers = max(1, min(int(max_workers or 1), 8))
        self.synthesizer = synthesizer
        self._lock = threading.RLock()
        self._default_agents = list(agents or [])
        if not self._default_agents and llm_fn is not None:
            # A single default worker is useful for legacy callers; callers
            # asking for real fan-out should pass distinct agents.
            self._default_agents = [SmartAgent(llm_fn=llm_fn, **kwargs)]

    @staticmethod
    def _worker_call(worker: Any, query: str,
                     messages: Iterable[dict[str, Any]] | None) -> str:
        if isinstance(worker, SmartAgent):
            return worker.run(query, messages)
        if hasattr(worker, "run") and callable(worker.run):
            return str(worker.run(query, _copy_messages(messages, query)))
        # A bare callback has no execution scope, owner, permission gate or
        # receipt boundary.  Accepting it here would reintroduce the exact
        # legacy bypass this compatibility layer is meant to remove.
        if callable(worker):
            raise TypeError(
                "raw worker callbacks are rejected; use a governed worker.run(query, messages)"
            )
        raise TypeError("worker must provide run(query, messages)")

    def run(
        self,
        query: str,
        messages: Iterable[dict[str, Any]] | None = None,
        agents: Iterable[Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        workers = list(self._default_agents if agents is None else agents)
        if not workers:
            return {
                "status": "failed",
                "worker_count": 0,
                "results": [],
                "errors": [{"code": "no_workers", "message": "未配置可运行的 Agent"}],
                "synthesized": False,
            }
        # ``max_workers`` bounds concurrent execution, not the number of
        # requested roles.  A team of five with two slots must still finish
        # all five roles instead of silently dropping three.
        workers = workers[:32]
        results: list[WorkerResult] = []
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(workers)),
            thread_name_prefix="hashmm-agent",
        ) as pool:
            futures = {
                pool.submit(self._worker_call, worker, str(query or ""), messages): idx
                for idx, worker in enumerate(workers)
            }
            for future in as_completed(futures):
                idx = futures[future]
                worker_id = f"worker-{idx + 1}"
                try:
                    answer = future.result()
                    results.append(WorkerResult(worker_id, "completed", answer=answer))
                except Exception as exc:  # each worker is isolated from peers
                    results.append(WorkerResult(
                        worker_id, "failed", error=f"{type(exc).__name__}: {exc}",
                    ))
        results.sort(key=lambda item: item.worker_id)
        public_results = [item.public() for item in results]
        failures = [item for item in results if item.status != "completed"]
        if not failures and results:
            status = "completed"
        elif len(failures) < len(results):
            status = "partial"
        else:
            status = "failed"
        output: dict[str, Any] = {
            "status": status,
            "worker_count": len(results),
            "results": public_results,
            "errors": [
                {"worker_id": item.worker_id, "code": "worker_failed", "message": item.error}
                for item in failures
            ],
            "synthesized": False,
            "conversation_id": self.conv_id,
        }
        if self.synthesizer and results:
            try:
                output["answer"] = str(self.synthesizer(public_results))[:20_000]
                output["synthesized"] = True
            except Exception as exc:
                output["errors"].append({
                    "code": "synthesizer_failed",
                    "message": f"{type(exc).__name__}: {exc}",
                })
                output["status"] = "partial" if public_results else "failed"
        return output

    execute = run

    def run_streaming(
        self,
        query: str,
        messages: Iterable[dict[str, Any]] | None = None,
        agents: Iterable[Any] | None = None,
        **kwargs: Any,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        result = self.run(query, messages, agents, **kwargs)
        for worker in result["results"]:
            yield ("worker", worker)
        yield ("done", result)


__all__ = ["SmartAgent", "MultiAgentExecutor", "WorkerResult"]
