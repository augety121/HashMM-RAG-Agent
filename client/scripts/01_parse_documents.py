#!/usr/bin/env python3
"""01 — Parse documents through RAG-Anything's MinerU pipeline.

Usage:
    python scripts/01_parse_documents.py --input ./data/pdfs --output ./data/parsed

For each PDF/document in --input, runs RAG-Anything's parser (no LightRAG
insertion — just parsing) and dumps a JSON file with content_list to --output.

The parse step is cached by RAG-Anything internally, so re-runs are fast.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Iterable

from hashmm.config import HashMMConfig
from hashmm.ingestion.adapter import RAGAnythingAdapter
from hashmm.utils import get_logger

logger = get_logger("scripts.01_parse")

SUPPORTED_EXTS = {".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg", ".md", ".txt"}


def iter_documents(input_dir: Path) -> Iterable[Path]:
    """Yield paths to all supported documents under input_dir (recursive)."""
    for p in sorted(input_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            yield p


async def main_async(input_dir: Path, output_dir: Path, cfg: HashMMConfig,
                     resume: bool = True) -> None:
    adapter = RAGAnythingAdapter(cfg)  # parse-only mode, no LLM/embed funcs needed
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resume support: sidecar log of PDF filenames already processed.
    # Format: one filename (basename) per line.
    processed_log = output_dir / ".processed_files.txt"
    already_done: set[str] = set()
    if resume and processed_log.exists():
        already_done = {
            line.strip() for line in processed_log.read_text().splitlines()
            if line.strip()
        }
        logger.info("resume: %d files already processed (skip)",
                    len(already_done))

    files = list(iter_documents(input_dir))
    if not files:
        logger.error("no supported documents found under %s", input_dir)
        return

    pending = [p for p in files if p.name not in already_done]
    logger.info("found %d documents (%d new, %d skipped)",
                len(files), len(pending), len(files) - len(pending))

    n_ok = 0
    n_fail = 0
    for i, path in enumerate(pending, 1):
        try:
            logger.info("[%d/%d] parsing %s", i, len(pending), path.name)
            result = await adapter.aparse_only(path)
            adapter.save_parse_result(result, output_dir)
            n_ok += 1
            # Append to resume log AFTER successful save (crash-safe)
            with processed_log.open("a", encoding="utf-8") as f:
                f.write(path.name + "\n")
        except Exception as e:
            logger.error("failed on %s: %s", path, e)
            n_fail += 1
    logger.info("done. %d ok, %d failed (+%d previously done) → %s",
                n_ok, n_fail, len(already_done), output_dir)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path, help="dir of PDFs / docs")
    ap.add_argument("--output", required=True, type=Path, help="dir for parsed JSONs")
    ap.add_argument("--no-resume", action="store_true",
                    help="re-parse everything, ignoring .processed_files.txt")
    args = ap.parse_args()

    cfg = HashMMConfig()
    asyncio.run(main_async(args.input, args.output, cfg,
                           resume=not args.no_resume))


if __name__ == "__main__":
    main()
