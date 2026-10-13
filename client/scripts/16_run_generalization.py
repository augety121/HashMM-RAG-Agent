#!/usr/bin/env python3
"""16 — Run HashMM-RAG benchmark across multiple ViDoRe v2 subsets.

Validates generalization beyond the single biomedical_lectures_eng_v2 dataset.

Usage:
    HASH_BITS=256 python scripts/16_run_generalization.py --ocr paddleocr

This runs the 256-bit HashMM-RAG vs BGE-M3 on all available ViDoRe v2 subsets
and produces a combined report.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

VIDORE_V2_SUBSETS = [
    "biomedical_lectures_eng_v2",
    "economics_reports_v2",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--subsets", nargs="*", default=VIDORE_V2_SUBSETS)
    ap.add_argument("--ocr", default="paddleocr")
    ap.add_argument("--out-dir", default="benchmarks")
    args = ap.parse_args()

    results = {}
    for subset in args.subsets:
        print(f"\n{'='*60}")
        print(f"Running: {subset}")
        print(f"{'='*60}\n")

        cmd = [
            sys.executable, "scripts/09_run_vidore.py",
            "--dataset", subset,
            "--retriever", "both",
            "--ocr", args.ocr,
            "--ocr-workers", "1",
        ]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"FAILED on {subset}: {e}")
            continue

        # Load results
        for model in ["BGE-M3-dense", "HashMM-RAG"]:
            json_path = Path(args.out_dir) / f"{subset}_{model}.json"
            if json_path.exists():
                with open(json_path) as f:
                    data = json.load(f)
                results.setdefault(subset, {})[model] = data.get("metrics", {})

    # Print combined table
    print(f"\n\n{'='*80}")
    print("GENERALIZATION REPORT — HashMM-RAG vs BGE-M3 across ViDoRe v2 subsets")
    print(f"{'='*80}\n")

    print(f"{'Dataset':<35} {'Model':<15} {'nDCG@5':>8} {'nDCG@10':>9} "
          f"{'R@10':>7} {'MAP':>7}")
    print("-" * 85)

    for subset in args.subsets:
        if subset not in results:
            print(f"{subset:<35} {'FAILED':<15}")
            continue
        for model in ["BGE-M3-dense", "HashMM-RAG"]:
            if model not in results[subset]:
                continue
            m = results[subset][model]
            print(f"{subset:<35} {model:<15} "
                  f"{m.get('ndcg_cut_5', 0):.4f}   "
                  f"{m.get('ndcg_cut_10', 0):.4f}    "
                  f"{m.get('recall_10', 0):.4f}  "
                  f"{m.get('map', 0):.4f}")
        print()

    # Compute averages
    avg = {"BGE-M3-dense": {}, "HashMM-RAG": {}}
    for model in avg:
        metrics_lists = {}
        for subset in results:
            if model in results[subset]:
                for k, v in results[subset][model].items():
                    metrics_lists.setdefault(k, []).append(v)
        for k, vals in metrics_lists.items():
            avg[model][k] = sum(vals) / len(vals) if vals else 0

    print(f"{'AVERAGE':<35} {'BGE-M3-dense':<15} "
          f"{avg['BGE-M3-dense'].get('ndcg_cut_5', 0):.4f}   "
          f"{avg['BGE-M3-dense'].get('ndcg_cut_10', 0):.4f}    "
          f"{avg['BGE-M3-dense'].get('recall_10', 0):.4f}  "
          f"{avg['BGE-M3-dense'].get('map', 0):.4f}")
    print(f"{'AVERAGE':<35} {'HashMM-RAG':<15} "
          f"{avg['HashMM-RAG'].get('ndcg_cut_5', 0):.4f}   "
          f"{avg['HashMM-RAG'].get('ndcg_cut_10', 0):.4f}    "
          f"{avg['HashMM-RAG'].get('recall_10', 0):.4f}  "
          f"{avg['HashMM-RAG'].get('map', 0):.4f}")

    ratio = (avg['HashMM-RAG'].get('ndcg_cut_5', 0) /
             max(avg['BGE-M3-dense'].get('ndcg_cut_5', 0), 1e-9))
    print(f"\nHashMM-RAG achieves {ratio*100:.1f}% of BGE-M3 nDCG@5 (average)")


if __name__ == "__main__":
    main()
