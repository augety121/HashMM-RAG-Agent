"""Losses for cross-modal hash training.

We compose three terms:

1. **Pairwise similarity preserving** (PSPL): the dominant signal. For each
   batch of `B` paired (text, image) items we have similarity matrix S where
   S[i,j] = +1 if i and j are positives (same pair index, plus optional
   document-level positives), else -1. The loss tries to align
       <b_text_i, b_image_j> / K
   with S[i,j]. We use a *negative log-likelihood* form derived from sigmoid
   (DCMH-style) which gives stable gradients across both modalities.

2. **Quantization** (Q): pulls each continuous code toward ±1 with
   ||b - sign(b)||² . Together with tanh annealing this keeps the
   sign() at inference cheap.

3. **Bit balance** (B): each bit's mean across the batch should be 0
   (i.e. equal +1 and -1 counts). Without this the network can collapse
   bits to constants.

The three weights are exposed in HashMMConfig.
"""

from __future__ import annotations

import torch
from torch import nn
import torch.nn.functional as F


class HashLoss(nn.Module):
    """Cross-modal pairwise hash loss (DCMH-style) + regularisers."""

    def __init__(
        self,
        bits: int,
        w_quant: float = 0.1,
        w_balance: float = 0.05,
        gamma: float = 4.0,
    ):
        """
        Args:
            bits: K — the hash length.
            w_quant: weight on the quantization regulariser.
            w_balance: weight on the bit balance regulariser.
            gamma: temperature-like scale. With cosine-style normalisation
                (theta = inner * gamma / sqrt(bits)), gamma=4 works well for
                K=64-256. Increase if pair loss stalls; decrease if it blows up.
        """
        super().__init__()
        self.bits = bits
        self.w_quant = w_quant
        self.w_balance = w_balance
        self.gamma = gamma

    def forward(
        self,
        b_text: torch.Tensor,    # (B, K) ∈ (-1, +1)
        b_image: torch.Tensor,   # (B, K) ∈ (-1, +1)
        sim: torch.Tensor | None = None,  # (B, B) with values in {-1, +1}; defaults to identity
    ) -> dict[str, torch.Tensor]:
        """Returns dict with named scalars; `loss` is the total.

        Components are returned individually so they can be logged.
        """
        B = b_text.shape[0]
        device = b_text.device

        if sim is None:
            # Default: i-th text matches i-th image only.
            sim = (
                2.0 * torch.eye(B, device=device, dtype=b_text.dtype) - 1.0
            )  # (B, B) in {-1, +1}

        # ── 1) Pairwise similarity preserving loss ─────────────────────
        # Cosine-style scaling: with codes in [-1,+1], <b_t, b_i> is O(bits)
        # for positives, but to feed it into sigmoid we want theta to land
        # in [-bits, +bits]/sqrt(bits) so gradients are non-vanishing.
        # Empirically, gamma=4.0 / sqrt(bits) works well for K∈{64,128,256}.
        inner = b_text @ b_image.t()                       # (B, B), range ~[-bits, +bits]
        scale = self.gamma * (self.bits ** 0.5) / self.bits  # = gamma / sqrt(bits)
        theta = inner * scale                              # well-scaled logits

        # DCMH-style stable NLL using logsigmoid:
        #   S_ij = +1 → loss_ij = -log(sigmoid( theta_ij))
        #   S_ij = -1 → loss_ij = -log(sigmoid(-theta_ij))
        # = -log(sigmoid(S_ij * theta_ij))
        nll = -F.logsigmoid(sim * theta)  # (B, B)
        loss_pair = nll.mean()

        # ── 2) Quantization: pull continuous code → ±1 ─────────────────
        # ||b - sign(b)||^2 ; sign() detached so gradient flows only through b.
        sign_t = torch.sign(b_text).detach()
        sign_i = torch.sign(b_image).detach()
        loss_quant = ((b_text - sign_t) ** 2).mean() + ((b_image - sign_i) ** 2).mean()

        # ── 3) Bit balance: per-bit mean over batch should be 0 ────────
        loss_balance = (b_text.mean(dim=0) ** 2).mean() + (
            b_image.mean(dim=0) ** 2
        ).mean()

        loss = loss_pair + self.w_quant * loss_quant + self.w_balance * loss_balance
        return {
            "loss": loss,
            "loss_pair": loss_pair.detach(),
            "loss_quant": loss_quant.detach(),
            "loss_balance": loss_balance.detach(),
        }


# ───────────────────────────────────────────────────────────────────────
# Helper: build the similarity matrix given doc_ids — items from the same
# source document are also positives. This is the "neighbourhood positives"
# trick that makes data-scarce settings (a few thousand pairs) trainable.
# ───────────────────────────────────────────────────────────────────────


def build_similarity_matrix(
    doc_ids: list[str],
    page_idxs: list[int] | None = None,
    page_window: int = 1,
    device: str = "cpu",
) -> torch.Tensor:
    """Construct an (N, N) similarity matrix in {-1, +1}.

    +1 if (a) same row (diagonal — paired) OR
          (b) same doc_id AND |page_a - page_b| <= page_window;
    -1 otherwise.

    The diagonal is always +1; off-diagonal positives reward the network
    for embedding semantically-related items from the same document close
    together.
    """
    N = len(doc_ids)
    sim = -torch.ones(N, N, device=device)
    if page_idxs is None:
        page_idxs = [0] * N
    for i in range(N):
        for j in range(N):
            if i == j:
                sim[i, j] = 1.0
            elif doc_ids[i] == doc_ids[j] and abs(page_idxs[i] - page_idxs[j]) <= page_window:
                sim[i, j] = 1.0
    return sim
