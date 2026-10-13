from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SearchRunState(str, Enum):
    QUEUED = "queued"
    SEARCHING = "searching"
    VERIFYING = "verifying"
    COMPARING = "comparing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


_MODES = {"fast", "verified", "deep", "rag_live"}


@dataclass(slots=True)
class SearchRequest:
    query: str
    mode: str = "verified"
    max_results: int = 8
    providers: list[str] = field(default_factory=list)
    freshness_days: int | None = None
    locale: str = "zh-CN"
    project_id: str | None = None

    def validate(self) -> "SearchRequest":
        self.query = " ".join(str(self.query or "").split())
        if not self.query or len(self.query) > 2000:
            raise ValueError("query 必须为 1–2000 个字符")
        self.mode = str(self.mode or "verified").lower()
        if self.mode not in _MODES:
            raise ValueError("mode 必须是 fast、verified、deep 或 rag_live")
        self.max_results = max(1, min(int(self.max_results or 8), 20))
        self.providers = list(dict.fromkeys(str(p).strip().lower() for p in self.providers if str(p).strip()))[:8]
        if self.freshness_days is not None:
            self.freshness_days = max(1, min(int(self.freshness_days), 3650))
        self.locale = str(self.locale or "zh-CN")[:20]
        self.project_id = str(self.project_id).strip()[:128] if self.project_id else None
        return self


@dataclass(slots=True)
class EvidenceRecord:
    id: str
    title: str
    url: str
    snippet: str
    provider: str
    domain: str = ""
    published_at: str | None = None
    retrieved_at: float = 0.0
    rank: int = 0
    provider_score: float | None = None
    freshness: str = "unknown"
    verification_status: str = "unverified"
    corroborating_providers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
