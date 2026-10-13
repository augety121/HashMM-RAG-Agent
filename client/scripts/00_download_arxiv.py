#!/usr/bin/env python3
"""00 — Download a handful of arXiv PDFs to bootstrap the pipeline.

Usage:
    python scripts/00_download_arxiv.py --output ./data/pdfs --ids 2505.16133 2510.12323 2407.01449

If --ids is omitted, downloads a curated set of papers in our adjacent
research areas (RAG, multimodal hashing, late-interaction retrieval) so
you have something on day one.

arXiv allows direct PDF download; we just hit the cdn URL. No API needed.
On AutoDL with a slow link, set DOWNLOAD_PROXY or just use a HTTP proxy.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen

DEFAULT_IDS = [
    # The papers our project builds on / sits adjacent to.
    "2505.16133",  # HASH-RAG (Guo et al., ACL 2025) — our direct baseline
    "2510.12323",  # RAG-Anything (Hou et al., HKUDS) — our base framework
    "2407.01449",  # ColPali — late-interaction multimodal retrieval
    "2502.18139",  # SigLIP 2
    "2402.03216",  # BGE-M3
    "2410.05983",  # LightRAG
]


def download_one(arxiv_id: str, out_dir: Path, retries: int = 3) -> Path | None:
    """Download a single arXiv PDF. Returns the local path or None on failure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{arxiv_id}.pdf"
    if dest.exists() and dest.stat().st_size > 5000:
        print(f"[skip] {arxiv_id} already exists ({dest.stat().st_size:,} bytes)")
        return dest

    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    headers = {"User-Agent": "Mozilla/5.0 (hashmm-rag bootstrap)"}

    for attempt in range(1, retries + 1):
        try:
            print(f"[get ] {url} (attempt {attempt})")
            req = Request(url, headers=headers)
            with urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) < 5000:
                raise RuntimeError(f"suspiciously small file ({len(data)} bytes)")
            dest.write_bytes(data)
            print(f"[ok  ] {arxiv_id} → {dest} ({len(data):,} bytes)")
            return dest
        except Exception as e:
            print(f"[fail] {arxiv_id} attempt {attempt}: {e}")
            time.sleep(2 * attempt)
    print(f"[give-up] {arxiv_id}")
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--ids", nargs="*", default=DEFAULT_IDS)
    args = ap.parse_args()

    n_ok = 0
    for aid in args.ids:
        if download_one(aid, args.output) is not None:
            n_ok += 1
    print(f"\ndone. {n_ok}/{len(args.ids)} papers in {args.output}")


if __name__ == "__main__":
    main()
