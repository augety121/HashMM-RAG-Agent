"""hashmm/training/merge_golden.py — 合并多个 golden_cases.json 并按问题去重。

推荐用法：把你的企业题（保领域、最值钱）和公开中文题（补量、补多样性）合到一起训。
企业题排在前面、优先保留；公开题里与之重复的问题会被去掉。

用法：
    python -m hashmm.training.merge_golden \
        /root/autodl-tmp/data/golden/golden_cases_corpus.json \
        /root/autodl-tmp/data/golden/golden_cmrc2018.json \
        --out /root/autodl-tmp/data/golden/golden_merged.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _norm(q: str) -> str:
    return "".join((q or "").split()).lower()


def load(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("cases", data) if isinstance(data, dict) else data


def merge(paths: list[str], repeat_first: int = 1) -> tuple[list[dict], dict]:
    """合并多个 golden 文件、按问题去重，靠前的优先保留。

    repeat_first：把**第一个文件**（你的企业题，最值钱）复制几份，避免被后面的大公开集稀释。
    例如企业题 299 条、公开题上万条，repeat_first=10 → 企业题在训练集里出现 ~2990 次，占比更合理。
    去重只在「不同问题」之间做；过采样是同一批企业题重复多份（这是有意的，用于加权）。
    """
    seen: set[str] = set()
    merged: list[dict] = []
    stats: dict = {}
    for i, p in enumerate(paths):
        cases = load(p)
        kept = 0
        unique_here = []
        for c in cases or []:
            if not isinstance(c, dict):
                continue
            q = c.get("query") or c.get("question") or ""
            k = _norm(q)
            if not k or k in seen:
                continue
            seen.add(k)
            unique_here.append(c)
            kept += 1
        # 第一个文件按 repeat_first 过采样（其余文件正常 1 份）
        times = repeat_first if (i == 0 and repeat_first > 1) else 1
        for _ in range(times):
            merged.extend(unique_here)
        stats[p] = {"total": len(cases or []), "kept": kept, "repeated": times}
    return merged, stats


def main():
    ap = argparse.ArgumentParser(description="合并去重多个 golden_cases.json")
    ap.add_argument("inputs", nargs="+", help="多个 golden json，靠前的优先保留")
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeat", type=int, default=1,
                    help="把第一个文件（企业题）过采样几份，避免被大公开集稀释。建议 5-10")
    args = ap.parse_args()

    merged, stats = merge(args.inputs, repeat_first=args.repeat)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    for p, s in stats.items():
        tag = f"（过采样 x{s['repeated']}）" if s.get("repeated", 1) > 1 else ""
        print(f"  {p}: 取 {s['kept']}/{s['total']} {tag}")
    print(f"[merge_golden] 合并去重后共 {len(merged)} 条 → {out}")


if __name__ == "__main__":
    main()
