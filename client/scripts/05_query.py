#!/usr/bin/env python3
"""05 — Query the hash index.

Usage:
    # text → anything
    python scripts/05_query.py --query "find a chart of revenue by region"

    # image → text/image
    python scripts/05_query.py --query "" --image /path/to/some.jpg

    # filter by modality
    python scripts/05_query.py --query "self-attention mechanism" --modality image

Prints top-K hits with their hamming distance and a snippet of the chunk text.
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from hashmm.config import HashMMConfig
from hashmm.retrieval.hash_retriever import HashRetriever
from hashmm.utils import get_logger

logger = get_logger("scripts.05_query")
console = Console()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True, help="text query (use empty string '' for image-only)")
    ap.add_argument("--image", default=None, help="optional image path for cross-modal query")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument(
        "--modality",
        default=None,
        choices=("text", "image", "table", "chart", "equation"),
        help="restrict results to this modality",
    )
    args = ap.parse_args()

    cfg = HashMMConfig()
    retriever = HashRetriever(cfg)  # all components lazy-load

    console.print(f"[bold]Query[/bold]: {args.query!r}")
    if args.image:
        console.print(f"[bold]Image[/bold]: {args.image}")
    if args.modality:
        console.print(f"[bold]Modality filter[/bold]: {args.modality}")

    if args.image and args.query:
        results = retriever.retrieve_multimodal(
            text=args.query,
            image_path=args.image,
            top_k=args.top_k,
            modality_hint=args.modality,
        )
    else:
        results = retriever.retrieve(
            query=args.query,
            top_k=args.top_k,
            modality_hint=args.modality,
            query_image_path=args.image,
        )

    if not results:
        console.print("[red]no results[/red]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", width=3)
    table.add_column("modality", width=10)
    table.add_column("ham", width=5, justify="right")
    table.add_column("doc / page", width=20)
    table.add_column("text snippet", overflow="fold")

    for r in results:
        ham = r.meta.get("hamming_dist", "")
        doc_id = r.meta.get("doc_id", "")
        page = r.meta.get("page_idx", "")
        snippet = (r.text or "")[:120]
        if r.image_path and not r.text:
            snippet = f"[image: {r.image_path}]"
        table.add_row(
            str(r.rank),
            r.modality,
            str(ham),
            f"{doc_id[:14]}…/p{page}",
            snippet,
        )
    console.print(table)


if __name__ == "__main__":
    main()
