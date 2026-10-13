#!/usr/bin/env python3
"""12 — Encode PAMIH datasets with BGE-M3 + SigLIP-2 for HashMM-RAG training.

Your mat files contain RAW DATA (captions, image paths, labels), not features.
This script loads them, encodes with HashMM-RAG's encoders, and caches the
results so training is instant on repeat runs.

Confirmed mat layout (from 11_inspect_datasets.py):
  caption.mat  → 'caption': actual text strings
  index.mat    → 'index':   image file paths/names
  label.mat    → 'category': (N, C) binary label matrix

Datasets available:
  COCO:      122218 samples, 80 classes, raw images at /root/autodl-tmp/database/coco/
  Flickr25K: 24581 samples,  24 classes
  NUS-WIDE:  192779 samples, 21 classes, captions in caption.txt
  IAPRTC12:  19626 samples,  291 classes

Usage:
    # COCO (has raw images — full pipeline)
    python scripts/12_encode_datasets.py --dataset coco --max-samples 10000

    # Check what's cached
    python scripts/12_encode_datasets.py --dataset coco --check-only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

# ── Dataset root paths (AutoDL layout) ───────────────────────────────

DATASET_ROOT = "/root/autodl-tmp/dataset"      # mat files
DATABASE_ROOT = "/root/autodl-tmp/database"     # raw images
CACHE_ROOT = "/root/autodl-tmp/hashmm/multidataset_cache"

DATASET_CONFIG = {
    "coco": {
        "mat_dir": f"{DATASET_ROOT}/coco",
        "image_dir": f"{DATABASE_ROOT}/coco",
        "n_classes": 80,
    },
    "flickr25k": {
        "mat_dir": f"{DATASET_ROOT}/flickr25k",
        "image_dir": f"{DATABASE_ROOT}/flickr25k",  # may not exist
        "n_classes": 24,
    },
    "nuswide": {
        "mat_dir": f"{DATASET_ROOT}/nuswide",
        "image_dir": f"{DATABASE_ROOT}/nuswide",
        "n_classes": 21,
    },
    "iaprtc12": {
        "mat_dir": f"{DATASET_ROOT}/iaprtc12",
        "image_dir": f"{DATABASE_ROOT}/iaprtc12",  # may not exist
        "n_classes": 291,
    },
}


# ── Mat file loaders ─────────────────────────────────────────────────


def load_captions(mat_dir: str, dataset: str) -> list[str]:
    """Load caption strings from caption.mat or caption.txt."""
    import scipy.io as sio

    # NUS-WIDE uses a .txt file
    txt_path = os.path.join(mat_dir, "caption.txt")
    if dataset == "nuswide" and os.path.exists(txt_path):
        print(f"  Loading captions from {txt_path}")
        with open(txt_path, encoding="utf-8", errors="replace") as f:
            captions = [line.strip() for line in f]
        print(f"  → {len(captions)} captions")
        return captions

    mat_path = os.path.join(mat_dir, "caption.mat")
    if not os.path.exists(mat_path):
        print(f"  WARNING: {mat_path} not found")
        return []

    print(f"  Loading captions from {mat_path}")
    data = sio.loadmat(mat_path)
    raw = data["caption"]

    # Handle different mat shapes:
    # COCO: shape=(1, 122218) dtype=object — cell array, each element is a string
    # Flickr25K: shape=(24581, 1) dtype=<U1559 — direct string array
    # IAPRTC12: shape=(19626, 1) dtype=<U414
    captions = []
    if raw.dtype == object:
        # Cell array — need to extract strings from nested arrays
        flat = raw.flatten()
        for item in flat:
            if isinstance(item, np.ndarray):
                # Could be a nested array containing the string
                s = str(item.flatten()[0]) if item.size > 0 else ""
            else:
                s = str(item)
            captions.append(s.strip())
    else:
        # Direct string array
        flat = raw.flatten()
        captions = [str(s).strip() for s in flat]

    print(f"  → {len(captions)} captions")
    # Show samples
    for i in [0, 1, len(captions) // 2]:
        if i < len(captions):
            preview = captions[i][:100] + ("..." if len(captions[i]) > 100 else "")
            print(f"    [{i}] {preview}")
    return captions


def load_image_paths(mat_dir: str, image_dir: str) -> list[str]:
    """Load image paths from index.mat → resolve to actual files."""
    import scipy.io as sio

    mat_path = os.path.join(mat_dir, "index.mat")
    if not os.path.exists(mat_path):
        print(f"  WARNING: {mat_path} not found")
        return []

    print(f"  Loading image index from {mat_path}")
    data = sio.loadmat(mat_path)
    raw_paths = data["index"].flatten()
    print(f"  → {len(raw_paths)} entries")

    # Show samples to understand the path format
    for i in [0, 1, 2]:
        if i < len(raw_paths):
            print(f"    [{i}] {raw_paths[i]}")

    # Resolve to actual file paths
    resolved = []
    n_found = 0
    n_missing = 0
    for rel_path in raw_paths:
        rel_path = str(rel_path).strip()
        # Try multiple resolution strategies
        candidates = [
            os.path.join(image_dir, rel_path),
            os.path.join(image_dir, os.path.basename(rel_path)),
            # COCO: images might be in train2017/ or val2017/ subdirs
            os.path.join(image_dir, "train2017", os.path.basename(rel_path)),
            os.path.join(image_dir, "val2017", os.path.basename(rel_path)),
            rel_path,  # absolute path
        ]
        found = None
        for c in candidates:
            if os.path.exists(c):
                found = c
                break
        if found:
            resolved.append(found)
            n_found += 1
        else:
            resolved.append(None)
            n_missing += 1

    print(f"  → resolved: {n_found} found, {n_missing} missing")
    if n_missing > 0 and n_missing < 5:
        # Show what's missing
        for i, (rp, fp) in enumerate(zip(raw_paths, resolved)):
            if fp is None and i < 5:
                print(f"    MISSING: {rp}")
    return resolved


def load_labels(mat_dir: str) -> np.ndarray:
    """Load (N, C) binary label matrix from label.mat."""
    import scipy.io as sio

    mat_path = os.path.join(mat_dir, "label.mat")
    if not os.path.exists(mat_path):
        raise FileNotFoundError(f"{mat_path} not found")

    print(f"  Loading labels from {mat_path}")
    data = sio.loadmat(mat_path)
    labels = data["category"].astype(np.float32)
    print(f"  → labels: {labels.shape}, {labels.sum(axis=1).mean():.1f} avg labels/sample")
    return labels


# ── Encoders ─────────────────────────────────────────────────────────


def encode_texts_bgem3(texts: list[str], batch_size: int = 32,
                        device: str = "cuda") -> np.ndarray:
    """Encode texts with BGE-M3 → (N, 1024) float32."""
    from hashmm.hashing.encoders import TextEncoder
    from hashmm.config import HashMMConfig
    import torch

    cfg = HashMMConfig()
    enc = TextEncoder(model_name=cfg.hash_text_encoder, device=device)

    all_embs = []
    t0 = time.time()
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        with torch.no_grad():
            emb = enc(batch).cpu().numpy().astype(np.float32)
        all_embs.append(emb)
        if i > 0 and i % (batch_size * 50) == 0:
            elapsed = time.time() - t0
            speed = i / elapsed
            eta = (len(texts) - i) / speed
            print(f"    BGE-M3: {i}/{len(texts)} ({speed:.0f} samples/s, ETA {eta:.0f}s)")

    result = np.vstack(all_embs)
    result /= (np.linalg.norm(result, axis=1, keepdims=True) + 1e-9)

    del enc
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    print(f"    BGE-M3 done: {result.shape} in {time.time()-t0:.0f}s")
    return result


def encode_images_siglip(image_paths: list[str], batch_size: int = 16,
                          device: str = "cuda") -> np.ndarray:
    """Encode images with SigLIP-2 → (N, 768) float32."""
    from hashmm.hashing.encoders import ImageEncoder
    from hashmm.config import HashMMConfig
    import torch

    cfg = HashMMConfig()
    enc = ImageEncoder(model_name=cfg.hash_image_encoder, device=device)

    all_embs = []
    t0 = time.time()
    for i in range(0, len(image_paths), batch_size):
        batch = image_paths[i:i + batch_size]
        with torch.no_grad():
            emb = enc(batch).cpu().numpy().astype(np.float32)
        all_embs.append(emb)
        if i > 0 and i % (batch_size * 30) == 0:
            elapsed = time.time() - t0
            speed = i / elapsed
            eta = (len(image_paths) - i) / speed
            print(f"    SigLIP-2: {i}/{len(image_paths)} ({speed:.0f} samples/s, ETA {eta:.0f}s)")

    result = np.vstack(all_embs)
    result /= (np.linalg.norm(result, axis=1, keepdims=True) + 1e-9)

    del enc
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    print(f"    SigLIP-2 done: {result.shape} in {time.time()-t0:.0f}s")
    return result


# ── Main ─────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dataset", required=True,
                    choices=list(DATASET_CONFIG.keys()))
    ap.add_argument("--max-samples", type=int, default=10000,
                    help="Max samples to encode. PAMIH used 10000 for COCO.")
    ap.add_argument("--batch-text", type=int, default=32)
    ap.add_argument("--batch-image", type=int, default=16)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--check-only", action="store_true",
                    help="Just check if cache exists, don't encode")
    ap.add_argument("--cache-dir", default=CACHE_ROOT)
    args = ap.parse_args()

    ds = args.dataset
    cfg = DATASET_CONFIG[ds]
    os.makedirs(args.cache_dir, exist_ok=True)
    cache_path = os.path.join(args.cache_dir, f"{ds}_bgem3_siglip2.npz")

    # Check existing cache
    if os.path.exists(cache_path):
        d = np.load(cache_path, allow_pickle=True)
        print(f"Cache exists: {cache_path}")
        print(f"  text_embs:  {d['text_embs'].shape}")
        print(f"  image_embs: {d['image_embs'].shape}")
        print(f"  labels:     {d['labels'].shape}")
        if args.check_only:
            return
        print("  (delete to re-encode)")
        return

    if args.check_only:
        print(f"No cache at {cache_path}")
        return

    print(f"\n{'='*60}")
    print(f"Encoding {ds} with BGE-M3 + SigLIP-2")
    print(f"{'='*60}\n")

    # ── Load raw data from mat ───────────────────────────────────────
    mat_dir = cfg["mat_dir"]
    image_dir = cfg["image_dir"]

    captions = load_captions(mat_dir, ds)
    image_paths = load_image_paths(mat_dir, image_dir)
    labels = load_labels(mat_dir)

    # Sanity checks
    N = labels.shape[0]
    assert len(captions) == N or len(captions) == 0, \
        f"Caption count {len(captions)} != label count {N}"
    assert len(image_paths) == N, \
        f"Image path count {len(image_paths)} != label count {N}"

    # ── Filter valid samples (have both caption and image) ───────────
    valid_indices = []
    for i in range(N):
        has_caption = i < len(captions) and captions[i].strip()
        has_image = image_paths[i] is not None
        if has_caption and has_image:
            valid_indices.append(i)

    print(f"\n  Valid samples (caption + image): {len(valid_indices)} / {N}")

    if len(valid_indices) == 0:
        print("ERROR: No valid samples found!")
        if not any(p is not None for p in image_paths):
            print("  → No images resolved. Check if raw images exist at:")
            print(f"    {image_dir}")
            print("  The index.mat paths might need a different base directory.")
            # Show what index.mat contains for debugging
            print("\n  First 3 index entries:")
            import scipy.io as sio
            data = sio.loadmat(os.path.join(mat_dir, "index.mat"))
            for i, p in enumerate(data["index"].flatten()[:3]):
                print(f"    {p}")
        sys.exit(1)

    # ── Subsample ────────────────────────────────────────────────────
    if len(valid_indices) > args.max_samples:
        rng = np.random.default_rng(42)
        valid_indices = sorted(rng.choice(valid_indices, args.max_samples, replace=False))
    print(f"  Using {len(valid_indices)} samples (max={args.max_samples})")

    sub_captions = [captions[i] for i in valid_indices]
    sub_images = [image_paths[i] for i in valid_indices]
    sub_labels = labels[valid_indices]

    # ── Encode ───────────────────────────────────────────────────────
    print(f"\n  Encoding {len(sub_captions)} captions with BGE-M3...")
    text_embs = encode_texts_bgem3(sub_captions, args.batch_text, args.device)

    print(f"\n  Encoding {len(sub_images)} images with SigLIP-2...")
    image_embs = encode_images_siglip(sub_images, args.batch_image, args.device)

    # ── Save ─────────────────────────────────────────────────────────
    np.savez(
        cache_path,
        text_embs=text_embs,
        image_embs=image_embs,
        labels=sub_labels,
    )
    print(f"\n✓ Saved → {cache_path}")
    print(f"  text_embs:  {text_embs.shape}  (BGE-M3, 1024-d)")
    print(f"  image_embs: {image_embs.shape}  (SigLIP-2, 768-d)")
    print(f"  labels:     {sub_labels.shape}  ({cfg['n_classes']} classes)")
    print(f"\nNext: python scripts/13_train_multidataset.py --datasets {ds}")


if __name__ == "__main__":
    main()
