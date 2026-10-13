"""Frozen pretrained encoders for the hash net.

Design choice: both encoders are **frozen** during hash net training. Only
the small MLP heads on top are learned. This is the standard recipe in
DCMH-family methods and matches the constraint of a single 4090 — training
the full encoders would not fit.

Defaults:
    text  → BAAI/bge-m3 (1024-d, multilingual, ~568M params)
    image → google/siglip2-base-patch16-256 (768-d image features)

Outputs are L2-normalized so cosine similarity = dot product; this is what
the pairwise similarity preserving loss expects.
"""

from __future__ import annotations

from typing import Sequence

import torch
from torch import nn

from hashmm.utils import get_logger

logger = get_logger("hashmm.hashing.encoders")


# ───────────────────────────────────────────────────────────────────────
# Text encoder
# ───────────────────────────────────────────────────────────────────────


class TextEncoder(nn.Module):
    """Frozen text encoder. Default: BGE-M3."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        max_length: int = 512,
        device: str = "cuda",
    ):
        super().__init__()
        from transformers import AutoModel, AutoTokenizer

        logger.info("loading text encoder: %s", model_name)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        # Prefer safetensors (avoids CVE-2025-32434 block on torch<2.6).
        # Fall back to default loading if a model has no safetensors variant.
        try:
            self.model = AutoModel.from_pretrained(model_name, use_safetensors=True)
        except (OSError, EnvironmentError) as e:
            logger.warning("no safetensors for %s (%s); falling back", model_name, e)
            self.model = AutoModel.from_pretrained(model_name)
        self.max_length = max_length
        self.device_str = device

        # Freeze: no grads, eval mode.
        for p in self.model.parameters():
            p.requires_grad = False
        self.model.eval()
        self.model.to(device)

        # Probe output dim with a tiny forward pass — done once at init so we
        # don't have to hardcode dims per model.
        with torch.no_grad():
            sample = self.tokenizer(
                "probe", return_tensors="pt", padding=True, truncation=True
            ).to(device)
            out = self.model(**sample)
            self.out_dim = out.last_hidden_state.shape[-1]
        logger.info("text encoder output dim = %d", self.out_dim)

    @torch.no_grad()
    def forward(self, texts: Sequence[str]) -> torch.Tensor:
        """Encode a batch of strings → (B, out_dim) L2-normalised."""
        if not texts:
            return torch.empty(0, self.out_dim, device=self.device_str)
        enc = self.tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        ).to(self.device_str)
        out = self.model(**enc)
        # BGE / BERT-style: mean-pool over non-padding tokens.
        mask = enc["attention_mask"].unsqueeze(-1).float()
        summed = (out.last_hidden_state * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1)
        pooled = summed / counts
        return torch.nn.functional.normalize(pooled, dim=-1)


# ───────────────────────────────────────────────────────────────────────
# Image encoder
# ───────────────────────────────────────────────────────────────────────


class ImageEncoder(nn.Module):
    """Frozen image encoder. Default: SigLIP-2 base."""

    def __init__(
        self,
        model_name: str = "google/siglip2-base-patch16-256",
        device: str = "cuda",
    ):
        super().__init__()
        from transformers import AutoModel, AutoProcessor

        logger.info("loading image encoder: %s", model_name)
        self.processor = AutoProcessor.from_pretrained(model_name)
        try:
            self.model = AutoModel.from_pretrained(model_name, use_safetensors=True)
        except (OSError, EnvironmentError) as e:
            logger.warning("no safetensors for %s (%s); falling back", model_name, e)
            self.model = AutoModel.from_pretrained(model_name)
        self.device_str = device

        for p in self.model.parameters():
            p.requires_grad = False
        self.model.eval()
        self.model.to(device)

        # Probe vision tower output.
        from PIL import Image

        with torch.no_grad():
            dummy = Image.new("RGB", (256, 256), color=(127, 127, 127))
            sample = self.processor(images=dummy, return_tensors="pt").to(device)
            feats = self._image_features(sample)
            self.out_dim = feats.shape[-1]
        logger.info("image encoder output dim = %d", self.out_dim)

    def _image_features(self, processed: dict) -> torch.Tensor:
        """Pull image features from SigLIP-family model in a version-robust way."""
        if hasattr(self.model, "get_image_features"):
            # SigLIP / CLIP-style API
            return self.model.get_image_features(**processed)
        # Fallback: pool the vision tower output
        out = self.model.vision_model(**processed)
        # Some versions return pooler_output, others last_hidden_state[:,0]
        if hasattr(out, "pooler_output") and out.pooler_output is not None:
            return out.pooler_output
        return out.last_hidden_state.mean(dim=1)

    @torch.no_grad()
    def forward(self, images) -> torch.Tensor:
        """Encode a batch of PIL.Image or paths → (B, out_dim) L2-normalised."""
        from PIL import Image

        if not images:
            return torch.empty(0, self.out_dim, device=self.device_str)
        pils = []
        for x in images:
            if isinstance(x, Image.Image):
                pils.append(x.convert("RGB"))
            elif isinstance(x, str):
                pils.append(Image.open(x).convert("RGB"))
            else:
                raise TypeError(f"unsupported image input: {type(x)}")
        processed = self.processor(images=pils, return_tensors="pt").to(self.device_str)
        feats = self._image_features(processed)
        return torch.nn.functional.normalize(feats, dim=-1)
