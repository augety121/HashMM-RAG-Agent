#!/usr/bin/env python
"""Rebuild the knowledge graph from indexed chunks using LLM semantic triples.

Why a CLI (not the "重新构建" button)
------------------------------------
The admin "重新构建" button calls a *synchronous* endpoint and is meant to be
fast (regex, < 5s). LLM extraction over a full corpus is a long, paid, batch
job (one LLM call per chunk). Running it inside an HTTP request would block for
many minutes and risk timeouts, with no way to control cost or validate on a
subset first. So the heavy LLM rebuild lives here, where you control scope and
concurrency. When it finishes, just click 刷新 / 可视化 in the admin UI — it
reads the same KG store this script writes to.

Recommended first run (cheap quality check on a subset):
    HASHMM_EVAL_EXEC unset; python scripts/rebuild_kg_llm.py --max-chunks 300 --workers 8

Full rebuild once you're happy with quality:
    python scripts/rebuild_kg_llm.py --workers 12 --yes

The LLM is read from your default model in the settings DB (where your DeepSeek
key lives), exactly like the server. No key in the environment is required.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fmt(stats: dict) -> str:
    et = stats.get("type_distribution") or stats.get("entity_types") or {}
    et_str = ", ".join(f"{k}:{v}" for k, v in sorted(et.items(), key=lambda x: -x[1]))
    return (f"实体={stats.get('entities', 0)}  关系={stats.get('relations', 0)}  "
            f"社区={stats.get('communities', 0)}\n  类型分布: {et_str or '—'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM semantic-triple KG rebuild from indexed chunks")
    ap.add_argument("--max-chunks", type=int, default=0,
                    help="只处理前 N 个 chunk（0=全部）。先用小值验证质量再全量。")
    ap.add_argument("--workers", type=int, default=8,
                    help="并发 LLM 调用数（默认 8；本地 vLLM 可调到 32-64）。")
    ap.add_argument("--no-communities", action="store_true",
                    help="构建后不做社区检测（默认会做）。")
    ap.add_argument("--summarize", action="store_true",
                    help="为每个社区生成 LLM 摘要（更慢、更花钱；默认只检测不摘要）。")
    ap.add_argument("--no-prefilter", action="store_true",
                    help="不跳过无实体信号的 chunk（默认会跳，省调用）。")
    ap.add_argument("--compact", action="store_true",
                    help="用精简 prompt（输入 token 少 ~60%%，适合本地模型）。")
    ap.add_argument("--yes", "-y", action="store_true", help="跳过确认直接开跑。")
    # ── Local / custom OpenAI-compatible endpoint (e.g. vLLM on the 4090) ──
    ap.add_argument("--base-url", default=os.environ.get("HASHMM_KG_LLM_BASE_URL", ""),
                    help="本地/自定义 LLM 端点（如 http://localhost:8000/v1）。"
                         "设置后用它而非设置 DB 的付费默认模型 → 零 token 费。")
    ap.add_argument("--model", default=os.environ.get("HASHMM_KG_LLM_MODEL", ""),
                    help="--base-url 时的模型名（如 Qwen2.5-7B-Instruct）。")
    ap.add_argument("--api-key", default=os.environ.get("HASHMM_KG_LLM_API_KEY", "EMPTY"),
                    help="本地端点 API key（vLLM 任意非空值即可，默认 EMPTY）。")
    ap.add_argument("--max-tokens", type=int, default=1024,
                    help="本地端点单次输出上限（默认 1024；抽取不需要更多，限它能大幅提速）。")
    ap.add_argument("--temperature", type=float, default=0.1, help="采样温度（默认 0.1）。")
    # ── In-process local HF model (no server; for machines where vLLM/Ollama
    #    can't be installed). Weights from a local dir (download via ModelScope). ──
    ap.add_argument("--local-hf", default=os.environ.get("HASHMM_KG_LLM_HF_PATH", ""),
                    help="进程内加载本地 HF 模型目录（如 /root/autodl-tmp/models/Qwen2.5-7B-Instruct）。"
                         "零费用、用本机 GPU、不依赖任何外部服务。")
    ap.add_argument("--device", default=None, help="--local-hf 的设备（cuda/cpu，默认自动）。")
    ap.add_argument("--checkpoint", default="data/kg/rebuild_checkpoint.jsonl",
                    help="断点文件路径。每处理一个切片就追加一行，断了可 --resume 续跑。")
    ap.add_argument("--resume", action="store_true",
                    help="从 --checkpoint 续跑：跳过已处理切片，合并其结果（全量重建强烈建议带上）。")
    ap.add_argument("--domain", default="auto",
                    help="KG 抽取领域：auto(默认,按语料自动判定)/finance/medical/legal/generic。"
                         "auto 会采样语料关键词自动选领域；通用语料→generic，财报→finance。")
    args = ap.parse_args()

    # 1) Resolve the LLM. Priority: in-process local HF model → custom endpoint
    #    (vLLM/Ollama) → default model in settings DB (paid path).
    if args.local_hf:
        from hashmm.kg.local_hf_llm import build_hf_llm_fn
        try:
            llm_fn = build_hf_llm_fn(args.local_hf, max_new_tokens=args.max_tokens,
                                     temperature=args.temperature, device=args.device)
        except Exception as e:
            print(f"❌ 加载本地 HF 模型失败：{e}")
            return 2
        model_name = f"local-hf:{args.local_hf}"
        if args.workers != 1:
            print("ℹ️ 进程内本地模型单 GPU 串行，已将 --workers 视为 1（并发无加速）。")
            args.workers = 1
    elif args.base_url:
        if not args.model:
            print("❌ 指定了 --base-url 就必须给 --model（本地端点的模型名）。")
            return 2
        from hashmm.api.model_manager import make_llm_fn_from_model
        llm_fn = make_llm_fn_from_model({
            "provider": "vllm", "base_url": args.base_url, "model_name": args.model,
            "api_key": args.api_key or "EMPTY",
            "temperature": args.temperature, "max_tokens": args.max_tokens,
        })
        model_name = f"{args.model} @ {args.base_url}"
    else:
        from hashmm.api.model_manager import get_active_llm_fn
        llm_fn, model_info = get_active_llm_fn()
        model_name = (model_info or {}).get("model_name", "?")
    if not callable(llm_fn):
        print("❌ 找不到可用模型。用 --local-hf 指本地模型目录，或 --base-url 指本地端点，或配置 DB 默认模型。")
        return 2

    # 2) Load chunks from the existing BM25 index (no PDF re-parsing).
    from hashmm.retrieval_pipeline import RetrievalPipeline
    print("加载已索引切片…")
    pipeline = RetrievalPipeline()
    pipeline.load()
    corpus = list(getattr(pipeline.bm25_index, "_corpus", []) or [])
    chunks = [{
        "text": m.get("text", ""), "page": m.get("page", -1),
        "filename": m.get("filename", ""), "doc_id": m.get("doc_id", ""),
        "chunk_id": m.get("chunk_id", ""),
    } for m in corpus]
    usable = [c for c in chunks if len(str(c.get("text", "")).strip()) >= 30]
    if not args.no_prefilter:
        from hashmm.kg.llm_extractor import has_entity_signal
        kept = [c for c in usable if has_entity_signal(c.get("text", ""))]
        skipped = len(usable) - len(kept)
        usable = kept
    else:
        skipped = 0
    n = len(usable) if args.max_chunks <= 0 else min(args.max_chunks, len(usable))
    if n == 0:
        print("❌ 没有可用切片。请先在「文档管理」索引文档。")
        return 2

    # 3) Confirm (cost is real on a paid endpoint; free on local vLLM).
    pf = f"（预过滤跳过 {skipped} 个无实体信号切片）" if skipped else ""
    print(f"模型: {model_name}   待处理: {n}{pf}   并发: {args.workers}   "
          f"{'compact' if args.compact else 'full'} prompt")
    cost_note = "本地端点零 token 费。" if (args.base_url or args.local_hf) else "⚠️ 付费端点：会消耗 token 并耗时。"
    print(f"将发起约 {n} 次 LLM 调用（按 {args.workers} 并发）。{cost_note}")
    if not args.yes:
        ans = input("确认开始？(y/N) ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消。")
            return 1

    # 4) Snapshot current KG for before/after comparison.
    from hashmm.kg.storage import KGStorage
    storage = KGStorage()
    try:
        before = storage.get_stats()
    except Exception:
        before = {}

    # 5) Build with LLM semantic triples (force opt-in for this run).
    os.environ["HASHMM_KG_LLM_EXTRACT"] = "1"
    from hashmm.kg.lightweight import LightweightKGBuilder
    from hashmm.kg.extractor import Entity, Relation
    from hashmm.kg.graph import KnowledgeGraph
    builder = LightweightKGBuilder(llm_fn=llm_fn)
    t0 = time.time()
    done_ref = [0]

    # ── Checkpoint / resume ────────────────────────────────────────────────
    # Each processed chunk appends one JSON line {cid, ents:[...], rels:[...]}.
    # A resumed run skips chunk_ids already in the checkpoint and carries their
    # triples forward, so an interrupted multi-hour rebuild never loses work.
    ckpt_path = Path(args.checkpoint) if args.checkpoint else None
    skip_ids: set[str] = set()
    if ckpt_path and args.resume and ckpt_path.exists():
        with ckpt_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    skip_ids.add(str(rec["cid"]))
                except Exception:
                    continue
        print(f"断点续传：checkpoint 已含 {len(skip_ids)} 个已处理切片，将跳过它们。")
    elif ckpt_path and not args.resume and ckpt_path.exists():
        ckpt_path.unlink()  # fresh run → clear stale checkpoint

    ckpt_fh = ckpt_path.open("a", encoding="utf-8") if ckpt_path else None
    ckpt_lock = threading.Lock()

    def _on_chunk(cid: str, ents, rels):
        if not ckpt_fh:
            return
        rec = {
            "cid": cid,
            "ents": [[e.name, e.entity_type] for e in ents],
            "rels": [[r.head, r.head_type, r.relation, r.tail, r.tail_type,
                      r.description, r.weight] for r in rels],
        }
        with ckpt_lock:
            ckpt_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            ckpt_fh.flush()

    def _progress(done: int, total: int):
        done_ref[0] = done
        pct = 100.0 * done / max(total, 1)
        print(f"\r  抽取进度: {done}/{total} ({pct:.0f}%)", end="", flush=True)

    # Resolve KG extraction domain (auto inspects a corpus sample).
    from hashmm.kg.kg_domains import resolve_domain
    _sample = [str(c.get("text", "")) for c in usable[:500]]
    _profile = resolve_domain(args.domain, _sample)
    print(f"KG 抽取领域：{_profile.name}"
          + (f"（--domain={args.domain} 自动判定）" if (args.domain or "").lower() == "auto" else ""))

    kg = builder.build_from_chunks(
        usable, max_chunks=(args.max_chunks or None),
        max_workers=args.workers, on_progress=_progress,
        prefilter=False, compact=args.compact,
        on_chunk=_on_chunk, skip_ids=skip_ids, domain=_profile.name,
    )
    print()  # newline after progress
    if ckpt_fh:
        ckpt_fh.close()
    elapsed = time.time() - t0

    # If we resumed (skipped some chunks this run), the in-memory kg only has
    # THIS run's triples. Merge in the resumed chunks' triples from the
    # checkpoint so the final KG is complete.
    if skip_ids and ckpt_path and ckpt_path.exists():
        all_e: list[Entity] = []
        all_r: list[Relation] = []
        with ckpt_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                for name, etype in rec.get("ents", []):
                    all_e.append(Entity(name=name, entity_type=etype, source_ids=[rec["cid"]]))
                for r in rec.get("rels", []):
                    head, ht, rel, tail, tt, desc, w = (list(r) + [""] * 7)[:7]
                    all_r.append(Relation(head=head, head_type=ht, relation=rel,
                                          tail=tail, tail_type=tt,
                                          weight=(w or 0.9), description=(desc or ""),
                                          source_ids=[rec["cid"]]))
        from hashmm.kg.llm_extractor import LLMTripleExtractor
        merger = LLMTripleExtractor(llm_fn=llm_fn)
        kg = KnowledgeGraph()
        kg.add_entities(merger._deduplicate_entities(all_e))
        kg.add_relations(merger._deduplicate_relations(all_r))
        print(f"已合并 checkpoint 全量三元组：{kg.num_entities} 实体 / {kg.num_relations} 关系。")

    if kg.num_entities == 0:
        print("❌ 没有抽到任何实体。可能模型返回异常或切片过短。未覆盖旧 KG。")
        return 2

    # 6) Communities + persist (reuse the tested pipeline path).
    n_comm = 0
    if not args.no_communities:
        from hashmm.pipeline.ingest import IngestPipeline
        p = IngestPipeline()
        p.kg = kg
        if args.summarize:
            try:
                p.community_mgr.set_llm(llm_fn)
            except Exception:
                pass
        try:
            n_comm = p.rebuild_communities()  # detect (+summarize) + save
        except Exception as e:
            print(f"⚠️ 社区检测失败（已保存图本体）: {e}")
            storage.save(kg)
    else:
        storage.save(kg)

    after = storage.get_stats()
    after["communities"] = n_comm or after.get("communities", 0)

    print("\n================ 重建完成 ================")
    print(f"耗时: {elapsed:.0f}s   社区: {n_comm}")
    print(f"[旧]  {_fmt(before)}")
    print(f"[新]  {_fmt(after)}")
    print("\n下一步：到管理后台「知识图谱」点【刷新】查看新图；点【可视化】检查是否还有 related_to 团块。")
    print("若新图质量满意，可不带 --max-chunks 再跑一次做全量重建。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
