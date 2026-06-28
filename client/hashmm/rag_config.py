"""RAG Configuration and Evaluation — centralized config + quality metrics.

D6: All RAG parameters in one place, hot-updatable via API.
C4: Retrieval quality evaluation (RAGAS-inspired metrics).
C3: Adaptive chunk size selection.
"""
from __future__ import annotations
import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.rag_config")

CONFIG_PATH = Path("data/rag_config.json")


@dataclass
class RAGConfig:
    """Centralized RAG configuration — all tunable parameters."""

    # Retrieval
    retrieval_mode: str = "mix"
    top_k: int = 10
    rerank_enabled: bool = False
    rerank_top_k: int = 5
    rrf_weights: list[float] = field(default_factory=lambda: [0.40, 0.30, 0.15, 0.15])

    # Chunking (C3: adaptive)
    chunk_size: int = 800
    chunk_overlap: int = 100
    chunk_strategy: str = "recursive"
    adaptive_chunk: bool = True  # C3: auto-select chunk size by doc type

    # KG Extraction
    kg_enabled: bool = True
    kg_extraction_model: str = "deepseek-chat"
    kg_max_entities_per_chunk: int = 15
    kg_max_relations_per_chunk: int = 10

    # Community
    community_algorithm: str = "greedy_modularity"
    community_min_size: int = 3

    # Citation
    citation_enabled: bool = True
    citation_max_sources: int = 5

    # Multi-hop
    multi_hop_enabled: bool = True
    multi_hop_max_depth: int = 2

    # Model
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    def save(self, path: Path | str | None = None):
        p = Path(path) if path else CONFIG_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "RAGConfig":
        p = Path(path) if path else CONFIG_PATH
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception as e:
                logger.warning(f"Failed to load RAG config: {e}")
        return cls()

    def to_dict(self) -> dict:
        return asdict(self)

    def update(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.save()


# Singleton
_config: RAGConfig | None = None


def get_rag_config() -> RAGConfig:
    global _config
    if _config is None:
        _config = RAGConfig.load()
    return _config


# ── C3: Adaptive Chunk Size ──

def adaptive_chunk_size(filename: str, text: str) -> tuple[int, str]:
    """Select optimal chunk size and strategy based on document characteristics.

    Returns:
        (chunk_size, strategy)
    """
    ext = Path(filename).suffix.lower() if filename else ""
    text_len = len(text)
    avg_line_len = text_len / max(text.count("\n"), 1)

    # Academic paper (long paragraphs, sections)
    if ext == ".pdf" or ext == ".tex":
        if text_len > 50000:
            return 1200, "paragraph"  # Long paper → large chunks
        return 800, "paragraph"

    # Code files
    if ext in (".py", ".js", ".ts", ".java", ".cpp", ".go", ".rs"):
        return 600, "recursive"  # Smaller chunks for code

    # Markdown / docs
    if ext in (".md", ".rst", ".txt"):
        if avg_line_len > 100:
            return 800, "recursive"
        return 500, "paragraph"

    # CSV / structured data
    if ext in (".csv", ".tsv", ".json"):
        return 400, "fixed"

    # Default
    return 800, "recursive"


# ── C4: Retrieval Evaluation ──

class RetrievalEvaluator:
    """RAGAS-inspired retrieval quality metrics.

    Evaluates retrieval quality without ground truth labels.
    Uses LLM-as-judge when available.
    """

    def __init__(self, llm_fn=None):
        self.llm_fn = llm_fn

    def evaluate(self, query: str, contexts: list[str], answer: str) -> dict:
        """Compute retrieval quality metrics.

        Returns:
            {
                "context_relevance": 0.85,   # How relevant are the retrieved contexts
                "answer_faithfulness": 0.90,  # Does the answer stick to the contexts
                "answer_relevance": 0.88,     # Does the answer address the query
                "context_coverage": 0.75,     # How much of the answer is covered by contexts
            }
        """
        metrics = {}

        # 1. Context Relevance (no LLM needed)
        metrics["context_relevance"] = self._context_relevance(query, contexts)

        # 2. Answer Relevance (no LLM needed)
        metrics["answer_relevance"] = self._answer_relevance(query, answer)

        # 3. Context Coverage
        metrics["context_coverage"] = self._context_coverage(contexts, answer)

        # 4. Answer Faithfulness (needs LLM)
        if self.llm_fn:
            metrics["answer_faithfulness"] = self._answer_faithfulness(contexts, answer)

        # Overall score
        scores = [v for v in metrics.values() if isinstance(v, (int, float))]
        metrics["overall"] = round(sum(scores) / len(scores), 3) if scores else 0

        return metrics

    def _context_relevance(self, query: str, contexts: list[str]) -> float:
        """Keyword overlap between query and retrieved contexts."""
        if not contexts:
            return 0.0
        q_words = set(re.findall(r'\w{2,}', query.lower()))
        if not q_words:
            return 0.5

        relevant_count = 0
        for ctx in contexts:
            c_words = set(re.findall(r'\w{2,}', ctx.lower()))
            overlap = len(q_words & c_words)
            if overlap >= max(1, len(q_words) * 0.3):
                relevant_count += 1

        return round(relevant_count / len(contexts), 3)

    def _answer_relevance(self, query: str, answer: str) -> float:
        """Keyword overlap between query and answer."""
        q_words = set(re.findall(r'\w{2,}', query.lower()))
        a_words = set(re.findall(r'\w{2,}', answer.lower()))
        if not q_words:
            return 0.5
        overlap = len(q_words & a_words)
        return round(min(overlap / len(q_words), 1.0), 3)

    def _context_coverage(self, contexts: list[str], answer: str) -> float:
        """How much of the answer content can be found in contexts."""
        if not answer or not contexts:
            return 0.0
        a_words = set(re.findall(r'\w{2,}', answer.lower()))
        if not a_words:
            return 0.5
        all_ctx = " ".join(contexts).lower()
        c_words = set(re.findall(r'\w{2,}', all_ctx))
        covered = len(a_words & c_words)
        return round(covered / len(a_words), 3)

    def _answer_faithfulness(self, contexts: list[str], answer: str) -> float:
        """LLM-judged: does the answer only use information from contexts?"""
        if not self.llm_fn:
            return -1.0

        ctx_text = "\n\n".join(c[:500] for c in contexts[:5])
        prompt = (
            f"判断以下回答是否忠实于给定的参考资料。\n\n"
            f"参考资料：\n{ctx_text}\n\n"
            f"回答：\n{answer[:500]}\n\n"
            f"请只回答一个 0-1 之间的数字，表示忠实度（1=完全忠实，0=完全编造）："
        )
        try:
            if hasattr(self.llm_fn, 'quick_call'):
                raw = self.llm_fn.quick_call("你是评估专家。", prompt, max_tok=10)
            else:
                import inspect
                sig = inspect.signature(self.llm_fn)
                if len(list(sig.parameters)) == 1:
                    raw = self.llm_fn(f"你是评估专家。\n\n{prompt}")
                else:
                    raw = self.llm_fn([
                        {"role": "system", "content": "你是评估专家。"},
                        {"role": "user", "content": prompt},
                    ])
            match = re.search(r'(0?\.\d+|1\.0|[01])', raw.strip())
            if match:
                return round(float(match.group(1)), 3)
        except Exception as _e:
            log_suppressed(logger, _e)
        return -1.0
