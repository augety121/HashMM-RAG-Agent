from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict
from typing import Any, Callable

from . import providers, store
from .contracts import EvidenceRecord, SearchRequest
from .verification import evidence_id, verify_and_dedupe

ProviderSearch = Callable[[str, str, str, int, dict[str, Any]], list[dict[str, Any]]]
LocalSearch = Callable[[str, str, str | None, int], list[dict[str, Any]]]


class RetrievalFabric:
    """Owner-scoped federated retrieval with durable, inspectable runs."""

    def __init__(self, *, provider_search: ProviderSearch | None = None,
                 local_search: LocalSearch | None = None) -> None:
        self.provider_search = provider_search or providers.search
        self.local_search = local_search or _default_local_search

    def provider_status(self, owner_id: str) -> list[dict[str, Any]]:
        return [{"provider": name, "label": spec.label, "kind": spec.kind,
                 "configured": providers.configured(owner_id, name),
                 "retired": False}
                for name, spec in providers.PROVIDERS.items()]

    def run(self, owner_id: str, request: SearchRequest) -> dict[str, Any]:
        created = self.create(owner_id, request)
        return self.execute(str(created["id"]), str(owner_id).strip(), request)

    def create(self, owner_id: str, request: SearchRequest) -> dict[str, Any]:
        owner = str(owner_id or "").strip()
        if not owner:
            raise ValueError("owner_id required")
        request.validate()
        run_id = "sr_" + uuid.uuid4().hex
        return store.create_run(run_id, owner, asdict(request))

    def execute(self, run_id: str, owner_id: str, request: SearchRequest) -> dict[str, Any]:
        owner = str(owner_id or "").strip()
        if not store.get_run(run_id, owner):
            raise KeyError("search run not found")
        return self._execute(run_id, owner, request.validate())

    def _execute(self, run_id: str, owner: str, request: SearchRequest) -> dict[str, Any]:
        store.update_run(run_id, owner, "searching")
        names = self._select_providers(owner, request)
        store.append_event(run_id, owner, "providers_selected", {"providers": names})
        per_provider = request.max_results if request.mode == "fast" else max(3, min(request.max_results, 8))
        options = {"mode": request.mode, "locale": request.locale, "freshness_days": request.freshness_days}
        records: list[EvidenceRecord] = []
        failures: list[dict[str, str]] = []
        executor = ThreadPoolExecutor(max_workers=min(4, max(1, len(names))), thread_name_prefix="search-provider")
        future_map = {executor.submit(self.provider_search, name, owner, request.query, per_provider, options): name for name in names}
        deadline = 18 if request.mode == "fast" else 35 if request.mode == "verified" else 55
        done, pending = wait(future_map, timeout=deadline)
        for future in done:
            name = future_map[future]
            try:
                rows = future.result()
                store.append_event(run_id, owner, "provider_completed", {"provider": name, "count": len(rows or [])})
                for rank, row in enumerate((rows or [])[:per_provider], 1):
                    title = str(row.get("title") or "").strip()[:500]
                    url = str(row.get("url") or "").strip()[:4000]
                    snippet = str(row.get("snippet") or "").strip()[:4000]
                    if not (title or url or snippet):
                        continue
                    records.append(EvidenceRecord(
                        id=evidence_id(url, title, snippet), title=title or "Untitled source", url=url,
                        snippet=snippet, provider=name, published_at=str(row.get("published_at") or "") or None,
                        retrieved_at=time.time(), rank=rank, provider_score=_safe_float(row.get("provider_score")),
                        metadata={"provider_kind": providers.PROVIDERS[name].kind},
                    ))
            except Exception as exc:
                code = _provider_error_code(exc)
                failures.append({"provider": name, "code": code})
                store.append_event(run_id, owner, "provider_failed", {"provider": name, "code": code})
        for future in pending:
            name = future_map[future]; future.cancel()
            failures.append({"provider": name, "code": "deadline_exceeded"})
            store.append_event(run_id, owner, "provider_failed", {"provider": name, "code": "deadline_exceeded"})
        executor.shutdown(wait=False, cancel_futures=True)

        current = store.get_run(run_id, owner)
        if current and current.get("state") == "cancelled":
            return current

        store.update_run(run_id, owner, "verifying")
        evidence = verify_and_dedupe(records, freshness_days=request.freshness_days)
        local_records: list[dict[str, Any]] = []
        comparison: dict[str, Any] | None = None
        if request.mode == "rag_live":
            store.update_run(run_id, owner, "comparing")
            try:
                local_records = self.local_search(owner, request.query, request.project_id, request.max_results)
            except Exception:
                failures.append({"provider": "local_rag", "code": "local_retrieval_failed"})
            comparison = _compare_local_live(local_records, evidence)

        current = store.get_run(run_id, owner)
        if current and current.get("state") == "cancelled":
            return current

        evidence.sort(key=lambda item: (
            0 if item.verification_status == "corroborated_listing" else 1,
            0 if item.freshness == "fresh" else 1,
            item.rank,
        ))
        output = {
            "run_id": run_id, "query": request.query, "mode": request.mode,
            "evidence": [item.to_dict() for item in evidence[:request.max_results]],
            "provider_failures": failures, "comparison": comparison,
            "local_evidence": local_records[:request.max_results],
            "disclaimer": "互证状态仅表示多个检索来源返回了相同条目，不等于事实真伪裁决。",
        }
        if evidence:
            state = "partial" if failures else "completed"
            store.update_run(run_id, owner, state, result=output)
        else:
            state = "failed"
            output["error_code"] = "no_evidence"
            store.update_run(run_id, owner, state, result=output, error_code="no_evidence")
        return store.get_run(run_id, owner) or output

    def _select_providers(self, owner: str, request: SearchRequest) -> list[str]:
        if request.providers:
            unknown = [name for name in request.providers if name not in providers.PROVIDERS]
            if unknown:
                raise ValueError("unknown providers: " + ",".join(unknown))
            return request.providers
        configured_names = [name for name in ("baidu", "brave", "exa", "tavily", "serper", "gemini", "doubao")
                            if providers.configured(owner, name)]
        if request.mode == "fast":
            return configured_names[:1] or ["duckduckgo"]
        if request.mode in {"verified", "rag_live"}:
            return configured_names[:3] or ["duckduckgo"]
        return configured_names[:6] or ["duckduckgo"]


def _safe_float(value: Any) -> float | None:
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None


def _provider_error_code(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, LookupError): return "not_configured"
    if "401" in text or "403" in text: return "authentication_failed"
    if "429" in text: return "rate_limited"
    if "timed out" in text or "timeout" in text: return "timeout"
    if "too_large" in text: return "response_too_large"
    return "provider_unavailable"


def _default_local_search(owner_id: str, query: str, project_id: str | None, count: int) -> list[dict[str, Any]]:
    from hashmm.retriever_bridge import kb_search_bridge
    context: dict[str, Any] = {"user_id": owner_id}
    if project_id: context["project_id"] = project_id
    result = kb_search_bridge({"query": query, "top_k": count}, context)
    rows = result.get("results") if isinstance(result, dict) else []
    normalized = []
    for row in rows or []:
        if not isinstance(row, dict): continue
        normalized.append({"title": row.get("source") or row.get("filename") or "Knowledge base",
                           "snippet": str(row.get("text") or "")[:4000], "score": row.get("score"),
                           "page": row.get("page"), "source_type": "private_rag"})
    return normalized


def _tokens(value: str) -> set[str]:
    import re
    return set(re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", value.lower()))


def _compare_local_live(local: list[dict[str, Any]], live: list[EvidenceRecord]) -> dict[str, Any]:
    local_terms = _tokens(" ".join(str(item.get("title") or "") + " " + str(item.get("snippet") or "") for item in local))
    live_terms = _tokens(" ".join(item.title + " " + item.snippet for item in live))
    overlap = local_terms & live_terms
    return {
        "status": "compared" if local and live else "insufficient_evidence",
        "local_count": len(local), "live_count": len(live), "shared_term_count": len(overlap),
        "possible_update": bool(local and live and len(overlap) < min(5, max(1, len(local_terms) // 10))),
        "warning": "词项差异仅用于发现潜在更新，不能自动判定知识库内容已过期。",
    }


_singleton: RetrievalFabric | None = None


def get_retrieval_fabric() -> RetrievalFabric:
    global _singleton
    if _singleton is None: _singleton = RetrievalFabric()
    return _singleton
