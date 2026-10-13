"""hashmm/training/gen_golden_from_corpus.py — 从你**真实的企业知识库**生成中文金标准题。

这是 P2 护城河的关键一步：之前用英文 HotpotQA 只是验证「4090 能训通」，对中文企业库没用。
真正有价值的训练数据，要从你自己的语料（服务器日志里那 68 篇文档 / 5576 个 chunk）来。

本脚本复用项目已有的 `hashmm.evaluation.casegen.generate_cases_from_chunks`：
  · 语料 chunk 来自 ServiceRegistry.state["metadata"]（你的知识库，启动时已加载）；
  · 用你配置的 LLM（deepseek-v4-pro，通过 ServiceRegistry.call_llm）从 chunk 生成「问题+答案+关键点」；
  · 产出 golden_cases.json —— 正好是 build_sft_data.py 的输入。

生成的是**候选**题，建议人工过一遍再训（剔除模型生成的低质题）。这套「机器生成→人工校验」
正是大厂造评测/训练集的标准做法。

用法（在你的 AutoDL 服务器，项目根目录；不需要启动 web 服务）：
    python -m hashmm.training.gen_golden_from_corpus \
        --out /root/autodl-tmp/data/golden/golden_cases_corpus.json \
        --n 300

然后接已验证的训练流程：
    python -m hashmm.training.build_sft_data --golden <上面的 out> --out_dir /root/autodl-tmp/data/sft_corpus
    CUDA_VISIBLE_DEVICES=0 python -m hashmm.training.train_lora_4090 --data_dir /root/autodl-tmp/data/sft_corpus ...
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logger = logging.getLogger("hashmm.training.gen_golden_from_corpus")


def _load_corpus_chunks() -> list[dict]:
    """从 ServiceRegistry 拿语料 chunk。优先用已初始化的 metadata；
    没初始化就只做最小初始化（读 metadata.jsonl，不加载 GPU 模型，省时间）。"""
    from hashmm.api.core.services import ServiceRegistry
    chunks = []
    try:
        meta = (ServiceRegistry.state or {}).get("metadata") or []
        chunks = list(meta)
    except Exception as e:
        logger.debug("read state.metadata failed: %s", e)

    if not chunks:
        # 最小初始化：只读 metadata 文件，不碰模型
        try:
            from hashmm.config import HashMMConfig
            cfg = HashMMConfig()
            mp = Path(cfg.hash_index_dir) / "metadata.jsonl"
            if mp.exists():
                with open(mp, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            chunks.append(json.loads(line))
        except Exception as e:
            logger.warning("fallback metadata load failed: %s", e)
    return chunks


def _get_llm_fn():
    """拿到可调用的 LLM 函数。先确保服务初始化过，再返回 ServiceRegistry.call_llm。"""
    from hashmm.api.core.services import ServiceRegistry
    try:
        if not getattr(ServiceRegistry, "_initialized", False):
            ServiceRegistry.init_fast()
        if not getattr(ServiceRegistry, "_heavy_initialized", False):
            # call_llm 只需要 LLM 配置，重模型可不加载；但有些版本在 heavy 里建 llm_fn
            try:
                ServiceRegistry.init_heavy()
            except Exception as e:
                logger.debug("init_heavy partial: %s", e)
    except Exception as e:
        logger.warning("ServiceRegistry init failed: %s", e)

    def llm_fn(prompt: str) -> str:
        return ServiceRegistry.call_llm(prompt)
    return llm_fn


def main():
    ap = argparse.ArgumentParser(description="从真实企业知识库生成中文金标准题")
    ap.add_argument("--out", default="/root/autodl-tmp/data/golden/golden_cases_corpus.json")
    ap.add_argument("--n", type=int, default=300, help="生成多少条候选题")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("[gen_golden] 读取知识库语料 chunk ...")
    chunks = _load_corpus_chunks()
    if not chunks:
        print("[gen_golden] 没读到语料 chunk。请确认：①在项目根目录运行；"
              "②知识库已建好索引（metadata.jsonl 存在）。")
        return
    print(f"[gen_golden] 语料 chunk 数：{len(chunks)}")

    print("[gen_golden] 连接 LLM（用你配置的 deepseek-v4-pro）...")
    llm_fn = _get_llm_fn()

    from hashmm.evaluation.casegen import generate_cases_from_chunks
    print(f"[gen_golden] 开始生成（目标 {args.n} 条，会调用 LLM，耐心等）...")
    cases = generate_cases_from_chunks(chunks, llm_fn, n=args.n, seed=args.seed)
    if not cases:
        print("[gen_golden] 没生成出题目。多半是 LLM 调用失败：检查管理后台 API Key / Base URL。")
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)
    print(f"[gen_golden] 完成：生成 {len(cases)} 条中文金标准题 → {out}")
    print("建议人工过一遍剔除低质题，然后：")
    print(f"  python -m hashmm.training.build_sft_data --golden {out} --out_dir /root/autodl-tmp/data/sft_corpus")


if __name__ == "__main__":
    main()
