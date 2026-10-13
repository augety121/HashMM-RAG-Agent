"""Provider-aware token accounting with an explicit confidence contract."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    method: str
    confidence: str

    def public(self) -> dict[str, Any]:
        return {
            "tokens": max(0, int(self.tokens)),
            "method": self.method,
            "confidence": self.confidence,
        }


class ProviderTokenCounter:
    """Count tokens without claiming a fallback estimate is exact.

    Provider adapters can inject their official tokenizer callback.  Otherwise
    HashMM uses a conservative multilingual estimate: CJK characters are close
    to one token, while non-CJK text is budgeted at roughly four characters per
    token with a safety margin.
    """

    def __init__(
        self,
        *,
        provider: str = "",
        model: str = "",
        count_fn: Callable[[str], int] | None = None,
    ):
        self.provider = str(provider or "")[:80]
        self.model = str(model or "")[:160]
        self.count_fn = count_fn

    def count(self, text: Any) -> TokenEstimate:
        value = str(text or "")
        if self.count_fn is not None:
            try:
                exact = int(self.count_fn(value))
                if exact >= 0:
                    return TokenEstimate(exact, "provider_tokenizer", "exact")
            except Exception:
                pass
        cjk = sum(
            1 for char in value
            if "\u3400" <= char <= "\u9fff"
            or "\u3040" <= char <= "\u30ff"
            or "\uac00" <= char <= "\ud7af"
        )
        other = max(0, len(value) - cjk)
        estimated = cjk + math.ceil(other / 4)
        # Reserve 12.5% for punctuation/tokenizer differences.
        return TokenEstimate(math.ceil(estimated * 1.125), "multilingual_fallback", "conservative")
