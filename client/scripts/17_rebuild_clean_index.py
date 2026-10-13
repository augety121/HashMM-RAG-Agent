#!/usr/bin/env python3
"""17 — Rebuild a clean index by filtering out junk chunks.

Before: 19,399 chunks (56% junk) → After: ~8,500 quality chunks

Usage:
    HASH_BITS=256 python scripts/17_rebuild_clean_index.py

This script:
  1. Reads existing metadata.jsonl
  2. Filters to only high-quality text chunks
  3. Re-encodes through BGE-M3 → hash head
  4. Builds a new FAISS binary index
  5. Writes new metadata.jsonl + index

The old index is backed up, not deleted.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np
import torch

MIN_TEXT_LEN = 120
MIN_ALPHA_RATIO = 0.45
SKIP_MODALITIES = {"equation"}
# Keep image/chart/table chunks only if they have substantial text captions
VISUAL_MIN_TEXT = 80


def is_good_chunk(entry: dict) -> bool:
    """Determine if a chunk is worth indexing."""
    text = (entry.get("text") or "").strip()
    modality = entry.get("modality", "")

    # Always skip equations
    if modality in SKIP_MODALITIES:
        return False

    # For visual modalities, lower text requirement (they have image value)
    if modality in ("image", "chart", "table"):
        if len(text) < VISUAL_MIN_TEXT:
            return False
        return True

    # For text chunks: strict quality filter
    if len(text) < MIN_TEXT_LEN:
        return False

    # Skip if mostly symbols/numbers
    alpha_count = sum(1 for c in text if c.isalpha())
    if alpha_count / max(len(text), 1) < MIN_ALPHA_RATIO:
        return False

    # Skip pure headers/titles (short + few words)
    words = text.split()
    if len(words) <= 6:
        return False

    return True


def main():
    from hashmm.config import HashMMConfig
    from hashmm.hashing.encoders import TextEncoder
    from hashmm.hashing.train import load_hash_net
    from hashmm.hashing.hash_net import pack_bits

    cfg = HashMMConfig()
    index_dir = Path(cfg.hash_index_dir)
    meta_path = index_dir / "metadata.jsonl"

    print(f"Reading {meta_path}...")
    all_chunks = []
    with open(meta_path) as f:
        for line in f:
            all_chunks.append(json.loads(line))

    print(f"Total chunks: {len(all_chunks)}")

    # Filter
    good_chunks = [c for c in all_chunks if is_good_chunk(c)]
    removed = len(all_chunks) - len(good_chunks)
    print(f"After filtering: {len(good_chunks)} good chunks ({removed} removed, {removed*100//len(all_chunks)}% junk)")

    if len(good_chunks) < 100:
        print("ERROR: Too few chunks remaining. Check filter thresholds.")
        return

    # Backup old files
    old_index = cfg.hash_index_path
    old_meta = meta_path
    backup_dir = index_dir / "backup_dirty"
    backup_dir.mkdir(exist_ok=True)

    if old_index.exists():
        shutil.copy2(old_index, backup_dir / old_index.name)
        print(f"Backed up old index → {backup_dir / old_index.name}")
    shutil.copy2(old_meta, backup_dir / "metadata.jsonl")
    print(f"Backed up old metadata → {backup_dir / 'metadata.jsonl'}")

    # Load encoder + hash net
    print("Loading BGE-M3 + hash net...")
    text_enc = TextEncoder(model_name=cfg.hash_text_encoder, device=cfg.hash_device)
    hash_net, ckpt_meta = load_hash_net(cfg)
    bits = ckpt_meta["bits"]
    print(f"Hash net: {bits}-bit")

    # Encode all good chunks
    texts = [(c.get("text") or "").strip() for c in good_chunks]
    # Filter out chunks with empty text (shouldn't happen after filtering, but safety)
    valid_idx = [i for i, t in enumerate(texts) if t]
    texts = [texts[i] for i in valid_idx]
    good_chunks = [good_chunks[i] for i in valid_idx]

    print(f"Encoding {len(texts)} chunks through BGE-M3 → hash head...")
    t0 = time.time()

    BATCH = 64
    all_codes = []
    for start in range(0, len(texts), BATCH):
        batch = texts[start:start + BATCH]
        with torch.no_grad():
            embs = text_enc(batch).to(cfg.hash_device)
            codes = hash_net.sign_text(embs)
            packed = pack_bits(codes).cpu().numpy().astype(np.uint8)
            all_codes.append(packed)

        if (start // BATCH) % 10 == 0:
            print(f"  encoded {min(start + BATCH, len(texts))} / {len(texts)}")

    all_codes = np.vstack(all_codes)
    elapsed = time.time() - t0
    print(f"Encoded {len(texts)} chunks in {elapsed:.1f}s")

    # Build FAISS index
    import faiss
    index = faiss.IndexBinaryFlat(bits)
    index.add(all_codes)
    print(f"FAISS index: {index.ntotal} vectors, {bits}-bit")

    # Save
    faiss.write_index_binary(index, str(cfg.hash_index_path))
    print(f"Saved index → {cfg.hash_index_path}")

    with open(meta_path, "w") as f:
        for chunk in good_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"Saved metadata → {meta_path} ({len(good_chunks)} entries)")

    # Summary
    index_kb = cfg.hash_index_path.stat().st_size / 1024
    print(f"\n{'='*50}")
    print(f"  Before: {len(all_chunks)} chunks")
    print(f"  After:  {len(good_chunks)} chunks ({len(good_chunks)*100//len(all_chunks)}%)")
    print(f"  Removed: {removed} junk chunks")
    print(f"  Index: {index_kb:.1f} KB")
    print(f"{'='*50}")
    print(f"\nRestore old index: cp {backup_dir}/* {index_dir}/")


if __name__ == "__main__":
    main()
