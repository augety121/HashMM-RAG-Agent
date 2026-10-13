#!/usr/bin/env python3
"""04 — Build the hash index from extracted chunks.

Usage:
    python scripts/04_build_index.py --chunks ./data/chunks.jsonl

Pipeline:
    chunks.jsonl  →  per-modality encoder forward  →  hash net sign()
                  →  pack to uint8 bits  →  Faiss IndexBinaryFlat

For each chunk:
- modality == 'text' / 'equation' → text encoder + net.sign_text()
- modality == 'image' / 'table' (when image_path present) → image encoder + net.sign_image()
- modality == 'table' WITHOUT image_path → text path on the caption/body preview

The resulting index + metadata are saved to cfg.hash_index_path /
cfg.hash_metadata_path. Each metadata row contains the chunk_id, modality,
text, image_path, doc_id, page_idx — enough to render hits without going back
to chunks.jsonl.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from hashmm.config import HashMMConfig
from hashmm.hashing.hash_net import pack_bits
from hashmm.hashing.index import HashIndex
from hashmm.hashing.train import load_hash_net
from hashmm.ingestion.chunk_extractor import Chunk, read_chunks_jsonl
from hashmm.utils import get_logger

logger = get_logger("scripts.04_index")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunks", required=True, type=Path)
    ap.add_argument("--batch-size", type=int, default=64)
    args = ap.parse_args()

    cfg = HashMMConfig()
    logger.info("loading chunks: %s", args.chunks)
    all_chunks: list[Chunk] = read_chunks_jsonl(args.chunks)
    logger.info("got %d chunks", len(all_chunks))

    # Partition by encoding path.
    text_chunks: list[Chunk] = []
    image_chunks: list[Chunk] = []
    for c in all_chunks:
        # image / table / chart all have rendered images we can pass to the
        # vision encoder (when img_path is present).
        if c.modality in ("image", "table", "chart") and c.image_path:
            image_chunks.append(c)
        else:
            # text, equation, and image-less tables/charts go via text encoder
            text_chunks.append(c)

    logger.info("text path: %d, image path: %d", len(text_chunks), len(image_chunks))

    # Load network (lazy-loads its encoders too, but here we want explicit control).
    import torch  # imported here so unit tests can stub it out

    net, meta = load_hash_net(cfg)
    net.eval()  # CRITICAL: switch BatchNorm to running-stats mode
    text_encoder_name = meta.get("text_encoder", cfg.hash_text_encoder)
    image_encoder_name = meta.get("image_encoder", cfg.hash_image_encoder)

    from hashmm.hashing.encoders import ImageEncoder, TextEncoder

    text_enc = TextEncoder(text_encoder_name, device=cfg.hash_device)

    bytes_per_code = cfg.hash_bits // 8
    all_codes = np.zeros((len(all_chunks), bytes_per_code), dtype=np.uint8)
    all_meta: list[dict] = [None] * len(all_chunks)  # type: ignore

    # ── Encode text chunks ────────────────────────────────────────────
    text_idxs = [i for i, c in enumerate(all_chunks) if c not in image_chunks]
    # rebuild: we want stable order
    text_idxs = []
    image_idxs = []
    for i, c in enumerate(all_chunks):
        if c.modality in ("image", "table", "chart") and c.image_path:
            image_idxs.append(i)
        else:
            text_idxs.append(i)

    logger.info("encoding %d text-path chunks…", len(text_idxs))
    for start in range(0, len(text_idxs), args.batch_size):
        batch_idxs = text_idxs[start : start + args.batch_size]
        batch_chunks = [all_chunks[i] for i in batch_idxs]
        # Equations use their text; tables-without-image use captions/body.
        texts = [c.text or "" for c in batch_chunks]
        with torch.no_grad():
            emb = text_enc(texts)
            codes = net.sign_text(emb)
        packed = pack_bits(codes).cpu().numpy()
        for k, i in enumerate(batch_idxs):
            all_codes[i] = packed[k]
            all_meta[i] = _chunk_to_meta(all_chunks[i])

    # ── Encode image chunks ───────────────────────────────────────────
    if image_idxs:
        image_enc = ImageEncoder(image_encoder_name, device=cfg.hash_device)
        logger.info("encoding %d image-path chunks…", len(image_idxs))
        for start in range(0, len(image_idxs), args.batch_size):
            batch_idxs = image_idxs[start : start + args.batch_size]
            batch_chunks = [all_chunks[i] for i in batch_idxs]
            img_paths = [c.image_path for c in batch_chunks]
            # Robust: skip individual images that fail to open
            try:
                with torch.no_grad():
                    emb = image_enc(img_paths)
                    codes = net.sign_image(emb)
                packed = pack_bits(codes).cpu().numpy()
            except (FileNotFoundError, OSError) as e:
                logger.warning("image batch failed: %s — falling back to per-image", e)
                packed = np.zeros((len(batch_chunks), bytes_per_code), dtype=np.uint8)
                from PIL import Image

                for k, ip in enumerate(img_paths):
                    try:
                        Image.open(ip).convert("RGB").close()
                        with torch.no_grad():
                            emb = image_enc([ip])
                            c = net.sign_image(emb)
                        packed[k] = pack_bits(c).cpu().numpy()[0]
                    except Exception as ee:
                        logger.warning("skipping bad image %s: %s", ip, ee)

            for k, i in enumerate(batch_idxs):
                all_codes[i] = packed[k]
                all_meta[i] = _chunk_to_meta(all_chunks[i])

    # ── Save ──────────────────────────────────────────────────────────
    idx = HashIndex(bits=cfg.hash_bits)
    idx.add(all_codes, all_meta)
    idx.save(cfg.hash_index_path, cfg.hash_metadata_path)
    logger.info("✓ %s", idx.size_summary())


def _chunk_to_meta(c: Chunk) -> dict:
    return {
        "chunk_id": c.chunk_id,
        "modality": c.modality,
        "text": c.text,
        "image_path": c.image_path,
        "doc_id": c.doc_id,
        "page_idx": c.page_idx,
        "meta": c.meta,
    }


if __name__ == "__main__":
    main()
