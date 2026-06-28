"""Auto Retrieval — automatically search knowledge base during conversation.

When the user asks a question, this module:
1. Detects if the query needs KB retrieval
2. Searches FAISS + BM25 + KG
3. Injects relevant context into the system prompt
4. Formats citations [1][2] in the response

Enterprise scenarios handled:
  - "去年Q3的销售数据" → search for Q3 sales report
  - "合同里的违约金条款" → search contract documents
  - "项目架构图" → search for architecture documents/images
  - "代码里的登录逻辑" → search code files
  - General chat ("你好") → skip retrieval
"""
from __future__ import annotations
import re
from hashmm.utils import get_logger

logger = get_logger("hashmm.auto_retrieval")

# Queries that DON'T need retrieval
_SKIP_PATTERNS = re.compile(
    r'^(你好|hi|hello|hey|嗨|谢谢|thanks|ok|好的|明白|'
    r'再见|bye|请继续|继续|是的|对|不是|不对|没有|有的|'
    r'帮我写|写一个|生成|创建|翻译|总结一下|解释一下)$',
    re.IGNORECASE,
)

# Queries that likely need retrieval
_RETRIEVAL_SIGNALS = re.compile(
    r'(文档|文件|论文|报告|合同|数据|代码|方案|'
    r'提到|说过|记录|里面|其中|关于|有没有|'
    r'根据|按照|参考|查找|搜索|检索|'
    r'什么是|怎么|如何|为什么|哪个|哪些|'
    r'对比|比较|区别|优缺点|'
    r'document|file|paper|report|code|data|'
    r'search|find|look up|according to)',
    re.IGNORECASE,
)


class AutoRetriever:
    """Automatically retrieve relevant context from knowledge base.

    Integrates with HybridRetriever to inject context into conversations.
    """

    def __init__(self, retriever=None, min_score: float = 0.3):
        """
        Args:
            retriever: HybridRetriever instance (or any object with .retrieve())
            min_score: Minimum relevance score to include a result
        """
        self.retriever = retriever
        self.min_score = min_score

    def should_retrieve(self, query: str) -> bool:
        """Decide whether a query needs knowledge base retrieval."""
        query = query.strip()
        if len(query) < 3:
            return False
        if _SKIP_PATTERNS.match(query):
            return False
        # Questions or retrieval signals → yes
        if query.endswith("?") or query.endswith("？"):
            return True
        if _RETRIEVAL_SIGNALS.search(query):
            return True
        # Long queries likely need context
        if len(query) > 30:
            return True
        return False

    def retrieve_context(self, query: str, top_k: int = 5) -> RetrievalContext:
        """Search knowledge base and return formatted context.

        Returns:
            RetrievalContext with context text, sources list, and system prompt injection.
        """
        if not self.retriever:
            return RetrievalContext.empty()

        try:
            response = self.retriever.retrieve(query, mode="mix", top_k=top_k)
            results = response.results

            if not results:
                return RetrievalContext.empty()

            # Filter by minimum score
            filtered = [r for r in results if r.score >= self.min_score]
            if not filtered:
                return RetrievalContext.empty()

            # Build context text with citation markers
            context_parts = []
            sources = []
            for i, r in enumerate(filtered[:top_k]):
                citation_id = i + 1
                context_parts.append(f"[{citation_id}] {r.text[:500]}")
                sources.append({
                    "id": citation_id,
                    "text": r.text[:200],
                    "source": getattr(r, 'source', '') or r.doc_id or '',
                    "page": getattr(r, 'page', -1),
                    "score": round(r.score, 3),
                    "chunk_id": r.chunk_id,
                })

            context_text = "\n\n".join(context_parts)

            # Build system prompt injection
            injection = (
                f"\n\n## 知识库检索结果（共 {len(sources)} 条相关内容）\n\n"
                f"{context_text}\n\n"
                f"请基于以上检索结果回答用户问题。引用时使用 [1][2] 等标记。"
                f"如果检索结果不足以回答，请说明并给出你的理解。"
            )

            # KG context
            kg_context = ""
            if response.kg_context:
                injection += f"\n\n## 知识图谱关联\n{response.kg_context[:500]}"

            return RetrievalContext(
                context=context_text,
                sources=sources,
                injection=injection,
                kg_context=response.kg_context,
                community_context=response.community_context,
                elapsed_ms=response.elapsed_ms,
            )

        except Exception as e:
            logger.warning(f"Auto-retrieval failed: {e}")
            return RetrievalContext.empty()


class RetrievalContext:
    """Holds retrieval results for injection into conversation."""

    def __init__(self, context: str = "", sources: list[dict] | None = None,
                 injection: str = "", kg_context: str = "",
                 community_context: str = "", elapsed_ms: int = 0):
        self.context = context
        self.sources = sources or []
        self.injection = injection
        self.kg_context = kg_context
        self.community_context = community_context
        self.elapsed_ms = elapsed_ms

    @classmethod
    def empty(cls) -> "RetrievalContext":
        return cls()

    @property
    def has_results(self) -> bool:
        return len(self.sources) > 0

    def format_citations_in_response(self, response_text: str) -> str:
        """Append citation list to the response if citations [1][2] are used."""
        if not self.sources:
            return response_text

        # Check if response contains citation markers
        citation_refs = set(re.findall(r'\[(\d+)\]', response_text))
        if not citation_refs:
            return response_text

        # Build citation footer
        footer_parts = ["\n\n---\n**参考来源：**"]
        for src in self.sources:
            if str(src["id"]) in citation_refs:
                source_name = src.get("source", "未知")
                page = src.get("page", -1)
                page_str = f" p.{page}" if page > 0 else ""
                footer_parts.append(
                    f"[{src['id']}] {source_name}{page_str} — {src['text'][:80]}..."
                )

        if len(footer_parts) > 1:
            return response_text + "\n".join(footer_parts)
        return response_text

    def to_dict(self) -> dict:
        return {
            "has_results": self.has_results,
            "num_sources": len(self.sources),
            "sources": self.sources,
            "elapsed_ms": self.elapsed_ms,
        }
