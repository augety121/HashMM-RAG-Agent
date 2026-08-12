"""Retriever Bridge — connects RetrievalPipeline to server's kb_search tool executor.

v5.0: Simplified. Only uses the new FAISS+BM25 RetrievalPipeline.
Old hash search wrapper removed.
"""
from __future__ import annotations
from pathlib import PurePath
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.retriever_bridge")

_pipeline = None


def _document_scope(args: dict, ctx: dict | None) -> list[str]:
    """Return the bounded, server-attached document scope for this tool call.

    The model may repeat the scope in arguments for compatibility, but a
    request context wins so it cannot widen the user's explicit selection.
    """
    context_scope = (ctx or {}).get("doc_filter")
    raw = context_scope if context_scope is not None else (
        args.get("doc_filter") or args.get("document_filter") or []
    )
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return list(dict.fromkeys(
        str(item).strip()[:260]
        for item in raw
        if str(item).strip()
    ))[:40]


def init_retriever(**kwargs):
    """Initialize the retrieval pipeline. Called from server.py _load()."""
    global _pipeline
    try:
        from hashmm.retrieval_pipeline import RetrievalPipeline
        _pipeline = RetrievalPipeline()
        _pipeline.load()
        vi = _pipeline.vector_index
        bm25 = _pipeline.bm25_index
        logger.info(f"Retriever bridge ready: {vi.total_vectors} vectors, {bm25.size} BM25 docs")

        # v7.0: Reset ChatRetrieval singleton so it picks up the new pipeline
        try:
            from hashmm.chat_retrieval import ChatRetrieval
            inst = ChatRetrieval._instance
            if inst is not None:
                inst.reset()
                logger.info("ChatRetrieval singleton reset after pipeline reload")
        except Exception as _e:
            log_suppressed(logger, _e)

        # v7.0: Clear embedding cache (pipeline data changed)
        try:
            from hashmm.encoder_pool import EncoderPool
            EncoderPool.clear_cache()
        except Exception as _e:
            log_suppressed(logger, _e)

        return True
    except Exception as e:
        logger.warning(f"Retriever bridge init failed: {e}")
        return False


def get_pipeline():
    """Get the initialized pipeline (for direct use by ChatRetrieval etc.)."""
    global _pipeline
    if not _pipeline:
        init_retriever()
    return _pipeline


def kb_search_bridge(args: dict, ctx: dict = None) -> dict:
    """Execute kb_search using RetrievalPipeline.

    Args:
        args: {"query": "...", "top_k": 5}

    Returns:
        {"results": [...], "num_results": N}
    """
    global _pipeline
    scope = _document_scope(args, ctx)
    if not _pipeline:
        init_retriever()
    from hashmm.retrieval.contract import build_retrieval_contract
    if not _pipeline:
        return {"results": [], "num_results": 0, "error": "No retrieval pipeline available",
                "document_scope": scope,
                "retrieval_contract": build_retrieval_contract(query=str(args.get("query") or ""), results=())}

    query = args.get("query", "")
    top_k = args.get("top_k")
    if top_k is None:
        # V311：未显式指定宽度时按自适应路由取（complex=12/multi_hop=8/single=5）。
        try:
            from hashmm.agent.adaptive_rag import route
            top_k = route(query).top_k or 5
        except Exception:  # noqa: BLE001
            top_k = 5
    top_k = max(1, int(top_k))

    if not query.strip():
        return {"results": [], "num_results": 0, "error": "Empty query",
                "document_scope": scope,
                "retrieval_contract": build_retrieval_contract(query="", requested_top_k=top_k, results=())}

    try:
        response = _pipeline.search(
            query,
            top_k=top_k,
            filters={"filename": scope} if scope else None,
        )
        # 时效性：把失效/归档文档从 agent 检索结果里剔除（安全，失败则原样返回）
        try:
            from hashmm.api import doc_validity as _dv
            _filtered = _dv.filter_results(response.results)
        except Exception:
            _filtered = response.results
        results = []
        for r in _filtered:
            results.append({
                "content": r.text,
                "source": r.filename or r.doc_id,
                "filename": r.filename,
                "page": r.page,
                "section": r.section,
                "score": round(r.score, 3),
            })

        # Explicit attachment selection is a fail-closed resource boundary.
        # Dense search already receives a filename filter, but older sparse
        # indexes do not.  Enforce the exact same boundary after RRF so a BM25
        # candidate can never escape the user's selected files.
        scope_dropped = 0
        if scope:
            allowed = {
                str(item).replace("\\", "/").strip().casefold()
                for item in scope if str(item).strip()
            }
            allowed_names = {PurePath(item).name.casefold() for item in allowed}
            scoped_results = []
            for item in results:
                raw = str(item.get("filename") or item.get("source") or "").replace("\\", "/").strip()
                if raw.casefold() in allowed or PurePath(raw).name.casefold() in allowed_names:
                    scoped_results.append(item)
                else:
                    scope_dropped += 1
            results = scoped_results

        from hashmm.retrieval.expected_gain import select_expected_gain
        before_gain = len(results)
        results, gain_contract = select_expected_gain(query, results, top_k=top_k)

        # Format as readable string for agent tool output
        contract = build_retrieval_contract(
            query=query,
            rewritten_query=response.expanded_query,
            requested_top_k=top_k,
            total_candidates=response.total_candidates,
            candidate_top_k=getattr(response, "candidate_top_k", 0),
            results=results,
            elapsed_ms=response.elapsed_ms,
            strategy="hybrid",
            rerank_method=getattr(response, "rerank_method", ""),
            filtered_count=max(0, len(response.results) - len(_filtered))
                           + scope_dropped + max(0, before_gain - len(results)),
        )
        contract["document_scope"] = list(scope)
        contract["scope_enforced"] = bool(scope)
        contract["scope_leakage_count"] = 0
        contract["expected_gain"] = gain_contract
        if not results:
            return {"results": [], "num_results": 0,
                    "text": (
                        "用户选定的资料范围内未找到相关结果。"
                        if scope else "知识库中未找到相关结果。"
                    ),
                    "document_scope": scope,
                    "retrieval_contract": contract}

        parts = []
        for i, r in enumerate(results, 1):
            source = r.get("filename", r.get("source", ""))
            page_str = f" p.{r['page']}" if r.get("page", -1) > 0 else ""
            parts.append(f"[{i}] {source}{page_str}\n{r['content'][:500]}")

        # V205 P1-5：图片资源库尾挂——文本命中图片时附提示（不参与 RRF，不影响原排序）
        try:
            from hashmm.retrieval.image_store import search_text as _img_search
            _imgs = _img_search(query, top_k=2, owner=str((ctx or {}).get("user_id") or ""))
            for _im in _imgs:
                parts.append(f"[图] {_im['filename']} — {_im.get('caption','')[:120]}（图片库 id={_im['id']}）")
        except Exception:
            pass
        return {
            "results": results,
            "num_results": len(results),
            "elapsed_ms": response.elapsed_ms,
            "document_scope": scope,
            "retrieval_contract": contract,
            "text": f"找到 {len(results)} 条相关结果：\n\n" + "\n\n".join(parts),
        }
    except Exception as e:
        logger.warning(f"kb_search failed: {e}")
        return {"results": [], "num_results": 0, "error": str(e),
                "document_scope": scope,
                "retrieval_contract": build_retrieval_contract(query=query, requested_top_k=top_k, results=())}
