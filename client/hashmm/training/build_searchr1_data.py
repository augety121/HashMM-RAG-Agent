"""hashmm/training/build_searchr1_data.py — P2 第一步：构造 Search-R1 训练数据。

把两类来源统一转成 Search-R1 官方训练格式（每条一个 dict），写成 train.parquet / test.parquet：

  来源 A（推荐冷启动）：公开多跳 QA。直接用 HuggingFace 上 Search-R1 官方训练集
                        PeterJinGo/nq_hotpotqa_train（NQ + HotpotQA 已转好格式）。
  来源 B（护城河）：你的企业金标准集 —— 即 hashmm.evaluation.casegen 产出的
                  golden_cases.json：[{"query","reference_answer", ...}, ...]。

Search-R1 官方数据格式（已核对 github.com/PeterGriffinJin/Search-R1 README + scripts/data_process/nq_search.py）：
    {
      "data_source": <str>,
      "prompt": [{"role": "user", "content": <question 文本，含指令模板>}],
      "ability": "fact-reasoning",
      "reward_model": {"style": "rule", "ground_truth": {"target": [<答案别名...>]}},
      "extra_info": {"split": <train|test>, "index": <int>},
    }

用法（在你的 GPU 服务器上）：
    # A. 直接用官方公开训练集（最省事，先把链路跑通）
    #    huggingface-cli download PeterJinGo/nq_hotpotqa_train --repo-type dataset --local-dir data/nq_hotpotqa_train
    #    官方该集已是上面的格式，可直接喂训练，无需本脚本。

    # B. 用你的企业金标准集转出可训练 parquet：
    python -m hashmm.training.build_searchr1_data \
        --golden data/eval/golden_cases.json \
        --out_dir data/hashmm_searchr1 \
        --test_ratio 0.1

依赖：pandas + pyarrow（写 parquet，Search-R1/veRL 读 parquet）。脚本本身不依赖 GPU。
"""
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path
from typing import Any


# Search-R1 用的标准问题模板：要求模型在 <think> 里推理、用 <search> 触发检索、
# <information> 包检索结果、最终 <answer> 给答案。与官方 nq_search.py 一致（中文化说明，
# 标签保持英文以匹配训练时的 reward 解析）。
_QUESTION_TEMPLATE = (
    "Answer the given question. You must conduct reasoning inside <think> and </think> "
    "first every time you get new information. After reasoning, if you find you lack some "
    "knowledge, you can call a search engine by <search> query </search>, and it will return "
    "the top searched results between <information> and </information>. You can search as many "
    "times as you want. If you find no further external knowledge needed, you can directly "
    "provide the answer inside <answer> and </answer> without detailed illustrations. For "
    "example, <answer> Beijing </answer>. Question: {question}\n"
)


def _norm_answers(reference_answer: Any) -> list[str]:
    """把参考答案规整成「别名列表」（Search-R1 的 ground_truth.target 是答案别名数组）。"""
    if reference_answer is None:
        return []
    if isinstance(reference_answer, (list, tuple)):
        return [str(a).strip() for a in reference_answer if str(a).strip()]
    s = str(reference_answer).strip()
    return [s] if s else []


def _to_searchr1_record(query: str, reference_answer: Any, *, data_source: str,
                        split: str, index: int) -> dict:
    """构造单条 Search-R1 训练样本。纯函数。"""
    return {
        "data_source": data_source,
        "prompt": [{"role": "user", "content": _QUESTION_TEMPLATE.format(question=query.strip())}],
        "ability": "fact-reasoning",
        "reward_model": {"style": "rule", "ground_truth": {"target": _norm_answers(reference_answer)}},
        "extra_info": {"split": split, "index": index},
    }


def load_golden_cases(path: str) -> list[dict]:
    """读 hashmm.evaluation.casegen 产出的 golden_cases.json。

    兼容两种形态：直接是列表，或 {"cases":[...]}。每条至少含 query 与
    reference_answer（casegen 的字段名）；缺答案的条目跳过（训练需要 ground truth）。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"金标准文件不存在：{path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    cases = data.get("cases", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for c in cases or []:
        if not isinstance(c, dict):
            continue
        q = (c.get("query") or c.get("question") or "").strip()
        a = c.get("reference_answer", c.get("answer"))
        if q and _norm_answers(a):
            out.append({"query": q, "reference_answer": a})
    return out


def build_from_golden(golden_path: str, out_dir: str, *, test_ratio: float = 0.1,
                      seed: int = 42, data_source: str = "hashmm_enterprise") -> dict:
    """企业金标准集 → train/test parquet（Search-R1 格式）。返回统计。"""
    import pandas as pd  # 延迟导入：只有真正建数据时才需要 pandas/pyarrow

    cases = load_golden_cases(golden_path)
    if not cases:
        raise ValueError("没有可用的金标准样本（需要 query + reference_answer）。先用 casegen 生成并人工校验。")

    rng = random.Random(seed)
    rng.shuffle(cases)
    n_test = max(1, int(len(cases) * test_ratio)) if len(cases) > 10 else 0
    test_cases, train_cases = cases[:n_test], cases[n_test:]

    def _build(split_cases: list[dict], split: str) -> list[dict]:
        return [_to_searchr1_record(c["query"], c["reference_answer"],
                                    data_source=data_source, split=split, index=i)
                for i, c in enumerate(split_cases)]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    train_records = _build(train_cases, "train")
    pd.DataFrame(train_records).to_parquet(out / "train.parquet")
    stats = {"train": len(train_records), "test": 0, "out_dir": str(out)}
    if test_cases:
        test_records = _build(test_cases, "test")
        pd.DataFrame(test_records).to_parquet(out / "test.parquet")
        stats["test"] = len(test_records)
    return stats


def main():
    ap = argparse.ArgumentParser(description="构造 Search-R1 训练数据（企业金标准集 → parquet）")
    ap.add_argument("--golden", required=True, help="金标准 JSON 路径（casegen 产出，含 query+reference_answer）")
    ap.add_argument("--out_dir", default="data/hashmm_searchr1", help="输出目录（写 train/test.parquet）")
    ap.add_argument("--test_ratio", type=float, default=0.1)
    ap.add_argument("--data_source", default="hashmm_enterprise")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    stats = build_from_golden(args.golden, args.out_dir, test_ratio=args.test_ratio,
                              seed=args.seed, data_source=args.data_source)
    print(f"[build_searchr1_data] 完成：train={stats['train']} test={stats['test']} → {stats['out_dir']}")
    print("下一步：python -m hashmm.training.load_and_train --data_dir", stats["out_dir"])


if __name__ == "__main__":
    main()
