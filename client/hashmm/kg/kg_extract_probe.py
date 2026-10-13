#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KG 抽取小样诊断（1 分钟看清本地 Qwen 到底返回了什么、为什么三元组被丢）。

不用赌 6 小时整库重建——只抽 N 块（默认 20），打印：
  · 模型对前几块的【原始输出】（看它返回的 JSON 字段名）
  · 解析出多少三元组 / 转换出多少实体
  · 被丢三元组的【keys + 原因】样例（定位字段名不匹配等问题）
  · 汇总 breakdown（有三元组 / 返回空 / 转换丢弃）

用法（在 /root/autodl-tmp，且已配好本地 Qwen 环境变量）：
    export HASHMM_KG_LLM_EXTRACT=1
    export HASHMM_KG_LLM_HF_PATH=/root/autodl-tmp/models/Qwen2.5-7B-Instruct
    python -m hashmm.kg.kg_extract_probe          # 默认 20 块
    HASHMM_KG_PROBE_N=10 python -m hashmm.kg.kg_extract_probe
只读、不落盘、不改你的 KG。
"""
from __future__ import annotations

import os
import sys

from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.probe")


def _load_chunks(n: int) -> list:
    """Read chunk texts straight from the BM25 corpus (no encoder/GPU)."""
    from hashmm.retrieval_pipeline import BM25Index
    from hashmm.kg.llm_extractor import has_entity_signal
    bm = BM25Index()
    bm.load()
    corpus = list(getattr(bm, "_corpus", []) or [])
    out = []
    for meta in corpus:
        text = str(meta.get("text", "")).strip()
        if len(text) >= 30 and has_entity_signal(text):
            out.append({"text": text, "chunk_id": meta.get("chunk_id", ""),
                        "doc_id": meta.get("doc_id", ""), "filename": meta.get("filename", "")})
        if len(out) >= n:
            break
    return out


def main() -> int:
    try:
        n = int(os.environ.get("HASHMM_KG_PROBE_N", "20"))
    except ValueError:
        n = 20
    show_raw = int(os.environ.get("HASHMM_KG_PROBE_SHOW", "3"))  # how many raw outputs to print

    print("=" * 64)
    print(f"KG 抽取小样诊断：抽 {n} 块，看本地 Qwen 返回什么、转换为何丢")
    print("=" * 64)

    # 1) local LLM
    try:
        from hashmm.kg.kg_llm_provider import get_kg_llm_fn
        llm_fn = get_kg_llm_fn(None)  # None → only the local Qwen path (no paid fallback)
    except Exception as e:
        print(f"✗ 取本地 Qwen 失败：{e}")
        return 1
    if llm_fn is None:
        print("✗ 本地 Qwen 未启用。请先：export HASHMM_KG_LLM_EXTRACT=1 "
              "HASHMM_KG_LLM_HF_PATH=/root/autodl-tmp/models/Qwen2.5-7B-Instruct")
        return 1

    # 2) chunks
    try:
        chunks = _load_chunks(n)
    except Exception as e:
        print(f"✗ 读取分块失败（需在项目根、索引存在）：{e}")
        return 1
    if not chunks:
        print("✗ 没有可用分块（BM25 索引为空？）")
        return 1
    print(f"✓ 取到 {len(chunks)} 块；本地 Qwen 就绪。开始抽取…\n")

    # 3) wrap llm_fn to capture raw outputs
    raw_log: list[str] = []

    def capturing_llm(prompt):
        out = llm_fn(prompt)
        if len(raw_log) < show_raw:
            raw_log.append(str(out))
        return out

    from hashmm.kg.llm_extractor import LLMTripleExtractor, _parse_triples_json
    compact = os.environ.get("HASHMM_KG_LLM_COMPACT", "").strip() in {"1", "true", "yes"}
    extractor = LLMTripleExtractor(llm_fn=capturing_llm, compact=compact)

    per_chunk = []
    for i, c in enumerate(chunks):
        ents, rels = extractor._safe_extract(i, c)
        per_chunk.append((c, ents, rels))

    # 4) report raw outputs
    print("—— 模型原始输出样例（看 JSON 字段名是不是 head/relation/tail）——")
    for j, r in enumerate(raw_log, 1):
        snippet = r.replace("\n", " ")[:400]
        print(f"  [{j}] {snippet}")
        parsed = _parse_triples_json(r)
        if parsed:
            print(f"      解析出 {len(parsed)} 条三元组；首条 keys = {list(parsed[0].keys())}")
    print()

    # 5) per-chunk yield
    ok = sum(1 for _, e, r in per_chunk if e or r)
    tot_e = sum(len(e) for _, e, _ in per_chunk)
    print(f"—— 产出：{ok}/{len(chunks)} 块出实体，共 {tot_e} 实体 ——")

    d = extractor.last_diag
    print(f"—— breakdown：有三元组={d.get('with_triples')} 返回空[]={d.get('empty_list')} "
          f"解析失败={d.get('unparsed')} 模型空返回={d.get('empty_raw')} "
          f"转换丢弃={d.get('convert_dropped')} ——")

    # 6) dropped samples (the smoking gun)
    samples = getattr(extractor, "_drop_samples", [])
    if samples:
        print("\n—— 被丢三元组样例（看 keys 是否不是 head/relation/tail）——")
        for s in samples[:8]:
            print(f"  丢弃[{s['reason']}] keys={s['keys']} "
                  f"head={s['head']!r} rel={s['relation']!r} tail={s['tail']!r}")
    else:
        print("\n（无转换丢弃 —— 字段名兼容已生效，或模型本就返回标准 head/relation/tail）")

    print("\n判读：")
    print("  · 若『被丢样例』的 keys 不是 head/relation/tail（如 主体/客体/subject/object）→ "
          "已被本版兼容，重建产出会大涨。")
    print("  · 若 返回空[] 占多数 → 模型对多数块认为无三元组（提示词/模型能力问题），"
          "可试 unset HASHMM_KG_GLEANINGS 提速、或换更大模型。")
    print("  · 若产出已正常 → 可放心整库重建（仍需数小时）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
