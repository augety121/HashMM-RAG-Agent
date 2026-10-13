"""Cross-modal hash network.

Architecture (DCMH / DSPH family):

    text_emb  ─→ MLP_text  ─→ tanh(τ·z)  ─→ b_text  ∈ (-1,+1)^K
    image_emb ─→ MLP_image ─→ tanh(τ·z)  ─→ b_image ∈ (-1,+1)^K

At inference: binary code = sign(b). At training: we keep the smooth tanh
and apply gradient via a straight-through estimator when we need a hard
binary step inside the loss (here we don't — pairwise similarity preserving
loss operates on the continuous b directly).

The temperature τ is *annealed* upward during training: start small (smooth
gradients) and increase toward the end so tanh approaches the sign function.
This makes the trained network's continuous output already close to ±1, so
the sign-thresholding at inference time loses minimal information.
"""

from __future__ import annotations

import torch
from torch import nn


class HashHead(nn.Module):
    """A single MLP that projects encoder features to K-bit codes.

    Architecture:
        Linear → GELU → Dropout → Linear → GELU → Dropout → Linear(→bits)
        → BatchNorm1d(affine=False)   ← prevents bit collapse

    The BatchNorm WITHOUT affine parameters is the critical anti-collapse
    insurance: it forces each of the K output bits to have zero mean and
    unit variance across the batch. This is a HARD constraint enforced
    by data statistics, not a soft regulariser — without it, the network
    can (and on small datasets does) learn to push all codes to the same
    quadrant of the hash space, collapsing similarity into uniformity.
    Standard trick from HashNet (Cao et al., ICCV 2017) and DPSH.

    Mid-width 2048 is generous but fits cheaply in 24GB even with thousands
    of items per batch — the heads are tiny vs. the frozen encoders.
    """

    def __init__(self, in_dim: int, hidden_dim: int, bits: int, dropout: float = 0.1):
        super().__init__()
        self.bits = bits
        self.mlp = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, bits),
        )
        # affine=False: pure standardisation, no learnable scale/shift —
        # otherwise the net can simply undo the standardisation.
        self.bn = nn.BatchNorm1d(bits, affine=False, eps=1e-5)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.mlp(x)
        # During eval BN uses running stats; during training, batch stats.
        # On tiny batches (B=8) this is noisy but still effective.
        return self.bn(z)


class CrossModalHashNet(nn.Module):
    """Two HashHeads (text + image) sharing a hash space.

    The encoders are NOT inside this module — we pass pre-computed
    embeddings in. This decouples encoding (slow, frozen) from hash training
    (fast, on small MLPs), so we can cache encoder outputs once and iterate
    quickly on the hash head.
    """

    def __init__(
        self,
        text_in_dim: int,
        image_in_dim: int,
        hidden_dim: int = 2048,
        bits: int = 128,
    ):
        super().__init__()
        self.bits = bits
        self.text_head = HashHead(text_in_dim, hidden_dim, bits)
        self.image_head = HashHead(image_in_dim, hidden_dim, bits)

    def encode_text(self, text_emb: torch.Tensor, tau: float = 1.0) -> torch.Tensor:
        # text_head returns pre-tanh activations (already BN-normalised).
        return torch.tanh(tau * self.text_head(text_emb))

    def encode_image(self, image_emb: torch.Tensor, tau: float = 1.0) -> torch.Tensor:
        return torch.tanh(tau * self.image_head(image_emb))

    def forward(
        self,
        text_emb: torch.Tensor,
        image_emb: torch.Tensor,
        tau: float = 1.0,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Encode both modalities. Returns (b_text, b_image) both in (-1,+1)^K."""
        return self.encode_text(text_emb, tau), self.encode_image(image_emb, tau)

    @torch.no_grad()
    def sign_text(self, text_emb: torch.Tensor) -> torch.Tensor:
        """Inference: hard binary code from text (1 / -1).

        IMPORTANT: relies on net.eval() being called externally so BatchNorm
        uses running stats (not batch stats — which would produce garbage on
        batches of 1).
        """
        return torch.sign(self.text_head(text_emb)).clamp(min=-1.0).clamp(max=1.0)

    @torch.no_grad()
    def sign_image(self, image_emb: torch.Tensor) -> torch.Tensor:
        """Inference: hard binary code from image (1 / -1). See sign_text."""
        return torch.sign(self.image_head(image_emb)).clamp(min=-1.0).clamp(max=1.0)


# ───────────────────────────────────────────────────────────────────────
# Bit packing utilities — convert (B, K) ∈ {-1, +1} to (B, K/8) uint8 for
# Faiss IndexBinary*. Bit order matches Faiss convention: bit i of byte j
# represents code element (j*8 + i).
# ───────────────────────────────────────────────────────────────────────


def pack_bits(codes: torch.Tensor) -> torch.Tensor:
    """Convert (B, K) tensor of ±1 to (B, K/8) uint8.

    Maps -1 → 0, +1 → 1, then packs 8 bits per byte (LSB first, matches numpy
    `packbits` with `bitorder='little'` which Faiss uses).
    """
    assert codes.dim() == 2, "expected (B, K)"
    K = codes.shape[1]
    if K % 8 != 0:
        raise ValueError(f"bit length must be a multiple of 8, got {K}")
    bits = (codes > 0).to(torch.uint8)  # (B, K)
    # Reshape to (B, K/8, 8) and weight bits 1,2,4,...,128.
    weights = torch.tensor(
        [1, 2, 4, 8, 16, 32, 64, 128], dtype=torch.uint8, device=bits.device
    )
    return (bits.view(bits.shape[0], -1, 8) * weights).sum(dim=-1).to(torch.uint8)


def unpack_bits(packed: torch.Tensor, bits: int) -> torch.Tensor:
    """Inverse of `pack_bits`: (B, K/8) uint8 → (B, K) ±1 float."""
    weights = torch.tensor(
        [1, 2, 4, 8, 16, 32, 64, 128], dtype=torch.uint8, device=packed.device
    )
    expanded = packed.unsqueeze(-1).repeat(1, 1, 8)
    out = ((expanded & weights) > 0).to(torch.float32).view(packed.shape[0], -1)
    out = out[:, :bits]
    return out * 2.0 - 1.0  # 0/1 → -1/+1
