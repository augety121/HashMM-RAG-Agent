"""Benchmark suite (M7): standard ViDoRe v2 evaluation for HashMM-RAG.

Public API:
    from hashmm.benchmark import (
        ViDoReDataset, evaluate, run_benchmark,
        BGEM3Dense, HashMMRetriever,
        dump_json, dump_markdown, make_markdown_table,
    )
"""

from hashmm.benchmark.evaluator import DEFAULT_METRICS, evaluate
from hashmm.benchmark.ocr import OCRCache, ocr_image
from hashmm.benchmark.reports import (
    PUBLIC_BASELINES,
    dump_json,
    dump_markdown,
    make_markdown_table,
)
from hashmm.benchmark.retrievers import (
    BaseRetriever,
    BGEM3Dense,
    HashMMRetriever,
)
from hashmm.benchmark.runner import RunResult, run_benchmark
from hashmm.benchmark.vidore_loader import ViDoReDataset

__all__ = [
    "ViDoReDataset",
    "evaluate",
    "DEFAULT_METRICS",
    "run_benchmark",
    "RunResult",
    "BaseRetriever",
    "BGEM3Dense",
    "HashMMRetriever",
    "dump_json",
    "dump_markdown",
    "make_markdown_table",
    "PUBLIC_BASELINES",
    "OCRCache",
    "ocr_image",
]
