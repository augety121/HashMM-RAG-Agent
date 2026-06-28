"""hashmm/training/build_all_data.py — 一键把「下载多个公开集 + 企业题 → 合并(过采样) → SFT」串好。

省得一条条手敲。它依次：
  1. 下载并转换你选的公开集（cmrc2018 / dureader / multidoc，可多选）；
  2. 和你的企业金标准集合并去重，企业题按 --repeat 过采样（避免被大公开集稀释）；
  3. 转成 SFT 训练数据（retrieval 风格，教先检索再答）；
  4. 打印最后的训练命令（训练耗时较长，留给你单独跑，可控）。

用法（AutoDL，项目根目录；国内先 export HF_ENDPOINT=https://hf-mirror.com）：
    python -m hashmm.training.build_all_data \
        --enterprise /root/autodl-tmp/data/golden/golden_cases_corpus.json \
        --datasets cmrc2018 dureader \
        --limit 6000 --repeat 10 \
        --work_dir /root/autodl-tmp/data

  想把 multidoc 也加上（自动下载，需先 huggingface-cli login 同意条款）：
    ... --datasets cmrc2018 dureader multidoc ...
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description="一键：下载公开集 + 企业题 → 合并(过采样) → SFT")
    ap.add_argument("--enterprise", required=True, help="你的企业金标准 json（最值钱，会过采样）")
    ap.add_argument("--datasets", nargs="+", default=["cmrc2018", "dureader"],
                    choices=["cmrc2018", "dureader", "drcd", "multidoc"], help="要加的公开集")
    ap.add_argument("--limit", type=int, default=6000, help="每个公开集最多取多少条")
    ap.add_argument("--subset", default="robust", help="dureader 子集 robust/checklist")
    ap.add_argument("--repeat", type=int, default=10, help="企业题过采样份数（建议 5-10）")
    ap.add_argument("--work_dir", default="/root/autodl-tmp/data", help="工作目录")
    ap.add_argument("--multidoc_dir", default="/root/autodl-tmp/data/multidoc_raw")
    args = ap.parse_args()

    from hashmm.training.fetch_public_zh_data import (
        from_cmrc2018, from_dureader, from_drcd, from_multidoc,
    )
    from hashmm.training.merge_golden import merge
    from hashmm.training.build_sft_data import build as build_sft
    import json

    work = Path(args.work_dir)
    golden_dir = work / "golden"
    golden_dir.mkdir(parents=True, exist_ok=True)

    # 1. 下载 + 转换各公开集（单个失败就跳过，不让整个流程崩）
    public_files = []
    failed = []
    for name in args.datasets:
        print(f"\n[build_all] 处理公开集：{name} ...")
        try:
            if name == "cmrc2018":
                cases = from_cmrc2018(args.limit)
            elif name == "dureader":
                cases = from_dureader(args.limit, args.subset)
            elif name == "drcd":
                cases = from_drcd(args.limit)
            else:
                cases = from_multidoc(args.limit, args.multidoc_dir)
        except Exception as e:
            print(f"[build_all] ⚠️  {name} 处理失败，已跳过（不影响其他）：{e}")
            failed.append(name)
            continue
        if not cases:
            print(f"[build_all] ⚠️  {name} 没解析出样本，跳过。")
            failed.append(name)
            continue
        fp = golden_dir / f"golden_{name}.json"
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(cases, f, ensure_ascii=False, indent=2)
        print(f"[build_all] {name}: {len(cases)} 条 → {fp}")
        public_files.append(str(fp))

    if not public_files:
        print("\n[build_all] 所有公开集都没成功。请检查网络/登录后重试；企业题仍可单独训练。")
        return
    if failed:
        print(f"\n[build_all] 注意：这些集被跳过：{failed}（其余照常合并）")

    # 2. 合并去重（企业题在最前、按 repeat 过采样）
    inputs = [args.enterprise] + public_files
    merged, stats = merge(inputs, repeat_first=args.repeat)
    merged_fp = golden_dir / "golden_all_merged.json"
    with open(merged_fp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"\n[build_all] 合并明细：")
    for p, s in stats.items():
        tag = f"（过采样 x{s['repeated']}）" if s.get("repeated", 1) > 1 else ""
        print(f"  {p}: 取 {s['kept']}/{s['total']} {tag}")
    print(f"[build_all] 合并后共 {len(merged)} 条 → {merged_fp}")

    # 3. 转 SFT（retrieval 风格）
    sft_dir = work / "sft_all"
    sft_stats = build_sft(str(merged_fp), str(sft_dir), val_ratio=0.1, style="retrieval")
    print(f"\n[build_all] SFT 数据：train={sft_stats['train']} val={sft_stats['val']} → {sft_dir}")

    # 4. 打印训练命令
    print("\n" + "=" * 64)
    print("数据已就绪。最后一步训练（耗时较长，单独执行）：")
    print(f"""
CUDA_VISIBLE_DEVICES=0 python -m hashmm.training.train_lora_4090 \\
    --model /root/autodl-tmp/models/Qwen2.5-7B-Instruct \\
    --data_dir {sft_dir} \\
    --out_dir /root/autodl-tmp/models/qwen2.5-7b-hashmm-all-lora --epochs 2
""")
    print("训练后验证（端到端真查知识库）：")
    print("""HASH_INDEX_DIR=/root/autodl-tmp/data/vector_index \\
CUDA_VISIBLE_DEVICES=0 python -m hashmm.training.run_trained_retrieval \\
    --model /root/autodl-tmp/models/Qwen2.5-7B-Instruct \\
    --lora /root/autodl-tmp/models/qwen2.5-7b-hashmm-all-lora""")
    print("=" * 64)


if __name__ == "__main__":
    main()
