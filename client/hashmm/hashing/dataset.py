"""PyTorch Dataset over CrossModalPair JSONL files.

We keep the dataset thin: it just loads metadata. The expensive parts
(text tokenisation, image processing, encoder forward passes) happen in a
custom `collate_fn` that calls the frozen encoders once per batch. This is
much more efficient than per-item encoding inside a DataLoader worker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import torch
from torch.utils.data import Dataset

from hashmm.ingestion.chunk_extractor import CrossModalPair


@dataclass
class PairBatch:
    """A batch of cross-modal pairs ready for the hash net."""

    texts: list[str]
    image_paths: list[str]
    doc_ids: list[str]
    page_idxs: list[int]


class CrossModalPairsDataset(Dataset):
    """Reads pairs.jsonl into memory (it's small) and yields PairBatch entries.

    Why in-memory: even a corpus of 100k pairs is ~30MB of strings — trivial.
    Image loading is deferred to encode time.
    """

    def __init__(self, jsonl_path: str | Path):
        self.pairs: list[CrossModalPair] = []
        with Path(jsonl_path).open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                self.pairs.append(CrossModalPair(**d))
        if not self.pairs:
            raise ValueError(f"no pairs loaded from {jsonl_path}")

    def __len__(self) -> int:
        return len(self.pairs)

    def __getitem__(self, idx: int) -> CrossModalPair:
        return self.pairs[idx]


def pair_collate(items: list[CrossModalPair]) -> PairBatch:
    """Bundle CrossModalPair list into parallel arrays."""
    return PairBatch(
        texts=[p.text for p in items],
        image_paths=[p.image_path for p in items],
        doc_ids=[p.doc_id for p in items],
        page_idxs=[p.page_idx for p in items],
    )


def split_pairs(
    pairs: list[CrossModalPair],
    val_fraction: float = 0.05,
    seed: int = 42,
) -> tuple[list[CrossModalPair], list[CrossModalPair]]:
    """Split pairs into train / val.

    Strategy:
        * If we have ≥4 unique doc_ids: document-level split (correct way to
          avoid same-doc leakage between train and val).
        * Otherwise: random pair-level split, with a logged warning. Below
          4 docs, doc-level splits don't produce a meaningful val set.

    Args:
        val_fraction: fraction of held-out items (interpreted as docs when
            possible, else pairs).
        seed: RNG seed.
    """
    import random
    import logging

    logger = logging.getLogger("hashmm.hashing.dataset")
    rng = random.Random(seed)

    doc_ids = sorted({p.doc_id for p in pairs})

    if len(doc_ids) >= 4:
        # Proper document-level split.
        rng.shuffle(doc_ids)
        n_val = max(1, int(len(doc_ids) * val_fraction))
        val_docs = set(doc_ids[:n_val])
        train_pairs = [p for p in pairs if p.doc_id not in val_docs]
        val_pairs = [p for p in pairs if p.doc_id in val_docs]
        logger.info(
            "doc-level split: %d train docs, %d val docs", len(doc_ids) - n_val, n_val
        )
        return train_pairs, val_pairs

    # Fallback: pair-level split. Not ideal but workable for bootstrapping.
    logger.warning(
        "only %d unique docs — falling back to pair-level split. "
        "Add more documents (≥4) for proper doc-level validation.",
        len(doc_ids),
    )
    idxs = list(range(len(pairs)))
    rng.shuffle(idxs)
    n_val = max(1, int(len(pairs) * max(val_fraction, 0.15)))
    val_idxs = set(idxs[:n_val])
    train_pairs = [p for i, p in enumerate(pairs) if i not in val_idxs]
    val_pairs = [p for i, p in enumerate(pairs) if i in val_idxs]
    return train_pairs, val_pairs
