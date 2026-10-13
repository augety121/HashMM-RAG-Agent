#!/usr/bin/env python3
"""v17 Phase 22 — migrate the live golden set to use a semantic rubric for code_10.

WHY: code_10 ("写一个 Python 字典按值排序的代码") was failed by the brittle check
`must_contain:["def"]`, even though the idiomatic `sorted(...)+lambda` answer is
correct and has no `def`. Phase 22 adds a `rubric` field: when a judge is available,
a semantic check ("correctly implements dict-sort-by-value") decides content
correctness instead of the literal-substring match. The keyword stays as a HINT to
the judge (NOT removed) — so this upgrades the judgment's VALIDITY, it does not relax it.

This script patches your deployed data/eval/golden_cases.json in place (idempotent).
The bundled golden_cases_100.json already carries the rubric.

Usage:
    python scripts/patch_golden_phase22.py            # patches data/eval/golden_cases.json
    python scripts/patch_golden_phase22.py path.json  # patch a specific file
"""
import json
import sys
from pathlib import Path

RUBRIC = ("正确实现了 Python 字典按值排序：用 sorted(...) 配合 key 取值排序，"
          "能给出升序（可含降序），代码可运行且逻辑正确（用 def 封装或直接表达式皆可）")


def patch(path: Path) -> bool:
    if not path.exists():
        print(f"[skip] {path} not found")
        return False
    cases = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for c in cases:
        if c.get("id") == "code_10" and not c.get("rubric"):
            c["rubric"] = RUBRIC          # keyword 'def'/'sorted' kept as hints
            changed = True
    if changed:
        path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok] patched code_10 rubric in {path}")
    else:
        print(f"[noop] {path} already has the rubric (idempotent)")
    return changed


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/eval/golden_cases.json")
    patch(target)
