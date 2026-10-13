#!/usr/bin/env python3
"""校验大规模评测集与语料自洽:每条正例(非拒答/对抗/招呼/派生计算)的
must_contain_any 至少有一个 token 真的出现在语料里。

  python validate_kit.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"
CASES = HERE / "golden_cases_large.json"
EXEMPT_CATS = {"refusal", "adversarial", "greeting", "code"}  # 答案不一定在语料/为派生


def main() -> int:
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in sorted(CORPUS.glob("*.md")))
    docs = {p.name for p in CORPUS.glob("*.md")}
    cases = json.loads(CASES.read_text(encoding="utf-8"))

    grounded = exempt = problems = 0
    cats: dict[str, int] = {}
    # 派生(数值计算/跨公司聚合)用例:答案是算出来的,不要求逐字在语料
    DERIVED_PREFIX = ("co", "global_")
    DERIVED_SUFFIX = ("_temporal_growth",)
    for c in cases:
        cid, cat = c["id"], c.get("category", "?")
        cats[cat] = cats.get(cat, 0) + 1
        for d in c.get("relevant_docs", []):
            if d and d not in docs:
                print(f"  ✗ {cid}: relevant_doc 不存在: {d}"); problems += 1

        is_derived = cid.endswith(DERIVED_SUFFIX) or cid.startswith("global_")
        if cat in EXEMPT_CATS or is_derived:
            exempt += 1
            continue
        toks = c.get("must_contain_any", [])
        if toks and not any(t in corpus for t in toks):
            print(f"  ✗ {cid} ({cat}): {toks} 均未在语料中找到")
            problems += 1
        else:
            grounded += 1

    print(f"\n语料文档: {len(docs)} | 用例: {len(cases)}")
    print(f"分类: {dict(sorted(cats.items()))}")
    print(f"语料内可答(正例): {grounded} | 豁免(拒答/对抗/招呼/派生): {exempt} | 问题: {problems}")
    print("✅ 自洽:每条正例都能从语料答出" if problems == 0 else "❌ 存在不一致")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
