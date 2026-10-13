"""Benchmark runner — orchestrates dataset → retriever → evaluator."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from hashmm.benchmark.evaluator import DEFAULT_METRICS, evaluate
from hashmm.benchmark.retrievers import BaseRetriever
from hashmm.benchmark.vidore_loader import ViDoReDataset
from hashmm.utils import get_logger

logger = get_logger("hashmm.benchmark.runner")


@dataclass
class RunResult:
    """One full eval run, ready to serialise as JSON or render as a table row."""
    model: str
    dataset: str
    metrics: dict[str, float] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "model": self.model, "dataset": self.dataset,
            "metrics": self.metrics, "stats": self.stats,
            "timestamp": self.timestamp,
        }


def run_benchmark(
    dataset: ViDoReDataset,
    retriever: BaseRetriever,
    *,
    top_k: int = 100,
    metrics: tuple[str, ...] = DEFAULT_METRICS,
) -> RunResult:
    """Run one (model, dataset) combination end-to-end.

    Steps:
        1. Make sure dataset is loaded.
        2. retriever.index(corpus)
        3. retriever.retrieve(queries, top_k)
        4. evaluate against qrels
        5. Bundle into a RunResult.
    """
    if not dataset.corpus:
        dataset.load()

    logger.info("running %s on %s (%d docs, %d queries)",
                retriever.name, dataset.name,
                len(dataset.corpus), len(dataset.queries))

    # Index
    t_index_start = time.time()
    retriever.index(dataset.corpus)
    t_index = time.time() - t_index_start

    # Retrieve
    t_retr_start = time.time()
    results = retriever.retrieve(dataset.queries, top_k=top_k)
    t_retr = time.time() - t_retr_start

    # Evaluate
    metrics_out = evaluate(dataset.qrels, results, metrics=metrics)

    # Bundle
    stats = {
        "n_docs": len(dataset.corpus),
        "n_queries": len(dataset.queries),
        "n_qrels": sum(len(v) for v in dataset.qrels.values()),
        "top_k": top_k,
        "index_size_bytes": retriever.index_size_bytes(),
        "index_size_mb": retriever.index_size_bytes() / 1024 / 1024,
        "index_time_sec": round(t_index, 2),
        "retrieve_time_sec": round(t_retr, 2),
        "avg_query_ms": round(retriever.avg_query_ms, 3),
    }
    return RunResult(model=retriever.name, dataset=dataset.name,
                     metrics=metrics_out, stats=stats)
