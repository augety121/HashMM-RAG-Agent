#!/usr/bin/env python3
"""03 — Train the cross-modal hash network.

Usage:
    python scripts/03_train_hash_net.py --pairs ./data/pairs.jsonl

All hyperparameters come from .env / HashMMConfig (HASH_BITS, HASH_EPOCHS, etc).
The trained checkpoint goes to cfg.hash_net_ckpt (default: ./checkpoints/hash_net.pt).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hashmm.config import HashMMConfig
from hashmm.hashing.train import train_hash_net
from hashmm.utils import get_logger

logger = get_logger("scripts.03_train")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", required=True, type=Path)
    ap.add_argument(
        "--val-fraction",
        type=float,
        default=0.05,
        help="fraction of *documents* held out for validation",
    )
    args = ap.parse_args()

    cfg = HashMMConfig()
    logger.info("config:")
    for k, v in cfg.to_dict().items():
        logger.info("  %s = %s", k, v)

    ckpt = train_hash_net(cfg, pairs_jsonl=args.pairs, val_fraction=args.val_fraction)
    logger.info("✓ checkpoint saved to %s", ckpt)


if __name__ == "__main__":
    main()
