"""Server Integration — drop-in hooks for server.py chat route.

Usage in server.py:

    # 1. Import at top (after other imports)
    from hashmm.server_hooks import retrieval_hook

    # 2. In generate_sse(), BEFORE building fast_msgs (around line 950):
    retrieval_context, retrieval_sources = retrieval_hook(q, history)

    # 3. Insert context into system prompt:
    if retrieval_context:
        sys_prompt = SYSTEM_PROMPT + retrieval_context
        sources = retrieval_sources
    
    # 4. In the done_data, replace sources=[]:
    done_data = {..., "sources": retrieval_sources, ...}

That's it. 4 lines of change in server.py.
"""
from __future__ import annotations
import time
from hashmm.utils import get_logger

logger = get_logger("hashmm.server_hooks")

# Global retrieval pipeline (initialized once on first call)
_chat_retrieval = None
_init_attempted = False


def _ensure_init():
    global _chat_retrieval, _init_attempted
    if _init_attempted:
        return
    _init_attempted = True
    try:
        from hashmm.chat_retrieval import ChatRetrieval
        _chat_retrieval = ChatRetrieval()
        logger.info("Server retrieval hook initialized")
    except Exception as e:
        logger.warning(f"Retrieval hook init failed: {e}")


def retrieval_hook(query: str, history: list[dict],
                   top_k: int = 5) -> tuple[str, list[dict]]:
    """Main hook for server.py — call before LLM.

    Args:
        query: Current user query (already expanded/rewritten)
        history: Conversation history [{"role": ..., "content": ...}]

    Returns:
        (context_injection, sources)
        - context_injection: Text to append to system prompt (empty if no retrieval needed)
        - sources: List of source dicts for the frontend
    """
    _ensure_init()
    if not _chat_retrieval:
        return "", []

    try:
        # Use ChatRetrieval to decide and search
        if not _chat_retrieval.should_search(query):
            return "", []

        # Rewrite query based on conversation context
        search_query = _chat_retrieval.rewrite_query(query, history)

        # Search knowledge base
        _chat_retrieval._ensure_pipeline()
        if not _chat_retrieval._pipeline:
            return "", []

        t0 = time.time()
        response = _chat_retrieval._pipeline.search(search_query, top_k=top_k)

        if not response.results:
            return "", []

        # Build injection text
        parts = []
        sources = []
        for i, r in enumerate(response.results):
            idx = i + 1
            source = r.filename or r.doc_id
            page_str = f" 第{r.page}页" if r.page > 0 else ""
            parts.append(f"[{idx}] {source}{page_str}\n{r.text[:500]}")
            sources.append({
                "id": idx, "text": r.text[:200], "filename": r.filename,
                "page": r.page, "score": round(r.score, 3),
            })

        elapsed = round((time.time() - t0) * 1000)
        context = (
            f"\n\n## 知识库检索结果（{len(sources)}条，{elapsed}ms）\n"
            f"请基于以下内容回答，引用时标注 [1][2]。\n\n"
            + "\n\n".join(parts)
        )

        logger.info(f"Retrieval hook: {len(sources)} results for '{search_query[:30]}' ({elapsed}ms)")
        return context, sources

    except Exception as e:
        logger.warning(f"Retrieval hook error: {e}")
        return "", []


def startup_hook():
    """Call during server startup to preload retrieval indexes.

    Usage in server.py _load() or lifespan:
        from hashmm.server_hooks import startup_hook
        startup_hook()
    """
    _ensure_init()
    if _chat_retrieval:
        _chat_retrieval._ensure_pipeline()
        if _chat_retrieval._pipeline:
            vi = _chat_retrieval._pipeline.vector_index
            bm25 = _chat_retrieval._pipeline.bm25_index
            logger.info(f"Retrieval indexes preloaded: {vi.total_vectors} vectors, {bm25.size} BM25 docs")
        else:
            logger.info("No retrieval indexes found (upload documents first)")
