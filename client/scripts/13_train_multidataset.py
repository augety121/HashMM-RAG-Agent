#!/usr/bin/env python3
"""13 — Train hash heads on multi-dataset embeddings (from 12_encode_datasets.py).

Two-phase training:
  Phase 1: Pre-train on general data (COCO/Flickr/NUS-WIDE) — many pairs, broad alignment
  Phase 2: Fine-tune on ViDoRe domain (10_finetune_vidore.py) — task-specific Pearson-r

Usage:
    python scripts/13_train_multidataset.py --datasets coco
    python scripts/13_train_multidataset.py --datasets coco flickr25k
    python scripts/13_train_multidataset.py --datasets coco --epochs 50 --lr 5e-4
"""
from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

import numpy as np
import torch
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR

from hashmm.config import HashMMConfig
from hashmm.hashing.hash_net import CrossModalHashNet
from hashmm.hashing.losses import HashLoss
from hashmm.utils import get_logger

logger = get_logger("scripts.13_train")

CACHE_ROOT = "/root/autodl-tmp/hashmm/multidataset_cache"


def load_cache(cache_dir: str, ds: str) -> dict:
    path = os.path.join(cache_dir, f"{ds}_bgem3_siglip2.npz")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Run: python scripts/12_encode_datasets.py --dataset {ds}")
    d = np.load(path, allow_pickle=True)
    print(f"  Loaded {ds}: text={d['text_embs'].shape} image={d['image_embs'].shape} "
          f"labels={d['labels'].shape}")
    return {k: d[k] for k in d.files}


def build_sim_from_labels(labels: np.ndarray, device: str) -> torch.Tensor:
    """(N, N) similarity: +1 if share ≥1 label, -1 otherwise."""
    L = torch.from_numpy(labels).to(device)
    overlap = L @ L.T  # (N, N)
    sim = (overlap > 0).float() * 2 - 1  # +1/-1
    return sim


def train_epoch(net, loss_fn, text_t, image_t, labels_np,
                optimizer, batch_size, tau, device):
    net.train()
    N = text_t.shape[0]
    perm = np.random.permutation(N)
    total_loss = 0.0
    n_batches = 0

    for start in range(0, N, batch_size):
        idx = perm[start:start + batch_size]
        if len(idx) < 8:
            continue

        t = text_t[idx].to(device)
        im = image_t[idx].to(device)

        # Build batch-level similarity from labels
        batch_labels = labels_np[idx]
        sim = build_sim_from_labels(batch_labels, device)

        b_text, b_image = net(t, im, tau=tau)
        losses = loss_fn(b_text, b_image, sim=sim)

        optimizer.zero_grad()
        losses["loss"].backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
        optimizer.step()

        total_loss += losses["loss"].item()
        n_batches += 1

    return total_loss / max(n_batches, 1)


@torch.no_grad()
def eval_map(net, text_t, image_t, labels_np, device, n_eval=1000):
    """Quick mAP@50 on a random subset."""
    net.eval()
    N = min(text_t.shape[0], n_eval)
    idx = np.random.choice(text_t.shape[0], N, replace=False)

    t_codes = net.sign_text(text_t[idx].to(device)).cpu()
    i_codes = net.sign_image(image_t[idx].to(device)).cpu()

    # Hamming similarity via inner product of ±1 codes
    sim_hash = t_codes @ i_codes.T  # higher = closer

    # Ground truth
    sub_labels = labels_np[idx]
    gt = (sub_labels @ sub_labels.T) > 0

    # mAP for t2i
    aps = []
    for i in range(N):
        scores = sim_hash[i].numpy()
        truth = gt[i]
        if truth.sum() == 0:
            continue
        ranked = np.argsort(-scores)
        hits = truth[ranked].astype(float)
        prec_at_k = np.cumsum(hits) / np.arange(1, N + 1)
        ap = (prec_at_k * hits).sum() / max(hits.sum(), 1)
        aps.append(ap)

    # Unique code count
    binary = (t_codes > 0).byte()
    n_unique = len(set(tuple(row.tolist()) for row in binary[:500]))

    return {
        "mAP_t2i": np.mean(aps) if aps else 0.0,
        "unique_codes": n_unique,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--datasets", nargs="+", default=["coco"])
    ap.add_argument("--cache-dir", default=CACHE_ROOT)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=128)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    cfg = HashMMConfig()
    device = cfg.hash_device
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    # ── Load data ────────────────────────────────────────────────────
    print("Loading datasets...")
    all_text, all_image, all_labels = [], [], []
    label_offset = 0

    for ds in args.datasets:
        cache = load_cache(args.cache_dir, ds)
        all_text.append(cache["text_embs"])
        all_image.append(cache["image_embs"])
        # Offset labels so COCO's class 0 ≠ Flickr's class 0
        lab = cache["labels"]
        padded = np.zeros((lab.shape[0], label_offset + lab.shape[1]), dtype=np.float32)
        padded[:, label_offset:label_offset + lab.shape[1]] = lab
        all_labels.append(padded)
        label_offset += lab.shape[1]

    # Pad all to same width
    max_C = max(l.shape[1] for l in all_labels)
    for i in range(len(all_labels)):
        if all_labels[i].shape[1] < max_C:
            all_labels[i] = np.hstack([
                all_labels[i],
                np.zeros((all_labels[i].shape[0], max_C - all_labels[i].shape[1]),
                         dtype=np.float32)
            ])

    text_embs = np.vstack(all_text)
    image_embs = np.vstack(all_image)
    labels = np.vstack(all_labels)
    text_t = torch.from_numpy(text_embs)
    image_t = torch.from_numpy(image_embs)

    print(f"\nMerged: {text_embs.shape[0]} samples, "
          f"text={text_embs.shape[1]}d, image={image_embs.shape[1]}d, "
          f"labels={labels.shape[1]} classes")

    # ── Build model ──────────────────────────────────────────────────
    net = CrossModalHashNet(
        text_in_dim=text_embs.shape[1],     # 1024 (BGE-M3)
        image_in_dim=image_embs.shape[1],   # 768 (SigLIP-2)
        hidden_dim=cfg.hash_proj_hidden,     # 2048
        bits=cfg.hash_bits,                  # 128
    ).to(device)

    loss_fn = HashLoss(
        bits=cfg.hash_bits,
        w_quant=cfg.hash_loss_w_quant,
        w_balance=cfg.hash_loss_w_balance,
    )

    optimizer = AdamW(net.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = CosineAnnealingLR(optimizer, T_max=args.epochs)

    print(f"\nTraining: {args.epochs} epochs, batch={args.batch_size}, "
          f"lr={args.lr}, bits={cfg.hash_bits}")
    print(f"{'─'*60}")

    # ── Train ────────────────────────────────────────────────────────
    tau_start, tau_end = 1.0, 5.0
    best_map = 0.0
    best_state = None
    t_start = time.time()

    for epoch in range(1, args.epochs + 1):
        tau = tau_start + (tau_end - tau_start) * (epoch / args.epochs)
        loss = train_epoch(net, loss_fn, text_t, image_t, labels,
                           optimizer, args.batch_size, tau, device)
        scheduler.step()

        if epoch % 5 == 0 or epoch <= 3 or epoch == args.epochs:
            ev = eval_map(net, text_t, image_t, labels, device)
            tag = ""
            if ev["mAP_t2i"] > best_map:
                best_map = ev["mAP_t2i"]
                best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
                tag = " ★"
            print(f"  epoch {epoch:3d}/{args.epochs}  loss={loss:.4f}  "
                  f"mAP_t2i={ev['mAP_t2i']:.4f}  "
                  f"unique={ev['unique_codes']}/500  "
                  f"tau={tau:.1f}  lr={scheduler.get_last_lr()[0]:.1e}{tag}")

    train_time = time.time() - t_start
    print(f"\n{'─'*60}")
    print(f"Training done: {train_time:.0f}s, best mAP_t2i = {best_map:.4f}")

    # Restore best
    if best_state:
        net.load_state_dict(best_state)
        print(f"Restored best-epoch weights")

    # ── Save ─────────────────────────────────────────────────────────
    ckpt_path = cfg.hash_net_ckpt
    if ckpt_path.exists():
        backup = ckpt_path.with_suffix(".pt.pre_multidataset")
        if not backup.exists():
            shutil.copy(ckpt_path, backup)
            print(f"Backed up → {backup}")

    ckpt = {
        "text_in_dim": text_embs.shape[1],
        "image_in_dim": image_embs.shape[1],
        "hidden_dim": cfg.hash_proj_hidden,
        "bits": cfg.hash_bits,
        "state_dict": net.state_dict(),
        "pretrain_datasets": args.datasets,
        "pretrain_samples": text_embs.shape[0],
        "pretrain_map": best_map,
        "pretrain_time": train_time,
    }
    torch.save(ckpt, ckpt_path)
    print(f"✓ Saved → {ckpt_path}")

    print(f"""
Next steps:
  python scripts/04_build_index.py
  python scripts/09_run_vidore.py --dataset biomedical_lectures_eng_v2 --retriever both
  python scripts/10_finetune_vidore.py --dataset biomedical_lectures_eng_v2
  python scripts/09_run_vidore.py --dataset biomedical_lectures_eng_v2 --retriever both
""")


if __name__ == "__main__":
    main()
