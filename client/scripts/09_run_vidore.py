#!/usr/bin/env python3
"""09 — Run ViDoRe v2 benchmark.

Usage:
    # M7.1: BGE-M3 baseline only (no hash net needed)
    python scripts/09_run_vidore.py \\
        --dataset biomedical_lectures_eng_v2 \\
        --retriever bge_m3

    # M7.2: HashMM-RAG (requires trained hash net at cfg.hash_net_ckpt)
    python scripts/09_run_vidore.py \\
        --dataset biomedical_lectures_eng_v2 \\
        --retriever hashmm

    # Both in one go (longest, full comparison table)
    python scripts/09_run_vidore.py \\
        --dataset biomedical_lectures_eng_v2 \\
        --retriever both

Outputs (under ./benchmarks/):
    {dataset}_{model}.json      raw metrics
    {dataset}_report.md         markdown comparison table
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hashmm.benchmark import (
    BGEM3Dense,
    HashMMRetriever,
    ViDoReDataset,
    dump_json,
    dump_markdown,
    run_benchmark,
)
from hashmm.config import HashMMConfig
from hashmm.utils import get_logger

console = Console()
logger = get_logger("scripts.09_run_vidore")


def _render_run_row(table: Table, run) -> None:
    m = run.metrics
    s = run.stats
    table.add_row(
        run.model,
        f"{m.get('ndcg_cut_5', 0):.4f}",
        f"{m.get('ndcg_cut_10', 0):.4f}",
        f"{m.get('recall_5', 0):.4f}",
        f"{m.get('recall_10', 0):.4f}",
        f"{m.get('map', 0):.4f}",
        f"{s.get('index_size_mb', 0):.2f} MB",
        f"{s.get('avg_query_ms', 0):.2f} ms",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True,
                    help="ViDoRe v2 dataset short name "
                         "(e.g. biomedical_lectures_eng_v2)")
    ap.add_argument("--retriever", default="bge_m3",
                    choices=("bge_m3", "hashmm", "both"))
    ap.add_argument("--top-k", type=int, default=100,
                    help="depth of retrieved list (default 100; eval cuts at 5/10)")
    ap.add_argument("--cache-dir", default="./benchmark_cache")
    ap.add_argument("--out-dir", default="./benchmarks")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--stage1-k", type=int, default=200,
                    help="Hamming candidate count for HashMM retriever's "
                         "first stage (only used for --retriever hashmm/both)")
    ap.add_argument("--force-reload", action="store_true",
                    help="redownload dataset (don't use cache)")
    ap.add_argument("--ocr", choices=("none", "tesseract", "paddleocr"),
                    default="tesseract",
                    help="OCR engine. paddleocr is ~3x better on slides. "
                         "Pre-req: `pip install paddlepaddle-gpu paddleocr`")
    ap.add_argument("--ocr-workers", type=int, default=4)
    ap.add_argument("--ocr-lang", default="eng",
                    help="Tesseract language code (default eng). "
                         "For multilingual splits use e.g. 'eng+fra'.")
    ap.add_argument("--force-reocr", action="store_true",
                    help="Force re-OCR even if text already cached "
                         "(use when switching OCR engines)")
    args = ap.parse_args()

    cfg = HashMMConfig()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Dataset ───────────────────────────────────────────────────────
    console.print(Panel(
        f"Dataset:  [bold]vidore/{args.dataset}[/bold]\n"
        f"Retriever: [bold]{args.retriever}[/bold]\n"
        f"Cache:    {args.cache_dir}\n"
        f"Output:   {out_dir}",
        title="ViDoRe v2 benchmark",
    ))

    ds = ViDoReDataset(args.dataset, cache_dir=args.cache_dir)
    try:
        ds.load(
            force_reload=args.force_reload,
            ocr=args.ocr if args.ocr != "none" else None,
            ocr_workers=args.ocr_workers,
            ocr_lang=args.ocr_lang,
            force_reocr=args.force_reocr,
        )
    except ImportError as e:
        console.print(f"[red]OCR dependency missing: {e}[/red]")
        console.print(
            "[yellow]Install with:\n"
            "  apt install tesseract-ocr tesseract-ocr-eng\n"
            "  pip install pytesseract[/yellow]"
        )
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]failed to load dataset: {e}[/red]")
        console.print(
            "[yellow]Hint: ensure HF_ENDPOINT is set to your mirror,\n"
            "and `pip install datasets` is up to date.[/yellow]"
        )
        sys.exit(1)

    stats = ds.stats()
    console.print(f"[green]✓[/green] loaded: {stats}")

    if stats["n_docs_with_text"] == 0:
        console.print(
            "[red]ERROR:[/red] no docs in corpus contain text. "
            "Try --ocr tesseract (default) and install tesseract-ocr."
        )
        sys.exit(2)

    # ── Build retrievers ──────────────────────────────────────────────
    retrievers = []
    if args.retriever in ("bge_m3", "both"):
        retrievers.append(BGEM3Dense(cfg, batch_size=args.batch_size))
    if args.retriever in ("hashmm", "both"):
        if not cfg.hash_net_ckpt.exists():
            console.print(
                f"[red]hash net checkpoint missing: {cfg.hash_net_ckpt}[/red]\n"
                "Train one with scripts/03_train_hash_net.py first, or use "
                "--retriever bge_m3 for the baseline-only run."
            )
            sys.exit(3)
        retrievers.append(HashMMRetriever(
            cfg, stage1_k=args.stage1_k, batch_size=args.batch_size,
        ))

    # ── Run each retriever ────────────────────────────────────────────
    runs = []
    for r in retrievers:
        console.rule(f"[bold]{r.name}[/bold]")
        result = run_benchmark(ds, r, top_k=args.top_k)
        runs.append(result)
        # Per-run JSON
        out_path = out_dir / f"{args.dataset}_{r.name.replace('/', '_')}.json"
        dump_json(result, out_path)

    # ── Render summary ────────────────────────────────────────────────
    console.rule("[bold]Summary[/bold]")
    tbl = Table(title=f"vidore/{args.dataset}", show_header=True)
    for col in ("Model", "nDCG@5", "nDCG@10", "R@5", "R@10",
                "MAP", "Index", "Lookup"):
        tbl.add_column(col)
    for r in runs:
        _render_run_row(tbl, r)
    console.print(tbl)

    # Markdown report (includes public baselines as context)
    md_path = out_dir / f"{args.dataset}_report.md"
    dump_markdown(runs, md_path, dataset_name=args.dataset)
    console.print(f"\n[green]✓[/green] markdown report → {md_path}")


if __name__ == "__main__":
    main()
