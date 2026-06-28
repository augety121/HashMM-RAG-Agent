#!/usr/bin/env python3
"""02 — Extract chunks and cross-modal training pairs from parsed JSONs.

Usage:
    python scripts/02_extract_pairs.py --parsed ./data/parsed \
                                       --chunks ./data/chunks.jsonl \
                                       --pairs  ./data/pairs.jsonl

Reads every *.json produced by script 01, runs the ChunkExtractor on each,
and concatenates the results into two JSONL files.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hashmm.ingestion.adapter import RAGAnythingAdapter
from hashmm.ingestion.chunk_extractor import (
    ChunkExtractor,
    write_chunks_jsonl,
    write_pairs_jsonl,
)
from hashmm.utils import get_logger

logger = get_logger("scripts.02_pairs")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parsed", required=True, type=Path)
    ap.add_argument("--chunks", required=True, type=Path)
    ap.add_argument("--pairs", required=True, type=Path)
    ap.add_argument("--neighbour-window", type=int, default=1)
    args = ap.parse_args()

    extractor = ChunkExtractor(neighbour_window=args.neighbour_window)
    all_chunks = []
    all_pairs = []

    json_files = sorted(args.parsed.glob("*.json"))
    if not json_files:
        logger.error("no parsed JSONs in %s — did script 01 succeed?", args.parsed)
        return
    logger.info("processing %d parsed files", len(json_files))

    for jf in json_files:
        try:
            result = RAGAnythingAdapter.load_parse_result(jf)
            chunks, pairs = extractor.extract(result.content_list, result.doc_id)
            all_chunks.extend(chunks)
            all_pairs.extend(pairs)
        except Exception as e:
            logger.error("failed on %s: %s", jf, e)

    write_chunks_jsonl(all_chunks, args.chunks)
    write_pairs_jsonl(all_pairs, args.pairs)

    logger.info(
        "wrote %d chunks → %s, %d pairs → %s",
        len(all_chunks), args.chunks, len(all_pairs), args.pairs,
    )
    by_mod: dict[str, int] = {}
    for c in all_chunks:
        by_mod[c.modality] = by_mod.get(c.modality, 0) + 1
    logger.info("chunk modality breakdown: %s", by_mod)
    by_source: dict[str, int] = {}
    for p in all_pairs:
        by_source[p.source] = by_source.get(p.source, 0) + 1
    logger.info("pair source breakdown: %s", by_source)


if __name__ == "__main__":
    main()
