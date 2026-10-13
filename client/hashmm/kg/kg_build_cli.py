#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立知识图谱构建器（本地 Qwen）——绕开服务端点导致的"吐空"问题。

为什么需要它：通过 /api/kg/build-from-chunks 在【服务进程】里建图时，本地 HF Qwen
是在 FastAPI 的工作线程里、且 GPU 同时驻留 BGE-M3+reranker+Qwen，结果模型对 ~99%
的块返回空 []（产出率 0.8%）。而完全相同的模型/prompt 在【独立进程的主线程】里跑
（probe 的方式）产出率 ~60%。本 CLI 就照搬 probe 的成功环境：
  · 独立进程、主线程顺序抽取（不经服务端点的工作线程）
  · 只用 BM25 读 chunk 文本（不加载 BGE-M3，显存只有 Qwen）

用法（务必先【停掉服务】，让显存只有 Qwen）：
    export HASHMM_KG_LLM_EXTRACT=1
    export HASHMM_KG_LLM_HF_PATH=/root/autodl-tmp/models/Qwen2.5-7B-Instruct
    export HASHMM_KG_FEWSHOT_RICH=1 HASHMM_KG_LAZY_SUMMARIES=1
    # 先小批验证（约 2 分钟）：
    HASHMM_KG_LLM_MAX_CHUNKS=500 python -m hashmm.kg.kg_build_cli
    # 满意后跑全量（约 3-4 小时，有进度+ETA）：
    python -m hashmm.kg.kg_build_cli
建完接着：HASHMM_KG_RESOLVE_DROP_NOISE=1 python -m hashmm.kg.entity_resolution
"""
from __future__ import annotations

import os
import sys
import time

from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.build_cli")


def _cap() -> int | None:
    raw = os.environ.get("HASHMM_KG_LLM_MAX_CHUNKS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return None


def main() -> int:
    print("=" * 64)
    print("独立 KG 构建（本地 Qwen，主线程，绕开服务端点）")
    print("=" * 64)

    # 本地 7B 首遍抽取命中率极低（~1%），靠 gleanings 第二遍“补漏”才能到 27-60%。
    # 默认开启（除非你显式 HASHMM_KG_GLEANINGS=0）。这是产出高低的关键开关。
    if os.environ.get("HASHMM_KG_GLEANINGS", "") == "":
        os.environ["HASHMM_KG_GLEANINGS"] = "1"
    print(f"  HASHMM_KG_GLEANINGS={os.environ.get('HASHMM_KG_GLEANINGS')} "
          f"（本地弱模型必须开，否则产出会从 ~30% 掉到 ~1%）")

    # 1) local model (None → only local Qwen, no paid fallback)
    try:
        from hashmm.kg.kg_llm_provider import get_kg_llm_fn
        llm_fn = get_kg_llm_fn(None)
    except Exception as e:
        print(f"✗ 取本地 Qwen 失败：{e}")
        return 1
    if llm_fn is None:
        print("✗ 本地 Qwen 未启用。请先 export HASHMM_KG_LLM_EXTRACT=1 "
              "HASHMM_KG_LLM_HF_PATH=/root/autodl-tmp/models/Qwen2.5-7B-Instruct")
        return 1
    if not getattr(llm_fn, "is_local_hf", False):
        print("⚠ 注意：当前 kg llm 不是本地 HF 模型（可能回退到了付费 chat）。继续，但请确认环境变量。")

    # 2) chunks via BM25 only — NO encoder on the GPU (mirrors the working probe)
    try:
        from hashmm.retrieval_pipeline import BM25Index
        bm = BM25Index()
        bm.load()
        corpus = list(getattr(bm, "_corpus", []) or [])
    except Exception as e:
        print(f"✗ 读取 BM25 分块失败（需在项目根、索引存在）：{e}")
        return 1
    if not corpus:
        print("✗ BM25 索引为空，无法建图。")
        return 1
    chunks = [{
        "text": m.get("text", ""), "page": m.get("page", -1),
        "filename": m.get("filename", ""), "doc_id": m.get("doc_id", ""),
        "chunk_id": m.get("chunk_id", ""),
    } for m in corpus]
    cap = _cap()
    print(f"✓ 取到 {len(chunks)} 块{'（限 ' + str(cap) + ' 块）' if cap else ''}；本地 Qwen 就绪。")
    print("  开始主线程顺序抽取（每 ~30s 打印进度+ETA）…\n")

    # 3) build in the MAIN thread (build_from_chunks → _build_with_llm; local model
    #    forces workers=1 → sequential loop in THIS thread, just like the probe)
    try:
        from hashmm.kg.lightweight import LightweightKGBuilder
        builder = LightweightKGBuilder(min_entity_freq=2, llm_fn=llm_fn)
        t0 = time.time()
        kg = builder.build_from_chunks(chunks, max_chunks=cap, max_workers=1)
        builder.save(kg)
        dt = time.time() - t0
        print(f"\n✓ 抽取完成：{kg.num_entities} 实体 / {kg.num_relations} 关系，用时 {dt:.0f}s")
    except Exception as e:
        print(f"✗ 建图失败：{e}")
        return 1

    # 4) communities (extraction is done — encoder coexistence no longer matters)
    try:
        from hashmm.pipeline.ingest import IngestPipeline
        pipe = IngestPipeline()
        pipe.load_kg()
        try:
            pipe.community_mgr.set_llm(llm_fn)
        except Exception:
            pass
        n = pipe.rebuild_communities()
        pipe.storage.save(pipe.kg, pipe.community_mgr)
        print(f"✓ 社区：{n if isinstance(n, int) else '已检测'}（HASHMM_KG_LAZY_SUMMARIES 下摘要按需生成）")
    except Exception as e:
        print(f"⚠ 社区检测跳过（不影响实体/关系）：{e}")

    print("\n下一步：")
    print("  HASHMM_KG_RESOLVE_DROP_NOISE=1 python -m hashmm.kg.entity_resolution   # 去噪合并")
    print("  python -m hashmm.agent.status                                         # 看实体数/连通块")
    print("  重新启动服务即可使用新图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
