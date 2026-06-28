"""Retriever Bridge — connects RetrievalPipeline to server's kb_search tool executor.

v5.0: Simplified. Only uses the new FAISS+BM25 RetrievalPipeline.
Old hash search wrapper removed.
"""
from __future__ import annotations
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.retriever_bridge")

_pipeline = None


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
    if not _pipeline:
        init_retriever()
    if not _pipeline:
        return {"results": [], "num_results": 0, "error": "No retrieval pipeline available"}

    query = args.get("query", "")
    top_k = args.get("top_k", 5)

    if not query.strip():
        return {"results": [], "num_results": 0, "error": "Empty query"}

    try:
        response = _pipeline.search(query, top_k=top_k)
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

        # Format as readable string for agent tool output
        if not results:
            return {"results": [], "num_results": 0,
                    "text": "知识库中未找到相关结果。"}

        parts = []
        for i, r in enumerate(results, 1):
            source = r.get("filename", r.get("source", ""))
            page_str = f" p.{r['page']}" if r.get("page", -1) > 0 else ""
            parts.append(f"[{i}] {source}{page_str}\n{r['content'][:500]}")

        return {
            "results": results,
            "num_results": len(results),
            "elapsed_ms": response.elapsed_ms,
            "text": f"找到 {len(results)} 条相关结果：\n\n" + "\n\n".join(parts),
        }
    except Exception as e:
        logger.warning(f"kb_search failed: {e}")
        return {"results": [], "num_results": 0, "error": str(e)}
