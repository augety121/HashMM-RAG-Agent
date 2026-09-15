"""Process-local control plane for one active conversation turn.

Conversation messages and checkpoints are durable in the database.  A running
Python coroutine is not: it cannot survive a process restart.  This module
therefore keeps only the live control state needed to interrupt a turn or add a
follow-up instruction while that exact turn is still running.

Every mutation is guarded by both conversation/user identity and the expected
turn id.  That prevents a delayed desktop or phone request from steering a
newer turn by mistake.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import threading
import time
import uuid


MAX_STEER_CHARS = 8_000
MAX_PENDING_STEERS = 20
MAX_STEER_ATTACHMENTS = 8
DEFAULT_TTL_SECONDS = 2 * 60 * 60


class ActiveRunError(RuntimeError):
    """Base class for stable API error mapping."""


class ActiveRunConflict(ActiveRunError):
    pass


class ActiveRunNotFound(ActiveRunError):
    pass


class ActiveRunNotSteerable(ActiveRunError):
    pass


class ActiveRunInvalidSteer(ActiveRunError):
    pass


class ActiveRunQueueFull(ActiveRunError):
    pass


@dataclass
class SteeringEntry:
    entry_id: str
    content: str
    client_message_id: str
    queued_at: float
    attachments: tuple[dict, ...] = ()
    message_id: str = ""

    def public(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "content": self.content,
            "client_message_id": self.client_message_id,
            "queued_at": self.queued_at,
            "attachments": [dict(item) for item in self.attachments],
            "message_id": self.message_id,
        }


@dataclass
class ActiveRun:
    turn_id: str
    conv_id: str
    user_id: str
    goal: str
    started_at: float
    mode: str = "initializing"
    steerable: bool = False
    finishing: bool = False
    interrupt_requested: bool = False
    closed: bool = False
    _pending: deque[SteeringEntry] = field(default_factory=deque, repr=False)
    _seen: dict[str, SteeringEntry] = field(default_factory=dict, repr=False)
    _accepted: dict[str, SteeringEntry] = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def public(self) -> dict:
        with self._lock:
            return {
                "turn_id": self.turn_id,
                "conversation_id": self.conv_id,
                "goal": self.goal[:500],
                "started_at": self.started_at,
                "status": "interrupting" if self.interrupt_requested else "running",
                "mode": self.mode,
                "steerable": bool(self.steerable and not self.finishing and not self.closed),
                "pending_steers": len(self._pending),
            }

    def mark_steerable(self, mode: str = "agent_loop") -> bool:
        with self._lock:
            if self.closed or self.finishing or self.interrupt_requested:
                return False
            self.mode = str(mode or "agent_loop")[:48]
            self.steerable = True
            return True

    def enqueue_steer(
        self,
        content: str,
        client_message_id: str = "",
        attachments: list[dict] | tuple[dict, ...] | None = None,
    ) -> tuple[SteeringEntry, bool]:
        text = str(content or "").strip()
        safe_attachments = tuple(dict(item) for item in (attachments or []))
        if not text and not safe_attachments:
            raise ActiveRunInvalidSteer("追加要求不能为空")
        if len(text) > MAX_STEER_CHARS:
            raise ActiveRunInvalidSteer(
                f"追加要求超过 {MAX_STEER_CHARS} 字符（当前 {len(text)}）"
            )
        client_id = str(client_message_id or "").strip()[:128]
        with self._lock:
            if self.closed or self.finishing or self.interrupt_requested or not self.steerable:
                raise ActiveRunNotSteerable("当前任务已进入收尾或不支持运行中追加要求")
            if client_id and client_id in self._seen:
                return self._seen[client_id], True
            if len(self._pending) >= MAX_PENDING_STEERS:
                raise ActiveRunQueueFull("待处理的追加要求过多，请等待当前要求生效")
            entry = SteeringEntry(
                entry_id=uuid.uuid4().hex,
                content=text,
                client_message_id=client_id,
                queued_at=time.time(),
                attachments=safe_attachments[:MAX_STEER_ATTACHMENTS],
            )
            self._pending.append(entry)
            self._accepted[entry.entry_id] = entry
            if client_id:
                self._seen[client_id] = entry
            return entry, False

    def bind_message(self, entry_id: str, message_id: str) -> bool:
        with self._lock:
            for entry in self._accepted.values():
                if entry.entry_id == entry_id:
                    entry.message_id = str(message_id or "")
                    return True
        return False

    def rollback_steer(self, entry_id: str) -> None:
        with self._lock:
            removed = [entry for entry in self._pending if entry.entry_id == entry_id]
            self._pending = deque(entry for entry in self._pending if entry.entry_id != entry_id)
            for entry in removed:
                self._accepted.pop(entry.entry_id, None)
                if entry.client_message_id and self._seen.get(entry.client_message_id) is entry:
                    self._seen.pop(entry.client_message_id, None)

    def drain_steering(self) -> list[dict]:
        with self._lock:
            items = [entry.public() for entry in self._pending]
            self._pending.clear()
            return items

    def accepted_steering(self) -> list[dict]:
        """Return this turn's durable accepted inputs, including drained ones."""
        with self._lock:
            return [entry.public() for entry in self._accepted.values()]

    def mark_unsteerable(self, mode: str = "fallback") -> None:
        """Close new steering while retaining cooperative interruption."""
        with self._lock:
            self.mode = str(mode or "fallback")[:48]
            self.steerable = False

    def close_steering_if_empty(self) -> bool:
        """Atomically enter finalization, unless accepted input is waiting.

        This is the important final-answer race guard: either a steer is
        accepted first and the loop must consume it, or finalization wins and a
        later steer is rejected.  Accepted input is never silently lost.
        """
        with self._lock:
            if self._pending:
                return False
            self.finishing = True
            self.steerable = False
            return True

    def request_interrupt(self) -> bool:
        with self._lock:
            if self.closed:
                return False
            self.interrupt_requested = True
            self.steerable = False
            return True

    def is_interrupted(self) -> bool:
        with self._lock:
            return bool(self.interrupt_requested)

    def finish(self) -> None:
        with self._lock:
            self.closed = True
            self.steerable = False
            self.finishing = True


class ActiveRunRegistry:
    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self.ttl_seconds = max(30.0, float(ttl_seconds))
        self._runs: dict[str, ActiveRun] = {}
        self._lock = threading.RLock()

    def _cleanup_locked(self, now: float | None = None) -> None:
        cutoff = (time.time() if now is None else now) - self.ttl_seconds
        stale = [conv_id for conv_id, run in self._runs.items()
                 if run.closed or run.started_at < cutoff]
        for conv_id in stale:
            run = self._runs.pop(conv_id, None)
            if run:
                run.finish()

    def reserve(self, conv_id: str, user_id: str, goal: str, *, turn_id: str = "") -> ActiveRun:
        with self._lock:
            self._cleanup_locked()
            current = self._runs.get(conv_id)
            if current and not current.closed:
                raise ActiveRunConflict("该对话已有任务正在运行")
            run = ActiveRun(
                turn_id=str(turn_id or uuid.uuid4().hex),
                conv_id=str(conv_id),
                user_id=str(user_id),
                goal=str(goal or "")[:MAX_STEER_CHARS],
                started_at=time.time(),
            )
            self._runs[run.conv_id] = run
            return run

    def get(self, conv_id: str, user_id: str, turn_id: str = "") -> ActiveRun:
        with self._lock:
            self._cleanup_locked()
            run = self._runs.get(str(conv_id))
            if (not run or run.closed or run.user_id != str(user_id)
                    or (turn_id and run.turn_id != str(turn_id))):
                raise ActiveRunNotFound("活动任务不存在")
            return run

    def maybe_get(self, conv_id: str, user_id: str) -> ActiveRun | None:
        try:
            return self.get(conv_id, user_id)
        except ActiveRunNotFound:
            return None

    def finish(self, run: ActiveRun) -> None:
        with self._lock:
            current = self._runs.get(run.conv_id)
            if current is run:
                self._runs.pop(run.conv_id, None)
            run.finish()

    def clear(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
            self._runs.clear()
        for run in runs:
            run.finish()


registry = ActiveRunRegistry()
