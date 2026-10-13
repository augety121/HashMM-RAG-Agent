"""v17 Phase 75 — long-horizon context engineering (轴 B2, general-purpose).

The clear short-board vs Claude Code: long conversations / long agent tasks
currently get crude tail truncation (``history[-6:]``), which silently drops
early decisions and facts. Errors and goal-drift accumulate as the context
degrades. The 2026 toolkit:

  - **Compaction**: when the working context exceeds a token budget, summarize the
    *older* turns into a structured summary while keeping the recent turns verbatim
    and any *pinned* facts (decisions, constraints) intact. (This is what cc's
    "conversation compacted" does.)
  - **Externalized state** (InfiAgent, 2026): persist the full state/event log to
    disk so the *working* context stays bounded regardless of task length —
    nothing is lost, but the model only ever sees a bounded window.

Both are **injectable** (summarization via an injected ``summarize_fn``; a
deterministic extractive fallback when no LLM), **default-off**, and **never
raise** — any failure degrades to a no-op (today's behavior).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger(__name__)


# ── tokens / config ──────────────────────────────────────────────────────
def estimate_tokens(obj: Any) -> int:
    """Rough token estimate (~1.8 chars/token, matching the rest of the codebase),
    plus a small per-message overhead for message lists."""
    if isinstance(obj, str):
        return int(len(obj) / 1.8) + 1
    if isinstance(obj, list):
        total = 0
        for m in obj:
            c = m.get("content", "") if isinstance(m, dict) else str(m)
            total += int(len(c) / 1.8) + 4  # role/format overhead
        return total
    return int(len(str(obj)) / 1.8) + 1


def compaction_enabled() -> bool:
    return os.environ.get("HASHMM_CONTEXT_COMPACTION", "0").strip().lower() in ("1", "true", "yes", "on")


def token_budget() -> int:
    try:
        return max(256, int(os.environ.get("HASHMM_CONTEXT_TOKEN_BUDGET", "6000")))
    except Exception:
        return 6000


def keep_recent() -> int:
    try:
        return max(1, int(os.environ.get("HASHMM_CONTEXT_KEEP_RECENT", "6")))
    except Exception:
        return 6


# ── compaction ─────────────────────────────────────────────────────────────
@dataclass
class CompactionResult:
    messages: list[dict]          # compacted window: [summary?] + pinned + recent
    summary: str = ""             # summary text folded in (or "")
    dropped: int = 0              # number of older messages summarized
    compacted: bool = False       # whether compaction actually happened


def _is_pinned(m: dict, pinned_ids: set) -> bool:
    return bool(m.get("pinned")) or (m.get("id") in pinned_ids if m.get("id") is not None else False)


def _default_summarize(messages: list[dict]) -> str:
    """Deterministic extractive fallback summary (no LLM). Keeps it readable and
    bounded: one line per older turn, role-tagged, truncated."""
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content", "") or "").strip().replace("\n", " ")
        if content:
            lines.append(f"- {role}: {content[:120]}")
    return "\n".join(lines)


def compact(messages: list[dict], *, budget: int | None = None, keep: int | None = None,
            summarize_fn: Callable[[list[dict]], str] | None = None,
            pinned_ids: set | None = None) -> CompactionResult:
    """Compact a message list to fit ``budget`` tokens.

    Keeps the last ``keep`` messages verbatim + any pinned messages; folds the
    rest into a single summary system message. Recent messages are never dropped;
    pinned messages are always retained. No-op (returns the input) when already
    under budget or on any error.
    """
    try:
        budget = budget if budget is not None else token_budget()
        keep = keep if keep is not None else keep_recent()
        pinned_ids = pinned_ids or set()
        msgs = list(messages or [])
        if not msgs or estimate_tokens(msgs) <= budget:
            return CompactionResult(msgs, "", 0, False)

        keep = min(keep, len(msgs))
        recent = msgs[len(msgs) - keep:]
        older = msgs[: len(msgs) - keep]

        pinned_older = [m for m in older if _is_pinned(m, pinned_ids)]
        to_summarize = [m for m in older if not _is_pinned(m, pinned_ids)]

        # PreCompact hook: fires before older messages are summarized away, so a
        # registered hook can archive / extract key facts first. No-op when none
        # registered → zero behaviour change. Never raises.
        if to_summarize:
            try:
                from hashmm.hooks import run_compact_hooks
                run_compact_hooks(to_summarize, {"budget": budget, "keep": keep,
                                                 "dropped": len(to_summarize)})
            except Exception as e:
                log_suppressed(logger, e)

        summary = ""
        if to_summarize:
            try:
                summary = (summarize_fn or _default_summarize)(to_summarize) or ""
            except Exception as e:
                log_suppressed(logger, e)
                summary = _default_summarize(to_summarize)

        out: list[dict] = []
        if summary:
            out.append({"role": "system",
                        "content": "[早期对话摘要 / earlier-context summary]\n" + summary})
        out.extend(pinned_older)   # keep pinned older turns verbatim, in order
        out.extend(recent)
        return CompactionResult(out, summary, len(to_summarize), True)
    except Exception as e:
        log_suppressed(logger, e)
        return CompactionResult(list(messages or []), "", 0, False)


# ── externalized state (bounded working context) ─────────────────────────
_KEY_RE = re.compile(r"[^A-Za-z0-9_.\-]")


class ExternalState:
    """File-backed state + event log keyed by conversation/task id. Lets the
    working context stay bounded while the full history is preserved on disk."""

    def __init__(self, base_dir: str | Path | None = None):
        base = base_dir or os.environ.get("HASHMM_AGENT_STATE_DIR", "data/agent_state")
        self.base = Path(base)
        try:
            self.base.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log_suppressed(logger, e)

    def _safe(self, key: str) -> str:
        s = _KEY_RE.sub("_", str(key))[:120]
        return s or "default"

    def save(self, key: str, data: dict) -> bool:
        try:
            (self.base / f"{self._safe(key)}.state.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return True
        except Exception as e:
            log_suppressed(logger, e)
            return False

    def load(self, key: str) -> dict | None:
        try:
            p = self.base / f"{self._safe(key)}.state.json"
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except Exception as e:
            log_suppressed(logger, e)
            return None

    def append_event(self, key: str, event: dict) -> bool:
        try:
            rec = {"ts": time.time(), **event}
            with (self.base / f"{self._safe(key)}.events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return True
        except Exception as e:
            log_suppressed(logger, e)
            return False

    def read_events(self, key: str) -> list[dict]:
        try:
            p = self.base / f"{self._safe(key)}.events.jsonl"
            if not p.exists():
                return []
            return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except Exception as e:
            log_suppressed(logger, e)
            return []
