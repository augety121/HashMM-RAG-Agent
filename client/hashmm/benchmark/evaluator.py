"""Evaluation: pytrec_eval wrapper.

Given:
    qrels   : { query_id: { doc_id: int_relevance } }
    results : { query_id: { doc_id: float_score } }

Computes metrics:
    - ndcg_cut_5, ndcg_cut_10
    - recall_5, recall_10
    - map
    - recip_rank (MRR)

These are the metrics ColPali / ViDoRe leaderboard uses, so we report on
the same axis. We compute per-query and aggregate; aggregate is the mean.
"""

from __future__ import annotations

from typing import Iterable

from hashmm.utils import get_logger

logger = get_logger("hashmm.benchmark.evaluator")


DEFAULT_METRICS = ("ndcg_cut_5", "ndcg_cut_10",
                   "recall_5", "recall_10",
                   "map", "recip_rank")


def evaluate(
    qrels: dict[str, dict[str, int]],
    results: dict[str, dict[str, float]],
    metrics: Iterable[str] = DEFAULT_METRICS,
) -> dict[str, float]:
    """Compute aggregate retrieval metrics.

    Returns a flat dict like {"ndcg_cut_5": 0.456, "recall_10": 0.781, ...}.
    """
    try:
        import pytrec_eval
    except ImportError as e:
        raise ImportError(
            "pytrec_eval is required for benchmarks. "
            "`pip install pytrec_eval`"
        ) from e

    metrics = list(metrics)
    evaluator = pytrec_eval.RelevanceEvaluator(qrels, set(metrics))

    # pytrec_eval needs all scores as floats and doc-ids as strings
    coerced = {
        str(qid): {str(did): float(s) for did, s in scores.items()}
        for qid, scores in results.items()
    }

    per_query = evaluator.evaluate(coerced)
    if not per_query:
        return {m: 0.0 for m in metrics}

    # Mean across queries (BEIR convention)
    agg = {}
    for m in metrics:
        scores = [pq.get(m, 0.0) for pq in per_query.values()]
        agg[m] = sum(scores) / len(scores) if scores else 0.0

    n_queries = len(per_query)
    n_judged = sum(len(q) for q in qrels.values())
    logger.info("evaluated %d queries (%d total positive judgements)",
                n_queries, n_judged)
    return agg


def humanize_metrics(metrics: dict[str, float]) -> str:
    """Pretty one-liner."""
    return ", ".join(f"{k}={v:.4f}" for k, v in metrics.items())
