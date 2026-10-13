"""Train the cross-modal hash net on extracted pairs.

Usage from CLI: see `scripts/03_train_hash_net.py`.
Programmatic:

    from hashmm.config import HashMMConfig
    from hashmm.hashing.train import train_hash_net
    cfg = HashMMConfig()
    train_hash_net(cfg, pairs_jsonl="data/pairs.jsonl")

Output: checkpoints/hash_net.pt with state_dict + meta (text_dim, image_dim, bits).
"""

from __future__ import annotations

import time
from pathlib import Path

import torch
from torch.optim import AdamW
from torch.utils.data import DataLoader

from hashmm.config import HashMMConfig
from hashmm.hashing.dataset import (
    CrossModalPairsDataset,
    PairBatch,
    pair_collate,
    split_pairs,
)
from hashmm.hashing.encoders import ImageEncoder, TextEncoder
from hashmm.hashing.hash_net import CrossModalHashNet
from hashmm.hashing.losses import HashLoss, build_similarity_matrix
from hashmm.utils import get_logger

logger = get_logger("hashmm.hashing.train")


def _anneal_tau(epoch: int, total_epochs: int, start: float, end: float) -> float:
    """Linear annealing of tanh temperature from `start` to `end`."""
    if total_epochs <= 1:
        return end
    frac = epoch / (total_epochs - 1)
    return start + frac * (end - start)


@torch.no_grad()
def _validate(
    net: CrossModalHashNet,
    val_pairs,
    text_enc: TextEncoder,
    image_enc: ImageEncoder,
    batch_size: int = 64,
) -> dict:
    """Compute val-set t2i and i2t mean Average Precision @ k (k=10) using sign codes."""
    if not val_pairs:
        return {"val_map_t2i": float("nan"), "val_map_i2t": float("nan"), "n": 0}

    net.eval()
    # Encode all val items.
    all_text = [p.text for p in val_pairs]
    all_img = [p.image_path for p in val_pairs]
    text_codes = []
    img_codes = []
    for i in range(0, len(val_pairs), batch_size):
        te = text_enc(all_text[i : i + batch_size])
        ie = image_enc(all_img[i : i + batch_size])
        text_codes.append(net.sign_text(te))
        img_codes.append(net.sign_image(ie))
    text_codes = torch.cat(text_codes, dim=0)
    img_codes = torch.cat(img_codes, dim=0)
    # Hamming distance via 0.5 * (K - <b_t, b_i>); equivalent ranking → use negative inner product.
    sim_t2i = text_codes @ img_codes.t()  # larger = closer
    sim_i2t = img_codes @ text_codes.t()
    truth_idx = torch.arange(len(val_pairs), device=text_codes.device)

    def map_at_k(sim_matrix, k: int = 10) -> float:
        # rank: for each row, where does the diagonal land?
        ranks = (-sim_matrix).argsort(dim=1)
        # find position of diagonal index
        pos = (ranks == truth_idx.unsqueeze(1)).float().argmax(dim=1)
        hits = (pos < k).float()
        # MAP@k with single relevant = mean of 1/(rank+1) when hit, else 0.
        ap = torch.where(hits.bool(), 1.0 / (pos.float() + 1.0), torch.zeros_like(pos, dtype=torch.float))
        return ap.mean().item()

    out = {
        "val_map_t2i": map_at_k(sim_t2i, k=10),
        "val_map_i2t": map_at_k(sim_i2t, k=10),
        "n": len(val_pairs),
    }
    net.train()
    return out


def train_hash_net(
    cfg: HashMMConfig,
    pairs_jsonl: str | Path,
    val_fraction: float = 0.05,
    use_doc_positives: bool = False,
) -> Path:
    """End-to-end training. Saves checkpoint to cfg.hash_net_ckpt and returns the path.

    Args:
        use_doc_positives: if True, items from the same document (within
            page_window=1) count as positives in addition to the diagonal.
            This expands the supervision signal when you have many short docs
            but is dangerous on tiny datasets (2-5 docs): batches become
            mostly-positive, the network learns to output 'similar' for
            everything, and bit balance collapses. Recommend False until
            you have 20+ docs.
    """
    device = cfg.hash_device

    # ── Data ──────────────────────────────────────────────────────────
    ds = CrossModalPairsDataset(pairs_jsonl)
    train_pairs, val_pairs = split_pairs(ds.pairs, val_fraction=val_fraction)
    logger.info(
        "loaded %d total pairs → %d train / %d val (split by doc_id)",
        len(ds), len(train_pairs), len(val_pairs),
    )

    train_ds = CrossModalPairsDataset.__new__(CrossModalPairsDataset)
    train_ds.pairs = train_pairs  # bypass JSONL reload
    loader = DataLoader(
        train_ds,
        batch_size=cfg.hash_batch_size,
        shuffle=True,
        collate_fn=pair_collate,
        num_workers=0,  # encoders are on GPU; multiproc workers don't help here
        drop_last=True,
    )

    # ── Models ────────────────────────────────────────────────────────
    text_enc = TextEncoder(cfg.hash_text_encoder, device=device)
    image_enc = ImageEncoder(cfg.hash_image_encoder, device=device)
    net = CrossModalHashNet(
        text_in_dim=text_enc.out_dim,
        image_in_dim=image_enc.out_dim,
        hidden_dim=cfg.hash_proj_hidden,
        bits=cfg.hash_bits,
    ).to(device)
    loss_fn = HashLoss(
        bits=cfg.hash_bits,
        w_quant=cfg.hash_loss_w_quant,
        w_balance=cfg.hash_loss_w_balance,
    )
    optim = AdamW(net.parameters(), lr=cfg.hash_lr, weight_decay=1e-4)

    # ── Loop ──────────────────────────────────────────────────────────
    net.train()
    best_val = -1.0
    epochs_no_improve = 0
    early_stop_patience = max(3, cfg.hash_epochs // 4)  # stop if no mAP improvement
    for epoch in range(cfg.hash_epochs):
        tau = _anneal_tau(
            epoch, cfg.hash_epochs, cfg.hash_tanh_temp_start, cfg.hash_tanh_temp_end
        )
        t0 = time.time()
        accum = {"loss": 0.0, "loss_pair": 0.0, "loss_quant": 0.0, "loss_balance": 0.0}
        n_batches = 0
        for batch in loader:
            batch: PairBatch
            text_emb = text_enc(batch.texts)
            try:
                image_emb = image_enc(batch.image_paths)
            except (FileNotFoundError, OSError) as e:
                logger.warning("skipping batch with bad image: %s", e)
                continue

            b_text, b_image = net(text_emb, image_emb, tau=tau)

            if use_doc_positives:
                sim = build_similarity_matrix(
                    batch.doc_ids, batch.page_idxs, page_window=1, device=device
                )
            else:
                sim = None

            losses = loss_fn(b_text, b_image, sim=sim)
            optim.zero_grad()
            losses["loss"].backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), max_norm=1.0)
            optim.step()

            for k in accum:
                accum[k] += float(losses[k].item())
            n_batches += 1

        n_batches = max(n_batches, 1)
        avg = {k: v / n_batches for k, v in accum.items()}

        val_metrics = _validate(net, val_pairs, text_enc, image_enc, batch_size=cfg.hash_batch_size)

        # Code-diversity diagnostic — how many UNIQUE binary codes does the
        # val set produce? If this collapses to 1 or 2, the network has
        # learned to output the same code for everything; retrieval is dead.
        n_unique = _count_unique_codes(net, val_pairs, text_enc, image_enc, batch_size=cfg.hash_batch_size)

        elapsed = time.time() - t0
        logger.info(
            "epoch %d/%d  τ=%.2f  loss=%.4f (pair=%.4f q=%.4f bal=%.4f)  "
            "mAP@10 t2i=%.3f i2t=%.3f  unique_codes=%d/%d  [%.1fs]",
            epoch + 1, cfg.hash_epochs, tau,
            avg["loss"], avg["loss_pair"], avg["loss_quant"], avg["loss_balance"],
            val_metrics["val_map_t2i"], val_metrics["val_map_i2t"],
            n_unique, 2 * len(val_pairs),  # text+image codes for each val pair
            elapsed,
        )

        # Save best by avg mAP; early-stop if no improvement.
        score = (val_metrics["val_map_t2i"] + val_metrics["val_map_i2t"]) / 2.0
        if score > best_val + 1e-4:  # require small improvement to count
            best_val = score
            epochs_no_improve = 0
            _save_checkpoint(cfg, net, text_enc, image_enc, epoch, val_metrics)
        else:
            epochs_no_improve += 1
            if epochs_no_improve >= early_stop_patience:
                logger.info(
                    "early stopping at epoch %d — no mAP improvement for %d epochs",
                    epoch + 1, early_stop_patience,
                )
                break

    logger.info("training complete. best val avg mAP@10 = %.3f", best_val)
    return cfg.hash_net_ckpt


@torch.no_grad()
def _count_unique_codes(
    net: CrossModalHashNet,
    pairs,
    text_enc: TextEncoder,
    image_enc: ImageEncoder,
    batch_size: int = 64,
) -> int:
    """Diagnostic: count unique binary codes across all (text, image) sides
    of the val set. If this is small (1-2), codes have collapsed."""
    if not pairs:
        return 0
    net.eval()
    texts = [p.text for p in pairs]
    imgs = [p.image_path for p in pairs]
    all_codes = set()
    for i in range(0, len(pairs), batch_size):
        try:
            te = text_enc(texts[i : i + batch_size])
            ie = image_enc(imgs[i : i + batch_size])
        except (FileNotFoundError, OSError):
            continue
        t_sign = net.sign_text(te).cpu().numpy()
        i_sign = net.sign_image(ie).cpu().numpy()
        for row in t_sign:
            all_codes.add(row.tobytes())
        for row in i_sign:
            all_codes.add(row.tobytes())
    net.train()
    return len(all_codes)


def _save_checkpoint(
    cfg: HashMMConfig,
    net: CrossModalHashNet,
    text_enc: TextEncoder,
    image_enc: ImageEncoder,
    epoch: int,
    val_metrics: dict,
) -> None:
    ckpt = cfg.hash_net_ckpt
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": net.state_dict(),
            "bits": cfg.hash_bits,
            "hidden_dim": cfg.hash_proj_hidden,
            "text_in_dim": text_enc.out_dim,
            "image_in_dim": image_enc.out_dim,
            "text_encoder": cfg.hash_text_encoder,
            "image_encoder": cfg.hash_image_encoder,
            "epoch": epoch,
            "val_metrics": val_metrics,
        },
        ckpt,
    )
    logger.info("saved best checkpoint → %s", ckpt)


def load_hash_net(cfg: HashMMConfig) -> tuple[CrossModalHashNet, dict]:
    """Reload a trained checkpoint. Returns (net, meta). Model is in eval mode
    (BatchNorm using running stats) — call .train() if you want to continue
    training."""
    ckpt = torch.load(cfg.hash_net_ckpt, map_location=cfg.hash_device, weights_only=False)
    net = CrossModalHashNet(
        text_in_dim=ckpt["text_in_dim"],
        image_in_dim=ckpt["image_in_dim"],
        hidden_dim=ckpt["hidden_dim"],
        bits=ckpt["bits"],
    )
    net.load_state_dict(ckpt["state_dict"])
    net.to(cfg.hash_device).eval()
    return net, ckpt
