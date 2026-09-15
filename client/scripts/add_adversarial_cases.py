#!/usr/bin/env python3
"""v17 Phase 23 — merge the bundled adversarial robustness probes into the golden set.

WHY: the 100-case domain set has saturated (98%) and lost discriminative power. A
held-out adversarial slice (prompt-injection, false-premise, PII over-ask, vague
query, noise, multi-hop) probes ROBUSTNESS rather than happy-path recall, so the
score starts meaning "robust quality" again. These probes are behavior-based and
corpus-agnostic — the correct behavior is honest refusal / no fabrication / non-empty,
which does not depend on your specific documents.

Idempotent: matches by id, only adds missing ones, never overwrites your edits.

Usage:
    python scripts/add_adversarial_cases.py                  # → data/eval/golden_cases.json
    python scripts/add_adversarial_cases.py path.json
"""
import json
import sys
from pathlib import Path

BUNDLED = Path(__file__).resolve().parent.parent / "hashmm/evaluation/adversarial_cases.json"


def merge(path: Path) -> dict:
    if not path.exists():
        print(f"[error] {path} not found")
        return {"added": 0}
    if not BUNDLED.exists():
        print(f"[error] bundled adversarial set missing: {BUNDLED}")
        return {"added": 0}
    cases = json.loads(path.read_text(encoding="utf-8"))
    have = {c.get("id") for c in cases}
    adv = json.loads(BUNDLED.read_text(encoding="utf-8"))
    added = [c for c in adv if c["id"] not in have]
    if added:
        cases.extend(added)
        path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] {path}: added {len(added)} adversarial case(s); total now {len(cases)}")
    return {"added": len(added), "total": len(cases)}


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/eval/golden_cases.json")
    merge(target)
