#!/usr/bin/env python3
"""v17 Phase 23 — assign a stratified, deterministic held-out split to the golden set.

WHY: a single pool of cases that all inform tuning will eventually be overfit — you
tweak prompts/thresholds until the number goes up, and the number stops meaning
"quality". The fix (EVAL_UPGRADE_PLAN, integrity layer) is a held-out slice that
NEVER informs tuning; the eval then reports train vs heldout pass rates and their gap.

This marks ~RATIO of each category as split="heldout", the rest "train". Assignment
is DETERMINISTIC (hash of case id) so it is stable and reproducible across runs and
machines — re-running never reshuffles, and the same id is always on the same side.

Usage:
    python scripts/assign_heldout.py                       # data/eval/golden_cases.json, 25%
    python scripts/assign_heldout.py --ratio 0.3 path.json
    python scripts/assign_heldout.py --reset path.json     # clear all splits → train
"""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def _bucket(case_id: str, salt: str = "hashmm-heldout-v1") -> float:
    """Stable [0,1) hash for a case id — deterministic across runs/machines."""
    h = hashlib.sha256(f"{salt}:{case_id}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def assign(path: Path, ratio: float = 0.25, reset: bool = False) -> dict:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if reset:
        for c in cases:
            c["split"] = "train"
        path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"reset": len(cases)}

    # Stratify per category: within each category, the lowest-hash ~ratio go heldout.
    by_cat: dict[str, list] = defaultdict(list)
    for c in cases:
        by_cat[c.get("category", "unknown")].append(c)

    counts = {"train": 0, "heldout": 0}
    for cat, group in by_cat.items():
        ranked = sorted(group, key=lambda c: _bucket(c["id"]))
        n_hold = round(len(ranked) * ratio)
        hold_ids = {c["id"] for c in ranked[:n_hold]}
        for c in group:
            c["split"] = "heldout" if c["id"] in hold_ids else "train"
            counts[c["split"]] += 1

    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="data/eval/golden_cases.json")
    ap.add_argument("--ratio", type=float, default=0.25)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    p = Path(args.path)
    if not p.exists():
        print(f"[error] {p} not found")
        raise SystemExit(1)
    res = assign(p, ratio=args.ratio, reset=args.reset)
    print(f"[ok] {p}: {res}")
