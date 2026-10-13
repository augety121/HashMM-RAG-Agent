"""hashmm/training/train_everything.py — 一条命令把「全部数据 → 一个最终权重」跑完。

为什么要它：客户端只做推理、不训练，所以要把所有训练数据合到一起、训出**一个**最终 LoRA 权重，
线上加载它即可。本脚本不重写训练逻辑，只是把项目里已验证的几步串成一条命令：

  1) 【LLM 补题】用你配置的 LLM 从企业语料（那 5576 个 chunk）现生成 N 条中文 QA
     —— 这就是你说的「没有答案就用 LLM 找」。已有企业题不够时，这步把量补上来（最值钱的数据）。
  2) 【合并全部金标准】企业题排最前、按 --repeat 过采样（避免被公开集稀释），并入你已有的公开集
     （默认自动并入 work_dir/golden 下的 cmrc2018 / dureader），按问题去重。
  3) 【SFT】转成 retrieval 风格训练数据（教模型「先 <search> 检索、再基于证据 <answer>」）。
  4) 【训练一个权重】调用已验证的 train_lora_4090，产出单一最终 LoRA 权重。

用法（AutoDL，项目根目录；训练耗时长，强烈建议用 nohup 挂后台，断线也不丢）：
    nohup python -m hashmm.training.train_everything \
        --n_corpus 600 \
        --out_dir /root/autodl-tmp/models/qwen2.5-7b-hashmm-final \
        --epochs 2 > /root/autodl-tmp/train_all.log 2>&1 &
    tail -f /root/autodl-tmp/train_all.log

只想准备数据、训练自己另跑：加 --prep_only。
已有足够企业题、不想再调 LLM：加 --n_corpus 0（会用已有的 golden_cases_corpus.json）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _discover_public(golden_dir: Path) -> list[str]:
    """默认并入的公开集：work_dir/golden 下已存在的 cmrc2018 / dureader（有就用，没有就算）。"""
    out = []
    for name in ("golden_cmrc2018.json", "golden_dureader.json", "golden_drcd.json"):
        p = golden_dir / name
        if p.exists():
            out.append(str(p))
    return out


def main():
    ap = argparse.ArgumentParser(description="一条命令：全部数据 → 一个最终训练权重")
    ap.add_argument("--work_dir", default="/root/autodl-tmp/data")
    ap.add_argument("--model", default="/root/autodl-tmp/models/Qwen2.5-7B-Instruct")
    ap.add_argument("--out_dir", default="/root/autodl-tmp/models/qwen2.5-7b-hashmm-final",
                    help="最终 LoRA 权重输出目录（客户端/服务端推理就加载它）")
    ap.add_argument("--n_corpus", type=int, default=600,
                    help="用 LLM 从企业语料新生成多少条 QA（0=不生成，用已有的 golden_cases_corpus.json）")
    ap.add_argument("--multihop_n", type=int, default=0,
                    help="用 LLM 造多少道多跳(桥接/对比)题（0=不造）。>0 时这些题会和企业题一起被过采样，"
                         "是让模型驱动检索真正强过单次检索的关键数据。")
    ap.add_argument("--public", nargs="*", default=None,
                    help="要并入的公开集 golden 文件路径；默认自动用 work_dir/golden 下已有的 cmrc2018/dureader")
    ap.add_argument("--repeat", type=int, default=10, help="企业题过采样份数（5-10，避免被大公开集稀释）")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--max_len", type=int, default=2048, help="最大序列长度，OOM 就调小到 1024")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--index_dir", default="/root/autodl-tmp/data/vector_index",
                    help="向量索引目录（用于读企业语料 chunk）；脚本会据此自动设好 HASH_INDEX_DIR")
    ap.add_argument("--prep_only", action="store_true", help="只准备数据，不训练（训练你用 nohup 单独跑）")
    args = ap.parse_args()

    # 关键：步骤①要从知识库读语料 chunk，必须设好 HASH_INDEX_DIR（否则报“没读到语料 chunk”）。
    # 这里自动设上，省得你每次手动加前缀；你若已在环境里设了，则尊重你的设置。
    os.environ.setdefault("HASH_INDEX_DIR", args.index_dir)
    print(f"[train_all] HASH_INDEX_DIR = {os.environ.get('HASH_INDEX_DIR')}")

    golden_dir = Path(args.work_dir) / "golden"
    golden_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1) LLM 补题：从企业语料生成 QA ----
    corpus_fp = golden_dir / "golden_corpus_auto.json"
    if args.n_corpus and args.n_corpus > 0:
        print(f"[train_all] 1/4 用 LLM 从企业语料生成 {args.n_corpus} 条 QA（没有答案就让 LLM 找）...")
        try:
            from hashmm.training.gen_golden_from_corpus import _load_corpus_chunks, _get_llm_fn
            from hashmm.evaluation.casegen import generate_cases_from_chunks
        except Exception as e:
            print(f"[train_all] ✗ 导入生成模块失败：{e}")
            return
        chunks = _load_corpus_chunks()
        if not chunks:
            print("[train_all] ✗ 没读到企业语料 chunk（确认在项目根目录运行、索引已建好）。")
            return
        llm_fn = _get_llm_fn()
        cases = generate_cases_from_chunks(chunks, llm_fn, n=args.n_corpus, seed=args.seed)
        if not cases:
            print("[train_all] ✗ LLM 没生成出题目（检查后台 API Key / Base URL）。")
            return
        corpus_fp.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[train_all]   生成 {len(cases)} 条 → {corpus_fp}")
    else:
        alt = golden_dir / "golden_cases_corpus.json"
        if corpus_fp.exists():
            print(f"[train_all] 1/4 跳过生成，用已有 {corpus_fp}")
        elif alt.exists():
            corpus_fp = alt
            print(f"[train_all] 1/4 跳过生成，用已有 {corpus_fp}")
        else:
            print("[train_all] ✗ --n_corpus=0 但找不到已有企业题（golden_corpus_auto.json / golden_cases_corpus.json）。")
            return

    # ---- 1.5) 可选：用 LLM 造多跳(桥接/对比)题（让模型驱动检索真正强过单次检索的关键数据）----
    multihop_fp = None
    if args.multihop_n and args.multihop_n > 0:
        print(f"[train_all] 1.5/4 用 LLM 造 {args.multihop_n} 道多跳(桥接/对比)题 ...")
        try:
            from hashmm.training.gen_golden_from_corpus import _load_corpus_chunks, _get_llm_fn
            from hashmm.training.gen_multihop_golden import generate_multihop
            _mhc = _load_corpus_chunks()
            mh = generate_multihop(_mhc, _get_llm_fn(), n=args.multihop_n, seed=args.seed) if _mhc else []
        except Exception as e:  # noqa: BLE001
            print(f"[train_all]   多跳生成异常，跳过：{e}")
            mh = []
        if mh:
            multihop_fp = golden_dir / "golden_multihop.json"
            multihop_fp.write_text(json.dumps(mh, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[train_all]   生成 {len(mh)} 道多跳题 → {multihop_fp}")
        else:
            print("[train_all]   未生成出多跳题（检查 LLM 配置），本次不含多跳数据。")

    # ---- 2) 合并全部金标准（企业题+多跳题在前、一起过采样；并入公开集补量、按问题去重）----
    print("[train_all] 2/4 合并全部金标准 ...")
    public = args.public if args.public is not None else _discover_public(golden_dir)
    try:
        from hashmm.training.merge_golden import merge
    except Exception as e:
        print(f"[train_all] ✗ 导入 merge_golden 失败：{e}")
        return
    # 先把企业题与多跳题合成一组（不过采样、去重），再整体过采样后并入公开集，
    # 保证多跳题和企业题一起被放大、不被大公开集稀释。
    if multihop_fp is not None:
        ent_merged, _ = merge([str(corpus_fp), str(multihop_fp)], repeat_first=1)
        first_fp = golden_dir / "golden_enterprise.json"
        first_fp.write_text(json.dumps(ent_merged, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[train_all]   企业题+多跳题合为 {len(ent_merged)} 条（将整体过采样 x{args.repeat}）")
    else:
        first_fp = corpus_fp
    inputs = [str(first_fp)] + list(public)
    merged, stats = merge(inputs, repeat_first=args.repeat)
    if not merged:
        print("[train_all] ✗ 合并后为空，检查输入文件。")
        return
    merged_fp = golden_dir / "golden_final.json"
    merged_fp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    for p, s in stats.items():
        tag = f"（过采样 x{s['repeated']}）" if s.get("repeated", 1) > 1 else ""
        print(f"   {p}: 取 {s['kept']}/{s['total']} {tag}")
    print(f"[train_all]   合并去重后共 {len(merged)} 条 → {merged_fp}")

    # ---- 3) 构建 SFT（retrieval 风格）----
    print("[train_all] 3/4 构建 SFT 训练数据 ...")
    try:
        from hashmm.training.build_sft_data import build as build_sft
    except Exception as e:
        print(f"[train_all] ✗ 导入 build_sft_data 失败：{e}")
        return
    sft_dir = Path(args.work_dir) / "sft_final"
    st = build_sft(str(merged_fp), str(sft_dir), val_ratio=0.1, style="retrieval")
    print(f"[train_all]   train={st.get('train')} val={st.get('val')} → {sft_dir}")

    # ---- 4) 训练一个最终权重（调用已验证的 train_lora_4090）----
    train_cmd = [sys.executable, "-m", "hashmm.training.train_lora_4090",
                 "--model", args.model, "--data_dir", str(sft_dir),
                 "--out_dir", args.out_dir, "--epochs", str(args.epochs),
                 "--max_len", str(args.max_len)]
    if args.prep_only:
        print("\n[train_all] --prep_only：数据已就绪。训练请单独跑（建议 nohup 挂后台）：")
        print("  CUDA_VISIBLE_DEVICES=0 " + " ".join(train_cmd))
        return
    print("[train_all] 4/4 开始训练最终权重（耗时较长，建议整条命令用 nohup 挂后台）...")
    print("  CUDA_VISIBLE_DEVICES=0 " + " ".join(train_cmd))
    ret = subprocess.run(train_cmd).returncode
    if ret != 0:
        print(f"[train_all] ✗ 训练子进程退出码 {ret}。OOM 的话重跑加 --max_len 1024。")
        return
    print(f"\n[train_all] ✓ 完成。最终权重 → {args.out_dir}")
    print("[train_all] 验证（held-out + 判官，客户端推理同款驱动）：")
    print(f"  HASH_INDEX_DIR=/root/autodl-tmp/data/vector_index CUDA_VISIBLE_DEVICES=0 \\")
    print(f"  python -m hashmm.training.eval_retrieval_policy --model {args.model} \\")
    print(f"      --lora {args.out_dir} --golden {golden_dir}/golden_eval_holdout.json \\")
    print(f"      --limit 50 --top_k 5 --max_hops 3 --max_new 384 --driver searchfirst --judge llm --dump 5 \\")
    print(f"      --out /root/autodl-tmp/data/eval/eval_final.json")


if __name__ == "__main__":
    main()
