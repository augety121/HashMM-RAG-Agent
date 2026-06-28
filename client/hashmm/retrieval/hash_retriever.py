"""Hash-based retriever.

Encodes a query (text or image) into a K-bit code using the same hash net
used to build the index, then does Hamming-distance kNN against the index.

This is the cross-modal magic: a text query naturally retrieves images,
tables, equations — anything that was indexed — because all modalities
share the K-bit code space.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from hashmm.config import HashMMConfig
from hashmm.hashing.index import HashIndex
from hashmm.retrieval.base import BaseRetriever, RetrievedChunk
from hashmm.utils import get_logger

logger = get_logger("hashmm.retrieval.hash")


class HashRetriever(BaseRetriever):
    """Retrieve via Hamming search over learned binary codes."""

    name = "hash"

    def __init__(
        self,
        cfg: HashMMConfig,
        hash_net=None,           # CrossModalHashNet (loaded), else lazy-loaded
        text_encoder=None,        # TextEncoder, else lazy-loaded
        image_encoder=None,       # ImageEncoder, else lazy-loaded
        hash_index: HashIndex | None = None,  # else loaded from cfg paths
    ):
        self.cfg = cfg
        self._net = hash_net
        self._text_enc = text_encoder
        self._image_enc = image_encoder
        self._index = hash_index
        self._device = cfg.hash_device

    # ── Lazy loaders ──────────────────────────────────────────────────

    def _ensure_net(self):
        if self._net is not None:
            return self._net
        from hashmm.hashing.train import load_hash_net

        net, meta = load_hash_net(self.cfg)
        net.eval()  # BatchNorm in HashHead must use running stats at query time
        self._net = net
        # Stash meta so we use the SAME encoder strings as training
        self._net_meta = meta
        return self._net

    def _ensure_text_encoder(self):
        if self._text_enc is not None:
            return self._text_enc
        from hashmm.hashing.encoders import TextEncoder

        self._ensure_net()
        encoder_name = self._net_meta.get("text_encoder", self.cfg.hash_text_encoder)
        self._text_enc = TextEncoder(encoder_name, device=self._device)
        return self._text_enc

    def _ensure_image_encoder(self):
        if self._image_enc is not None:
            return self._image_enc
        from hashmm.hashing.encoders import ImageEncoder

        self._ensure_net()
        encoder_name = self._net_meta.get("image_encoder", self.cfg.hash_image_encoder)
        self._image_enc = ImageEncoder(encoder_name, device=self._device)
        return self._image_enc

    def _ensure_index(self):
        if self._index is not None:
            return self._index
        self._index = HashIndex.load(
            self.cfg.hash_index_path, self.cfg.hash_metadata_path
        )
        return self._index

    # ── Public API ────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        modality_hint: str | None = None,
        query_image_path: str | None = None,
    ) -> list[RetrievedChunk]:
        """Hash-domain Hamming-distance kNN.

        If `query_image_path` is set, the image side of the query is used;
        otherwise the text side. (You can do both — see `retrieve_multimodal`
        below.)

        When `modality_hint` is set, we widen the index search to top_k * 10
        before filtering. Otherwise on small indexes the top-K is dominated
        by the most populous modality and filtering yields empty results.
        """
        if query_image_path:
            code = self._encode_image_to_bits(query_image_path)
        else:
            code = self._encode_text_to_bits(query)

        idx = self._ensure_index()
        # Widen the candidate pool when a modality filter is active so we
        # have enough non-rejected hits to fill top_k.
        if modality_hint:
            search_k = min(max(top_k * 10, 100), idx.n_items or top_k * 10)
        else:
            search_k = top_k
        hits = idx.search(code, top_k=search_k)
        filtered = self._hits_to_chunks(hits, modality_hint=modality_hint)
        return filtered[:top_k]  # truncate after filtering

    def retrieve_multimodal(
        self,
        text: str | None,
        image_path: str | None,
        top_k: int = 20,
        modality_hint: str | None = None,
    ) -> list[RetrievedChunk]:
        """Combine text and image queries by averaging their codes.

        When both modalities are given, take the bit-wise majority vote of
        their codes (equivalent to averaging in {-1, +1} space then signing).
        """
        if text is None and image_path is None:
            return []
        if image_path is None:
            return self.retrieve(text or "", top_k=top_k, modality_hint=modality_hint)
        if text is None:
            return self.retrieve(
                "",
                top_k=top_k,
                modality_hint=modality_hint,
                query_image_path=image_path,
            )

        import torch  # local import — keeps top-of-file numpy-only

        net = self._ensure_net()
        text_enc = self._ensure_text_encoder()
        image_enc = self._ensure_image_encoder()
        with torch.no_grad():
            t_emb = text_enc([text])
            i_emb = image_enc([image_path])
            t_code = net.sign_text(t_emb)
            i_code = net.sign_image(i_emb)
            # Majority vote → equivalent to sign of sum
            joint = torch.sign(t_code + i_code)
            joint[joint == 0] = 1.0  # ties → +1
        code = self._pack_to_uint8(joint)
        idx = self._ensure_index()
        if modality_hint:
            search_k = min(max(top_k * 10, 100), idx.n_items or top_k * 10)
        else:
            search_k = top_k
        hits = idx.search(code, top_k=search_k)
        return self._hits_to_chunks(hits, modality_hint=modality_hint)[:top_k]

    # ── Encoding helpers ──────────────────────────────────────────────

    def _encode_text_to_bits(self, query: str) -> np.ndarray:
        import torch

        net = self._ensure_net()
        text_enc = self._ensure_text_encoder()
        with torch.no_grad():
            emb = text_enc([query])
            code = net.sign_text(emb)
        return self._pack_to_uint8(code)

    def _encode_image_to_bits(self, image_path: str) -> np.ndarray:
        import torch

        net = self._ensure_net()
        image_enc = self._ensure_image_encoder()
        with torch.no_grad():
            emb = image_enc([image_path])
            code = net.sign_image(emb)
        return self._pack_to_uint8(code)

    @staticmethod
    def _pack_to_uint8(code) -> np.ndarray:
        """Convert (1, K) torch ±1 → (K/8,) uint8 with Faiss bit order."""
        from hashmm.hashing.hash_net import pack_bits

        packed = pack_bits(code).cpu().numpy()
        return packed.reshape(-1)

    # ── Hit → RetrievedChunk ──────────────────────────────────────────

    @staticmethod
    def _hits_to_chunks(hits, modality_hint: str | None = None) -> list[RetrievedChunk]:
        out: list[RetrievedChunk] = []
        for h in hits:
            if modality_hint and h.modality != modality_hint:
                continue
            # Score: invert hamming distance so higher = better.
            # We pass the raw distance through too in meta.
            meta = dict(h.meta)
            meta["hamming_dist"] = h.hamming_dist
            out.append(
                RetrievedChunk(
                    chunk_id=h.chunk_id,
                    modality=h.modality,
                    text=meta.get("text", ""),
                    image_path=meta.get("image_path"),
                    score=-float(h.hamming_dist),
                    rank=h.rank,
                    source="hash",
                    meta=meta,
                )
            )
        return out
