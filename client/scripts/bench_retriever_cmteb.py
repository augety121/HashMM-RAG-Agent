#!/usr/bin/env python3
"""v17 Phase 24 — benchmark the retriever on the C-MTEB public Chinese suite.

WHY (EVAL_UPGRADE_PLAN, "通用层"): your 100-case 小米/网易 golden proves the system
works ON YOUR CORPUS, but it cannot tell you whether the EMBEDDER ITSELF is strong —
that question is corpus-independent and your domain set saturates. C-MTEB is the
standard Chinese embedding benchmark; running BGE-M3 on it yields comparable
nDCG@10 / Recall@k / MRR, so when you swap embedding / chunking / reranking you can
tell if retrieval *itself* improved, independent of 小米/网易.

This benchmarks the SAME BGE-M3 weights you serve (point --model-path at your local
copy). It must run on your GPU box — it needs `pip install mteb sentence-transformers`
and downloads the C-MTEB datasets on first run.

Examples:
    pip install mteb sentence-transformers
    # quick smoke test (one smaller retrieval task):
    python scripts/bench_retriever_cmteb.py --model-path /root/autodl-tmp/.local_models/bge-m3
    # a fuller Chinese retrieval set:
    python scripts/bench_retriever_cmteb.py --tasks CovidRetrieval MedicalRetrieval DuRetrieval
    # list available Chinese tasks if a name errors:
    python -c "import mteb; print(sorted(t.metadata.name for t in mteb.get_tasks(languages=['cmn'])))"

NOTE on task sizes: T2Retrieval / MMarcoRetrieval have millions of passages and take
hours; CovidRetrieval / MedicalRetrieval / EcomRetrieval / VideoRetrieval are far
smaller — start with those.
"""
import argparse
import json
import os
import time
from pathlib import Path

# China networks usually can't reach huggingface.co (the user's run showed
# "Network is unreachable"). Default to the community mirror so datasets download.
# Override by exporting HF_ENDPOINT yourself, or pre-download + set HF_DATASETS_OFFLINE=1.
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# v17 Phase 27 (B5): the user's OOM came from running this benchmark while the
# hashmm server held ~10 GiB on the same 4090. Reduce allocator fragmentation so a
# co-resident process is less likely to hit "tried to allocate 16 GiB". Best
# practice is still to STOP the server or use a separate GPU during benchmarking.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# Smaller C-MTEB Chinese retrieval tasks make a sane default (full set is huge).
DEFAULT_TASKS = ["CovidRetrieval", "MedicalRetrieval"]
OUT_DIR = Path("data/eval_runs")


def _load_model(model_path: str):
    """Return an mteb-compatible model for the given path (your served BGE-M3).

    For a LOCAL directory, use SentenceTransformer directly (mteb.get_model treats the
    arg as a HF repo id and warns on a filesystem path). Falls back to mteb.get_model
    for hub names, then to a FlagEmbedding encode() wrapper.
    """
    if os.path.isdir(model_path):
        try:
            from sentence_transformers import SentenceTransformer
            return SentenceTransformer(model_path)
        except Exception:
            pass
    try:
        import mteb
        return mteb.get_model(model_path)
    except Exception:
        pass
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(model_path)
    except Exception:
        pass
    # Last resort: BGE-M3 via FlagEmbedding wrapped to the mteb encode() protocol.
    from FlagEmbedding import BGEM3FlagModel  # type: ignore

    class _BGEM3Wrapper:
        def __init__(self, p):
            self.m = BGEM3FlagModel(p, use_fp16=True)

        def encode(self, sentences, batch_size=32, **kwargs):
            return self.m.encode(list(sentences), batch_size=batch_size)["dense_vecs"]

    return _BGEM3Wrapper(model_path)


def _run(model, task_names, batch_size):
    """Run tasks across mteb API variants (v2 evaluate preferred, then stable run)."""
    import mteb
    tasks = mteb.get_tasks(tasks=task_names, languages=["cmn"])
    if not tasks:
        raise SystemExit(f"No tasks matched {task_names} for languages=['cmn'].")
    out = OUT_DIR / "cmteb"
    out.mkdir(parents=True, exist_ok=True)
    enc = {"batch_size": batch_size}
    # Preferred (mteb >= 2): non-deprecated functional API
    if hasattr(mteb, "evaluate"):
        try:
            return mteb.evaluate(model, tasks, encode_kwargs=enc)
        except TypeError:
            return mteb.evaluate(model, tasks)
    # Stable legacy API
    ev = mteb.MTEB(tasks=tasks)
    return ev.run(model, output_folder=str(out), encode_kwargs=enc, eval_splits=["test"])


def _extract_scores(results) -> dict:
    """Pull nDCG@10 / Recall@10 / MRR@10 / map@10 out of heterogeneous result objects."""
    keys = ("ndcg_at_10", "recall_at_10", "mrr_at_10", "map_at_10",
            "ndcg_at_100", "recall_at_100")
    rows = {}
    for r in (results or []):
        name = getattr(getattr(r, "task_name", None), "__str__", lambda: None)() \
            or getattr(r, "task_name", None) or str(getattr(r, "name", "task"))
        scores = {}
        # try several shapes the result object may take across versions
        candidates = []
        for attr in ("scores", "results"):
            v = getattr(r, attr, None)
            if isinstance(v, dict):
                candidates.append(v)
        d = getattr(r, "to_dict", lambda: None)()
        if isinstance(d, dict):
            candidates.append(d.get("scores", d))
        for cand in candidates:
            for split in ("test", "dev"):
                block = cand.get(split) if isinstance(cand, dict) else None
                seq = block if isinstance(block, list) else ([block] if block else [])
                for item in seq:
                    if isinstance(item, dict):
                        for k in keys:
                            if k in item and k not in scores:
                                scores[k] = round(float(item[k]), 4)
        rows[str(name)] = scores or {"note": "scores in output folder JSON"}
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-path", default="/root/autodl-tmp/.local_models/bge-m3")
    ap.add_argument("--tasks", nargs="*", default=DEFAULT_TASKS)
    ap.add_argument("--batch-size", type=int, default=16,
                    help="编码 batch(默认16，与服务共卡时更稳；独占卡可调大到64)")
    args = ap.parse_args()

    try:
        import mteb  # noqa: F401
    except ImportError:
        print("[error] mteb not installed. Run: pip install mteb sentence-transformers")
        raise SystemExit(1)

    print(f"[cmteb] model={args.model_path} tasks={args.tasks}")
    t0 = time.time()
    model = _load_model(args.model_path)
    results = _run(model, args.tasks, args.batch_size)
    scores = _extract_scores(results)
    report = {
        "kind": "cmteb_retriever_benchmark",
        "model_path": args.model_path,
        "tasks": args.tasks,
        "scores": scores,
        "elapsed_s": round(time.time() - t0, 1),
        "ts": time.time(),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUT_DIR / f"cmteb_{int(time.time())}.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(scores, ensure_ascii=False, indent=2))
    print(f"[cmteb] done in {report['elapsed_s']}s → {out_json}")
    print("提示：默认已用国内镜像 HF_ENDPOINT=https://hf-mirror.com 下载数据集。")
    print("若仍连不上：在能联网的机器上预下载数据集后拷贝过来，并设 HF_DATASETS_OFFLINE=1 HF_HOME=<缓存目录>。")
    print("检索类任务很大，T2Retrieval/MMarco 动辄数小时，建议先用默认小任务。")


if __name__ == "__main__":
    main()
