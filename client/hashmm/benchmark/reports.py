"""Reports: JSON dumps + Markdown comparison tables for benchmark runs.

We hard-code published reference numbers for ColPali / BGE-M3 from the
ViDoRe v2 paper (Macé et al. 2025, arXiv:2505.17166) so a single-run
report still shows the competitive context. Override via --baseline-json.

These numbers are AGGREGATE across the v2 corpus and serve as orientation;
for the canonical per-dataset numbers see the ViDoRe leaderboard:
    https://huggingface.co/spaces/vidore/vidore-leaderboard
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from hashmm.benchmark.runner import RunResult
from hashmm.utils import get_logger

logger = get_logger("hashmm.benchmark.reports")


# ── Reference numbers from public sources ─────────────────────────────
# Source: Macé et al. 2025 (ViDoRe v2 paper), Table 2.
# These are AVERAGE ndcg_at_5 across the v2 corpus, not per-dataset.
# Update as the leaderboard moves.
PUBLIC_BASELINES = {
    "ColPali v1.3": {"ndcg_cut_5": 0.595, "source": "ViDoRe v2 paper Table 2"},
    "ColQwen2 v1.0": {"ndcg_cut_5": 0.625, "source": "ViDoRe v2 paper Table 2"},
    "BGE-M3 (chunked OCR)": {"ndcg_cut_5": 0.451, "source": "ViDoRe v2 paper Table 2"},
    "BM25 (chunked OCR)":   {"ndcg_cut_5": 0.342, "source": "ViDoRe v2 paper Table 2"},
}


def dump_json(result: RunResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
    logger.info("wrote %s", path)
    return path


def make_markdown_table(
    runs: Iterable[RunResult],
    include_public: bool = True,
    dataset_name: str | None = None,
) -> str:
    """Render a Markdown report with all runs and public baselines."""
    runs = list(runs)
    if not runs:
        return "_(no runs)_"

    dataset_name = dataset_name or runs[0].dataset

    lines = [
        f"# Benchmark Report — `{dataset_name}`",
        "",
        f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        "## Our runs",
        "",
        "| Model | nDCG@5 | nDCG@10 | R@5 | R@10 | MAP | MRR | Index | Lookup |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in runs:
        m = r.metrics
        s = r.stats
        lines.append(
            f"| **{r.model}** "
            f"| {m.get('ndcg_cut_5', 0):.4f} "
            f"| {m.get('ndcg_cut_10', 0):.4f} "
            f"| {m.get('recall_5', 0):.4f} "
            f"| {m.get('recall_10', 0):.4f} "
            f"| {m.get('map', 0):.4f} "
            f"| {m.get('recip_rank', 0):.4f} "
            f"| {s.get('index_size_mb', 0):.2f} MB "
            f"| {s.get('avg_query_ms', 0):.2f} ms |"
        )

    if include_public:
        lines += [
            "",
            "## Public baselines (ViDoRe v2 paper, **aggregate** numbers)",
            "",
            "| Model | nDCG@5 | source |",
            "| --- | --- | --- |",
        ]
        for name, info in PUBLIC_BASELINES.items():
            lines.append(
                f"| {name} | {info['ndcg_cut_5']:.4f} | {info['source']} |"
            )
        lines += [
            "",
            "> NOTE: public baselines above are **averaged across all v2 "
            "datasets**, not the single split we evaluated on. For dataset-"
            "specific reference numbers, check the official leaderboard: "
            "https://huggingface.co/spaces/vidore/vidore-leaderboard",
        ]

    if runs:
        lines += [
            "",
            "## Run details",
            "",
        ]
        for r in runs:
            lines += [
                f"### {r.model}",
                "",
                f"- Dataset: `{r.dataset}`",
                f"- Docs / Queries / Qrels: "
                f"{r.stats.get('n_docs')} / "
                f"{r.stats.get('n_queries')} / "
                f"{r.stats.get('n_qrels')}",
                f"- Index build time: {r.stats.get('index_time_sec', 0):.1f} s",
                f"- Retrieve total time: {r.stats.get('retrieve_time_sec', 0):.1f} s",
                f"- Average query latency: {r.stats.get('avg_query_ms', 0):.2f} ms",
                f"- Index size on disk: {r.stats.get('index_size_mb', 0):.2f} MB",
                "",
                f"Full metrics:",
                "",
                "```json",
                json.dumps(r.metrics, indent=2),
                "```",
                "",
            ]

    return "\n".join(lines)


def dump_markdown(runs: Iterable[RunResult], path: str | Path,
                  dataset_name: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    md = make_markdown_table(runs, dataset_name=dataset_name)
    path.write_text(md, encoding="utf-8")
    logger.info("wrote %s", path)
    return path
