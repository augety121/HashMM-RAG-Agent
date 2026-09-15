"""hashmm/training/load_and_train.py — P2 第二步：读取/校验训练数据 + 给出 Qwen2.5-7B-Instruct 训练命令。

Search-R1 的 RL 训练本体由 veRL 驱动（github.com/PeterGriffinJin/Search-R1，基于 veRL），需要在你的
GPU 服务器上、按它的 conda 环境跑。本脚本做两件**能在任何机器先跑、避免白烧 GPU**的事：
  1. 读取 + 严格校验 build_searchr1_data.py 产出的 parquet（字段齐不齐、答案非空、prompt 格式对不对）；
  2. 打印在 Qwen2.5-7B-Instruct 上训练的**完整、已核对的命令**（含检索服务、PPO/GRPO 关键超参）。

用法：
    python -m hashmm.training.load_and_train --data_dir data/hashmm_searchr1
    python -m hashmm.training.load_and_train --data_dir data/hashmm_searchr1 --print_cmd_only
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


# ── 校验：在烧 GPU 前先确认数据格式没问题 ──────────────────────────────
_REQUIRED_TOP = ("data_source", "prompt", "ability", "reward_model", "extra_info")


def validate_record(rec: dict) -> list[str]:
    """校验单条 Search-R1 样本，返回问题列表（空 = 合格）。纯函数、不抛错。"""
    errs: list[str] = []
    try:
        for k in _REQUIRED_TOP:
            if k not in rec:
                errs.append(f"缺字段 {k}")
        prompt = rec.get("prompt")
        if not isinstance(prompt, list) or not prompt or not isinstance(prompt[0], dict):
            errs.append("prompt 必须是非空的消息列表")
        elif prompt[0].get("role") != "user" or not str(prompt[0].get("content", "")).strip():
            errs.append("prompt[0] 必须是非空的 user 消息")
        rm = rec.get("reward_model", {})
        if not isinstance(rm, dict) or rm.get("style") != "rule":
            errs.append("reward_model.style 必须为 'rule'")
        else:
            gt = rm.get("ground_truth", {})
            target = gt.get("target") if isinstance(gt, dict) else None
            if not isinstance(target, list) or not [t for t in target if str(t).strip()]:
                errs.append("ground_truth.target 必须是非空答案别名列表")
    except Exception as e:  # 校验自身绝不抛错
        errs.append(f"校验异常：{e}")
    return errs


def validate_parquet(path: str, *, sample_limit: int = 0) -> dict:
    """读 parquet 并逐条校验。返回 {n, n_bad, examples_bad, ok}。"""
    import pandas as pd
    p = Path(path)
    if not p.exists():
        return {"n": 0, "n_bad": 0, "examples_bad": [f"文件不存在：{path}"], "ok": False}
    df = pd.read_parquet(p)
    records = df.to_dict("records")
    if sample_limit:
        records = records[:sample_limit]
    bad: list[str] = []
    for i, rec in enumerate(records):
        # parquet 里嵌套字段读回来可能是 numpy 类型，转成原生 dict 再校验
        errs = validate_record(_to_native(rec))
        if errs:
            bad.append(f"#{i}: {'; '.join(errs)}")
    return {"n": len(records), "n_bad": len(bad), "examples_bad": bad[:10], "ok": not bad}


def _to_native(obj: Any) -> Any:
    """把 numpy / pandas 容器递归转成原生 python（校验用）。"""
    try:
        import numpy as np
        if isinstance(obj, np.ndarray):
            return [_to_native(x) for x in obj.tolist()]
        if isinstance(obj, np.generic):
            return obj.item()
    except Exception:
        pass
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(x) for x in obj]
    return obj


# ── 训练命令（已核对 Search-R1 官方 README + issue #142 的 Qwen2.5-7B-Instruct 配置）──
_TRAIN_CMD = r"""
# ============ 在你的 GPU 服务器上执行（多卡，7B 建议 ≥4×A100/H100 80G）============
# 0) 拿到 Search-R1 框架本体（基于 veRL）
git clone https://github.com/PeterGriffinJin/Search-R1.git && cd Search-R1
# 按它 README 建两个 conda 环境：searchr1（训练）与 searchr1-retriever（检索服务）

# 1) 准备检索语料 + 索引（官方 NQ/HotpotQA 用 2018 维基 dump + E5）
save_path=./data/corpus
python scripts/download.py --save_path $save_path
cat $save_path/part_* > $save_path/e5_Flat.index
gzip -d $save_path/wiki-18.jsonl.gz
# 想用「你自己的企业语料」当检索源：把它做成 wiki-18.jsonl 同样的 jsonl（每行 {"id","contents"}），
# 再用官方 search/index_builder 建 e5 索引；或直接把 retrieval_server 指向你 HashMM 的检索后端。

# 2) 起本地稠密检索服务（E5），训练时模型的 <search> 会打到这里
conda activate searchr1-retriever
python search/retrieval_server.py \
  --index_path $save_path/e5_Flat.index \
  --corpus_path $save_path/wiki-18.jsonl \
  --topk 3 --retriever_name e5 --retriever_model intfloat/e5-base-v2 --faiss_gpu &

# 3) 训练数据：二选一
#   (A) 官方公开训练集（先把链路跑通）：
#       huggingface-cli download PeterJinGo/nq_hotpotqa_train --repo-type dataset --local-dir data/nq_hotpotqa_train
#   (B) 你的企业集（护城河）：用 hashmm.training.build_searchr1_data 产出的 data/hashmm_searchr1
export DATA_DIR=data/hashmm_searchr1   # 或 data/nq_hotpotqa_train

# 4) 在 Qwen2.5-7B-Instruct 上做 PPO 训练（outcome reward + retrieved-token masking）
conda activate searchr1
export CUDA_VISIBLE_DEVICES=0,1,2,3
export BASE_MODEL='Qwen/Qwen2.5-7B-Instruct'
export EXPERIMENT_NAME=hashmm-searchr1-ppo-qwen2.5-7b-it
export WAND_PROJECT="Search-R1"
export NCCL_P2P_DISABLE=1
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"
# 官方提供 train_ppo.sh / train_grpo.sh；按其参数运行即可。要点：
#   · data.train_files=$DATA_DIR/train.parquet  data.val_files=$DATA_DIR/test.parquet
#   · actor_rollout_ref.model.path=$BASE_MODEL
#   · 启用 retrieved-token masking（官方默认开，勿关，否则训练不稳）
#   · 奖励用 rule-based outcome（答案 EM/子串匹配 ground_truth.target）
#   · PPO：lr≈1e-6，kl_coef≈0.001，rollout.n≈5，max_turns≈4（多跳轮数上限）
bash train_ppo.sh    # 或 bash train_grpo.sh（GRPO 更省显存）

# 5) 训练产出的 checkpoint = 你专属的检索策略模型。回填 HashMM：
#    用它作为 AgenticRetriever 的 llm_fn（给证据摘要、返回 search/finish 决策）即可平滑替换。
""".strip()


def print_training_guide(data_dir: str) -> None:
    print("=" * 72)
    print("Qwen2.5-7B-Instruct 上的 Search-R1 训练步骤（已核对官方仓库）")
    print("=" * 72)
    print(_TRAIN_CMD.replace("data/hashmm_searchr1", data_dir))


def main():
    ap = argparse.ArgumentParser(description="P2：校验训练数据 + 打印 Qwen2.5-7B-Instruct 训练命令")
    ap.add_argument("--data_dir", default="data/hashmm_searchr1", help="含 train.parquet / test.parquet 的目录")
    ap.add_argument("--print_cmd_only", action="store_true", help="只打印训练命令，不校验数据")
    args = ap.parse_args()

    if not args.print_cmd_only:
        for split in ("train", "test"):
            fp = Path(args.data_dir) / f"{split}.parquet"
            if not fp.exists():
                print(f"[load_and_train] 跳过 {split}（{fp} 不存在）")
                continue
            r = validate_parquet(str(fp))
            tag = "✅" if r["ok"] else "❌"
            print(f"{tag} {split}.parquet：{r['n']} 条，不合格 {r['n_bad']} 条")
            for e in r["examples_bad"]:
                print("    -", e)
        print()
    print_training_guide(args.data_dir)


if __name__ == "__main__":
    main()
