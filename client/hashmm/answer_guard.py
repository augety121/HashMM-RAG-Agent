"""v17 Phase 25 (B1) — never return an empty answer.

The live generation path occasionally yields an empty string (observed on
overclaim / guarantee prompts like "保证盈利翻倍" and over-vague prompts like
"概括一下", where the LLM produced 0 chars). Returning nothing is unacceptable in
a production / enterprise system. This guard converts an empty / whitespace /
too-short generation into a safe, honest, non-fabricating fallback so the system
always responds with something useful instead of silence.

This is a *defensive* net (defense in depth), not a root-cause fix for why the
upstream model returned empty — both layers matter, but a user-facing system must
never surface a blank answer regardless of upstream behaviour.
"""
from __future__ import annotations

# Below this many non-whitespace chars we treat the answer as effectively empty.
_MIN_CHARS = 2

_FALLBACK_DEFAULT = (
    "抱歉，我暂时无法生成可靠的回答。请换一种问法或补充更具体的信息，我会尽力帮你。"
)
# For low-grounding / guarantee-style asks: explicitly decline to assert or
# guarantee without support. This is also the correct response to overclaim
# prompts ("保证…一定…"), so a blank turns into an honest refusal.
_FALLBACK_LOW_GROUNDING = (
    "根据现有资料，我无法对此做出可靠的回答或保证。"
    "如果你能提供更具体的背景或问题，我可以再帮你查证。"
)

_LOW_GROUNDING_MODES = {"augmented", "insufficient", "direct"}


def is_empty_answer(answer) -> bool:
    """True if the answer is None / blank / shorter than _MIN_CHARS non-blank chars."""
    if answer is None:
        return True
    s = str(answer).strip()
    return len(s) < _MIN_CHARS


def guard_empty_answer(answer, strategy: str | None = None) -> str:
    """Return `answer` unchanged if non-empty; otherwise a safe honest fallback.

    `strategy` (e.g. 'augmented' / 'insufficient' / 'grounded') tunes the wording:
    low-grounding strategies get an explicit "can't verify / guarantee" message.
    Never returns an empty string.
    """
    if not is_empty_answer(answer):
        return str(answer)
    if strategy in _LOW_GROUNDING_MODES:
        return _FALLBACK_LOW_GROUNDING
    return _FALLBACK_DEFAULT
