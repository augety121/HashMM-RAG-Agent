"""hashmm/training/build_sft_data.py — 把金标准集转成「SFT 训练样本」（单卡 4090 可训）。

为什么单独有这个：Search-R1 的强化学习（PPO/GRPO）单张 4090（24G）跑不动 7B —— RL 显存里要
同时塞 actor+reference+critic+reward 四份模型，7B 需要多张 A100/H100 80G。单卡能落地的正路是
**LoRA + 4bit 量化的监督微调（SFT）**：用「问题 → 期望的推理+检索+答案轨迹」成对数据，教模型学会
按 Search-R1 的 <think>/<search>/<information>/<answer> 格式去思考与检索。

本脚本把企业金标准集（hashmm.evaluation.casegen 产出的 golden_cases.json：含 query + reference_answer，
可选 supporting / context）转成 SFT 的「对话对」，写成 train.jsonl / val.jsonl（TRL SFTTrainer 直接读）。

每条样本形如：
  {"messages": [
     {"role": "user",      "content": <含 Search-R1 指令模板的问题>},
     {"role": "assistant", "content": "<think>...</think><answer>参考答案</answer>"}
  ]}
若金标准里带 supporting（支持段落），会构造一条「先 search 再 answer」的两步轨迹，让模型学会
「不确定就检索」；没有 supporting 就退化成「直接想清楚再答」。

用法（在你的 AutoDL 4090 上）：
    python -m hashmm.training.build_sft_data \
        --golden /root/autodl-tmp/data/golden/golden_cases.json \
        --out_dir /root/autodl-tmp/data/sft \
        --val_ratio 0.1
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

# 与 build_searchr1_data.py 同一套 Search-R1 指令模板（保持训练/推理一致）。
_USER_TEMPLATE = (
    "Answer the given question. You must conduct reasoning inside <think> and </think> "
    "first every time you get new information. After reasoning, if you find you lack some "
    "knowledge, you can call a search engine by <search> query </search>, and it will return "
    "the top searched results between <information> and </information>. You can search as many "
    "times as you want. If you find no further external knowledge needed, you can directly "
    "provide the answer inside <answer> and </answer> without detailed illustrations. For "
    "example, <answer> Beijing </answer>. Question: {question}\n"
)


def _first_answer(reference_answer: Any) -> str:
    if isinstance(reference_answer, (list, tuple)):
        for a in reference_answer:
            if str(a).strip():
                return str(a).strip()
        return ""
    return str(reference_answer or "").strip()


def _supporting_text(case: dict, limit: int = 2) -> str:
    """从金标准条目里取支持段落（兼容多种字段名）。没有就返回空串。"""
    for key in ("supporting", "support", "evidence", "contexts", "context", "passages"):
        v = case.get(key)
        if isinstance(v, (list, tuple)) and v:
            parts = [str(x).strip() for x in v if str(x).strip()]
            return " ".join(parts[:limit])[:600]
        if isinstance(v, str) and v.strip():
            return v.strip()[:600]
    return ""


def build_assistant_target(question: str, answer: str, supporting: str, style: str = "retrieval") -> str:
    """构造期望的助手轨迹（监督信号）。

    style="retrieval"（默认，推荐）：教模型**先检索再答**——即使是事实题也先发 <search>。
      这样训练出的模型遇到问题会先查知识库；配合 searchr1_policy 桥接器，<search> 会真的打到
      你的 BGE-M3+FAISS 后端，基于真实证据作答，而不是凭记忆脑补（解决「模型自己编数字」）。
      有 supporting 段落就用真段落当检索结果示范；没有就用参考答案当作检索到的证据来示范流程。
    style="direct"：旧行为，无支持段落时直接答（适合纯事实问答、不想引入检索开销的场景）。
    """
    if style == "direct" and not supporting:
        return (
            f"<think>这个问题可以根据已有知识直接作答：{question}</think>\n"
            f"<answer>{answer}</answer>"
        )
    # retrieval 风格（默认）：演示 think → search → information → think → answer
    evidence = supporting if supporting else answer
    return (
        f"<think>要回答「{question}」，我需要先检索知识库获取依据，不能凭记忆。</think>\n"
        f"<search>{question}</search>\n"
        f"<information>{evidence}</information>\n"
        f"<think>根据检索到的资料，已经可以基于证据作答了。</think>\n"
        f"<answer>{answer}</answer>"
    )


def build_multihop_target(question: str, answer: str, hops: list) -> str:
    """构造**多跳**助手轨迹：依次 search→information 若干轮，再综合作答。

    hops: ``[{"subq": 子问题, "evidence": 该跳检索到的证据}, ...]``（≥2 跳）。
    教模型把复杂问题拆成多次检索、每跳用真实证据、最后综合——这是单跳 SFT 给不了的能力，
    也是 HASHMM_SEARCHR1 那条路真正比单次检索强的来源。
    """
    parts = [f"<think>要回答「{question}」需要分步查证，先查第一项。</think>"]
    n = len(hops)
    for i, h in enumerate(hops, 1):
        subq = (h.get("subq") or h.get("query") or h.get("question") or question).strip()
        ev = (h.get("evidence") or h.get("text") or h.get("information") or "").strip()
        parts.append(f"<search>{subq}</search>")
        parts.append(f"<information>{ev}</information>")
        if i < n:
            parts.append(f"<think>已拿到第 {i} 项依据，还需继续查第 {i + 1} 项。</think>")
        else:
            parts.append("<think>各项依据已齐，可以综合作答了。</think>")
    parts.append(f"<answer>{answer}</answer>")
    return "\n".join(parts)


def case_to_sft(case: dict, style: str = "retrieval") -> dict | None:
    """单条金标准 → SFT 对话对。缺问题/答案返回 None。

    若 case 含 ``hops``（≥2 跳）则自动生成**多跳**目标（与 style 无关），否则按 style 走单跳。
    """
    q = (case.get("query") or case.get("question") or "").strip()
    a = _first_answer(case.get("reference_answer", case.get("answer")))
    if not q or not a:
        return None
    hops = case.get("hops")
    if isinstance(hops, list) and len(hops) >= 2:
        target = build_multihop_target(q, a, hops)
    else:
        target = build_assistant_target(q, a, _supporting_text(case), style=style)
    return {"messages": [
        {"role": "user", "content": _USER_TEMPLATE.format(question=q)},
        {"role": "assistant", "content": target},
    ]}


def load_golden_cases(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"金标准文件不存在：{path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("cases", data) if isinstance(data, dict) else data


def build(golden_path: str, out_dir: str, *, val_ratio: float = 0.1, seed: int = 42,
          style: str = "retrieval") -> dict:
    cases = load_golden_cases(golden_path)
    samples = [s for s in (case_to_sft(c, style=style) for c in (cases or []) if isinstance(c, dict)) if s]
    if not samples:
        raise ValueError("没有可用样本（需要 query + reference_answer）。先用 casegen 生成并人工校验。")
    rng = random.Random(seed)
    rng.shuffle(samples)
    n_val = max(1, int(len(samples) * val_ratio)) if len(samples) > 10 else 0
    val, train = samples[:n_val], samples[n_val:]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "train.jsonl", "w", encoding="utf-8") as f:
        for s in train:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    stats = {"train": len(train), "val": 0, "out_dir": str(out)}
    if val:
        with open(out / "val.jsonl", "w", encoding="utf-8") as f:
            for s in val:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        stats["val"] = len(val)
    return stats


def main():
    ap = argparse.ArgumentParser(description="构造 SFT 训练数据（金标准集 → jsonl，单卡 4090 可训）")
    ap.add_argument("--golden", required=True, help="金标准 JSON（casegen 产出，含 query+reference_answer）")
    ap.add_argument("--out_dir", default="/root/autodl-tmp/data/sft")
    ap.add_argument("--val_ratio", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--style", choices=["retrieval", "direct"], default="retrieval",
                    help="retrieval=教先检索再答（默认，推荐，治模型脑补）；direct=事实题直接答")
    args = ap.parse_args()
    stats = build(args.golden, args.out_dir, val_ratio=args.val_ratio, seed=args.seed, style=args.style)
    print(f"[build_sft_data] 完成（style={args.style}）：train={stats['train']} val={stats['val']} → {stats['out_dir']}")
    print("下一步：python -m hashmm.training.train_lora_4090 --data_dir", stats["out_dir"])


if __name__ == "__main__":
    main()
