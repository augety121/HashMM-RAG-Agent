#!/usr/bin/env python3
"""10 — Fine-tune hash net on ViDoRe v2 corpus (M7.2).

Self-supervised similarity distillation via DIRECT Pearson-r maximisation:

    target  = BGE-M3 cosine matrix (frozen, off-diagonal only)
    student = hash code cosine matrix (off-diagonal only)
    loss    = - off_diag_Pearson_r(student, target)

Why this loss, and not MSE / listwise CE (both tried in v0.3.3 and v0.3.4,
both failed):

  * MSE on raw similarities fights BN's zero-mean constraint — the corpus
    mean cosine is ~0.7 but hash similarities centre at 0 (BN), so MSE
    just shouts "make all sims = 0.7" which BN refuses, leaving a tiny
    barely-moving loss while the structure quietly degrades.

  * Listwise softmax-CE puts ~99% of mass on the diagonal (S[i,i]=1 is
    dominated by S[i,j]≈0.7), so the gradient says "match yourself to
    yourself" — true but useless — and noise from the unconstrained
    direction drifts the head away from BGE-M3's structure.

  * Pearson r on off-diagonal is what we MEASURE in the eval table, and
    it's:
      - mean / scale invariant (BN-friendly)
      - sees only relative orderings (the thing nDCG cares about)
      - has no temperature hyperparam
      - has a well-conditioned gradient

We also use FULL-BATCH gradient descent (corpus is 996 docs, fits trivially
on the 4090) — every step sees the entire similarity graph, eliminating
mini-batch noise.

Safety: if final binary Pearson r < initial, the checkpoint is NOT saved.
The original is always at hash_net.pt.original.

Run:
    python scripts/10_finetune_vidore.py --dataset biomedical_lectures_eng_v2
    python scripts/09_run_vidore.py --dataset biomedical_lectures_eng_v2 --retriever both
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import numpy as np
import torch
from rich.console import Console
from rich.table import Table
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR

from hashmm.benchmark.vidore_loader import ViDoReDataset
from hashmm.config import HashMMConfig
from hashmm.hashing.encoders import TextEncoder
from hashmm.hashing.train import load_hash_net
from hashmm.utils import get_logger

console = Console()
logger = get_logger("scripts.10_finetune")


# ── corpus encoding (cached) ──────────────────────────────────────────


def encode_corpus_bgem3(corpus: dict, text_enc: TextEncoder,
                         batch_size: int, cache_path: Path | None):
    """Encode every doc with BGE-M3. Cached to .npz so re-runs are O(0)."""
    if cache_path and cache_path.exists():
        logger.info("loading cached BGE-M3 embeddings from %s", cache_path)
        d = np.load(cache_path, allow_pickle=True)
        return d["embs"], list(d["doc_ids"])

    ids: list[str] = []
    texts: list[str] = []
    for did, entry in corpus.items():
        t = (entry.get("text") or "").strip()
        if not t:
            continue
        ids.append(did)
        texts.append(t)

    logger.info("encoding %d docs with BGE-M3 (batch=%d)",
                len(texts), batch_size)
    out = []
    for i in range(0, len(texts), batch_size):
        with torch.no_grad():
            e = text_enc(texts[i:i + batch_size]).cpu().numpy().astype(np.float32)
        out.append(e)
        if (i // batch_size) % 10 == 0:
            logger.info("  ... %d / %d", i + len(e), len(texts))
    D = np.vstack(out)
    D /= (np.linalg.norm(D, axis=1, keepdims=True) + 1e-9)

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_path, embs=D, doc_ids=np.array(ids, dtype=object))
        logger.info("cached %d embeddings to %s", len(ids), cache_path)
    return D, ids


# ── loss ──────────────────────────────────────────────────────────────


def neg_pearson_off_diag(s_pred: torch.Tensor, s_target: torch.Tensor) -> torch.Tensor:
    """Negative Pearson r on off-diagonal elements.

    Diagonal (self-similarity = 1) is excluded so the loss is driven by
    cross-document relationships, not the trivial identity.
    """
    n = s_pred.size(0)
    mask = ~torch.eye(n, dtype=torch.bool, device=s_pred.device)
    sp = s_pred[mask]
    st = s_target[mask]
    sp_c = sp - sp.mean()
    st_c = st - st.mean()
    denom = (sp_c.norm() * st_c.norm()).clamp(min=1e-9)
    return -((sp_c * st_c).sum() / denom)


# ── main ──────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--cache-dir", default="./benchmark_cache")
    ap.add_argument("--steps", type=int, default=100,
                    help="Number of FULL-BATCH gradient steps. 100 steps "
                         "on 996 docs takes <30s on a 4090.")
    ap.add_argument("--lr", type=float, default=5e-5,
                    help="Very gentle — text_head is already pre-trained.")
    ap.add_argument("--encode-batch-size", type=int, default=32,
                    help="Used only for BGE-M3 forward passes; training is "
                         "full-batch.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--force-save", action="store_true",
                    help="Save even if Pearson r got worse (NOT recommended).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Train but DO NOT touch the checkpoint.")
    ap.add_argument("--max-grad-norm", type=float, default=1.0)
    args = ap.parse_args()

    cfg = HashMMConfig()
    device = cfg.hash_device
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    # ── Load dataset ──────────────────────────────────────────────────
    ds = ViDoReDataset(args.dataset, cache_dir=args.cache_dir)
    ds.load()
    n_with_text = sum(1 for d in ds.corpus.values() if d.get("text"))
    if n_with_text == 0:
        console.print(
            "[red]Corpus has no text. Run 09_run_vidore.py first to OCR.[/red]"
        )
        return
    console.print(f"[green]✓[/green] dataset: {n_with_text} docs with text")

    # ── Encode corpus with BGE-M3 (cached) ────────────────────────────
    enc_cache = Path(args.cache_dir) / args.dataset / "bgem3_corpus.npz"
    text_enc = TextEncoder(model_name=cfg.hash_text_encoder, device=device)
    D, doc_ids = encode_corpus_bgem3(ds.corpus, text_enc,
                                      args.encode_batch_size, enc_cache)
    n_docs, d_in = D.shape
    console.print(f"[green]✓[/green] BGE-M3 corpus matrix: {D.shape}")

    del text_enc
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # ── Load hash net (+ backup if first run) ─────────────────────────
    if not cfg.hash_net_ckpt.exists():
        console.print(f"[red]hash net checkpoint missing[/red]")
        return

    backup_path = cfg.hash_net_ckpt.with_suffix(".pt.original")
    if backup_path.exists():
        console.print(
            f"[dim]backup exists at {backup_path}. Loading current "
            f"hash_net.pt — if it's a broken fine-tune, restore via:\n"
            f"  cp {backup_path} {cfg.hash_net_ckpt}[/dim]"
        )

    net, ckpt_meta = load_hash_net(cfg)
    bits = ckpt_meta["bits"]
    console.print(f"[green]✓[/green] loaded hash net: bits={bits}")

    if ckpt_meta["text_in_dim"] != d_in:
        console.print(f"[red]Encoder dim mismatch[/red]")
        return

    for p in net.image_head.parameters():
        p.requires_grad = False
    trainable = sum(p.numel() for p in net.text_head.parameters()
                    if p.requires_grad)
    console.print(f"trainable params (text_head only): {trainable:,}")

    if not args.dry_run and not backup_path.exists():
        shutil.copy(cfg.hash_net_ckpt, backup_path)
        console.print(f"[green]✓[/green] backed up original → {backup_path}")

    # ── Target similarity matrix ──────────────────────────────────────
    D_t = torch.from_numpy(D).to(device)
    S_target = (D_t @ D_t.T).clamp(-1, 1)
    console.print(
        f"target sim matrix: shape={tuple(S_target.shape)}, "
        f"off-diag mean={S_target[~torch.eye(n_docs, dtype=torch.bool, device=device)].mean():.4f}, "
        f"std={S_target[~torch.eye(n_docs, dtype=torch.bool, device=device)].std():.4f}"
    )

    # ── Baseline metrics ──────────────────────────────────────────────
    def _eval_pearson_r(state_label: str):
        net.eval()
        with torch.no_grad():
            h = torch.tanh(net.text_head(D_t))
            S_cont = (h @ h.T) / bits
            cont_r = _off_diag_pearson(S_cont, S_target)

            b = torch.sign(net.text_head(D_t)).clamp(-1, 1)
            b = torch.where(b == 0, torch.ones_like(b), b)
            S_bin = (b @ b.T) / bits
            bin_r = _off_diag_pearson(S_bin, S_target)
        return cont_r, bin_r

    baseline_cont_r, baseline_bin_r = _eval_pearson_r("baseline")
    console.print(
        f"[dim]baseline: continuous r = {baseline_cont_r:.4f}, "
        f"binary r = {baseline_bin_r:.4f}[/dim]"
    )

    # Snapshot original head weights so we can restore if training degrades
    original_state = {k: v.detach().clone()
                      for k, v in net.text_head.state_dict().items()}
    # Also snapshot running BN stats (they'll be updated during training)
    for name, buf in net.text_head.named_buffers():
        original_state[name] = buf.detach().clone()

    # ── Full-batch training loop ──────────────────────────────────────
    net.train()
    optim = AdamW(net.text_head.parameters(), lr=args.lr, weight_decay=0)
    sched = CosineAnnealingLR(optim, T_max=args.steps)

    history = []
    best_bin_r = baseline_bin_r
    best_state = None
    t_start = time.time()
    for step in range(1, args.steps + 1):
        h = torch.tanh(net.text_head(D_t))                # (N, K)
        S_pred = (h @ h.T) / bits                          # (N, N)
        loss = neg_pearson_off_diag(S_pred, S_target)      # scalar
        optim.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.text_head.parameters(),
                                       args.max_grad_norm)
        optim.step()
        sched.step()

        # Periodically eval real binary Pearson r (the metric we ship)
        if step <= 5 or step % 10 == 0 or step == args.steps:
            cont_r, bin_r = _eval_pearson_r(f"step {step}")
            net.train()
            history.append({"step": step, "loss": float(loss),
                            "cont_r": cont_r, "bin_r": bin_r,
                            "lr": sched.get_last_lr()[0]})
            console.print(
                f"step {step:3d}/{args.steps}  "
                f"loss={float(loss):+.5f}  "
                f"cont_r={cont_r:.4f}  "
                f"bin_r={bin_r:.4f}  "
                f"lr={sched.get_last_lr()[0]:.6f}"
            )
            # Keep best-seen weights (early stop in spirit)
            if bin_r > best_bin_r:
                best_bin_r = bin_r
                # save buffers + params
                bs = {k: v.detach().clone()
                      for k, v in net.text_head.state_dict().items()}
                for name, buf in net.text_head.named_buffers():
                    bs[name] = buf.detach().clone()
                best_state = bs
    train_time = time.time() - t_start

    # ── Final eval — restore best-seen weights if any improvement ─────
    if best_state is not None:
        net.text_head.load_state_dict(best_state, strict=False)
        # Also restore buffers manually (state_dict includes them but be safe)
        for name, buf in net.text_head.named_buffers():
            if name in best_state:
                buf.copy_(best_state[name])
        console.print(
            f"[green]restored best-step weights (bin_r = {best_bin_r:.4f})[/green]"
        )

    final_cont_r, final_bin_r = _eval_pearson_r("final")

    tbl = Table(title="Fine-tuning summary", show_header=True)
    tbl.add_column("Metric", width=30)
    tbl.add_column("Before", width=10)
    tbl.add_column("After", width=10)
    tbl.add_column("Δ", width=10)
    tbl.add_row("Pearson r (continuous)",
                f"{baseline_cont_r:.4f}", f"{final_cont_r:.4f}",
                f"{final_cont_r - baseline_cont_r:+.4f}")
    tbl.add_row("Pearson r (binary — what 09 uses)",
                f"{baseline_bin_r:.4f}", f"{final_bin_r:.4f}",
                f"{final_bin_r - baseline_bin_r:+.4f}")
    console.print(tbl)
    console.print(f"trained for {train_time:.1f}s ({args.steps} full-batch steps)")

    # ── Safety: only save if binary Pearson r improved ────────────────
    improved = final_bin_r > baseline_bin_r
    if args.dry_run:
        console.print("[yellow]--dry-run: not touching the checkpoint[/yellow]")
        return
    if not improved and not args.force_save:
        console.print(
            f"[red]binary Pearson r did NOT improve "
            f"({baseline_bin_r:.4f} → {final_bin_r:.4f}). "
            f"Restoring original head weights in memory and NOT saving.[/red]"
        )
        # Restore param weights AND running buffers
        param_keys = {n for n, _ in net.text_head.named_parameters()}
        buf_keys = {n for n, _ in net.text_head.named_buffers()}
        param_state = {k: v for k, v in original_state.items() if k in param_keys}
        buf_state = {k: v for k, v in original_state.items() if k in buf_keys}
        # load params via state_dict
        full = net.text_head.state_dict()
        full.update(param_state)
        full.update(buf_state)
        net.text_head.load_state_dict(full)
        console.print(
            f"[dim]hash_net.pt left unchanged. Use --force-save to override.[/dim]"
        )
        return

    new_ckpt = {
        "text_in_dim": ckpt_meta["text_in_dim"],
        "image_in_dim": ckpt_meta["image_in_dim"],
        "hidden_dim": ckpt_meta["hidden_dim"],
        "bits": ckpt_meta["bits"],
        "state_dict": net.state_dict(),
        "finetune_dataset": args.dataset,
        "finetune_history": history,
        "finetune_time_sec": train_time,
        "baseline_bin_r": baseline_bin_r,
        "final_bin_r": final_bin_r,
    }
    torch.save(new_ckpt, cfg.hash_net_ckpt)
    console.print(
        f"[green]✓[/green] saved fine-tuned ckpt → {cfg.hash_net_ckpt}\n"
        f"\n[bold]Next:[/bold]\n"
        f"  python scripts/09_run_vidore.py "
        f"--dataset {args.dataset} --retriever hashmm\n"
        f"\n[dim]Restore original anytime:\n"
        f"  cp {backup_path} {cfg.hash_net_ckpt}[/dim]"
    )


# ── helpers ────────────────────────────────────────────────────────────


def _off_diag_pearson(s_pred: torch.Tensor, s_target: torch.Tensor) -> float:
    n = s_pred.size(0)
    mask = ~torch.eye(n, dtype=torch.bool, device=s_pred.device)
    sp = s_pred[mask].flatten().float()
    st = s_target[mask].flatten().float()
    sp_c = sp - sp.mean()
    st_c = st - st.mean()
    denom = (sp_c.norm() * st_c.norm()).clamp(min=1e-9)
    return float((sp_c * st_c).sum() / denom)


if __name__ == "__main__":
    main()


