"""RAG quality metrics (V15 Phase 8).

Extends the existing keyword-based RAGEvaluator with the three things a real
enterprise eval loop needs:

  1. Retrieval metrics — Recall@k / MRR / NDCG: did retrieval surface the RIGHT
     documents (not just did the answer contain keywords).
  2. LLM-as-judge — score answer quality semantically (handles paraphrase:
     "营收增长" vs "收入上升" both count), instead of brittle keyword match.
  3. Regression comparison — diff two eval runs to catch "I changed retrieval/
     prompt and it got WORSE without noticing".

All three are independent and degrade gracefully (no judge LLM → skip judging;
no relevance labels → skip retrieval metrics).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.evaluation.metrics")

_RUNS_DIR = Path("data/eval_runs")


# ════════════════════════════════════════════════════════════════════════
# 1. Retrieval quality metrics
# ════════════════════════════════════════════════════════════════════════

def _relevant_hits(retrieved: list[str], relevant: list[str]) -> list[int]:
    """Return a binary relevance list aligned to `retrieved` order.

    A retrieved doc counts as relevant if any relevant id/filename is a
    substring of it (or vice-versa) — tolerant to "file.pdf" vs "file.pdf#p3".
    """
    rel = []
    for r in retrieved:
        r_l = str(r).lower()
        hit = any(str(g).lower() in r_l or r_l in str(g).lower() for g in relevant)
        rel.append(1 if hit else 0)
    return rel


def recall_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    if not relevant:
        return 0.0
    topk = retrieved[:k]
    found = sum(1 for g in relevant
                if any(str(g).lower() in str(r).lower() or str(r).lower() in str(g).lower()
                       for r in topk))
    return round(found / len(relevant), 4)


def mrr(retrieved: list[str], relevant: list[str]) -> float:
    """Mean reciprocal rank of the first relevant hit."""
    rel = _relevant_hits(retrieved, relevant)
    for i, h in enumerate(rel):
        if h:
            return round(1.0 / (i + 1), 4)
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Normalized DCG with binary relevance."""
    rel = _relevant_hits(retrieved, relevant)[:k]
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rel))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return round(dcg / ideal, 4) if ideal else 0.0


def retrieval_metrics(retrieved_ids: list[str], relevant_ids: list[str],
                      ks: tuple[int, ...] = (3, 5, 10)) -> dict:
    """Compute the standard retrieval metric bundle for one query."""
    out = {"mrr": mrr(retrieved_ids, relevant_ids)}
    for k in ks:
        out[f"recall@{k}"] = recall_at_k(retrieved_ids, relevant_ids, k)
        out[f"ndcg@{k}"] = ndcg_at_k(retrieved_ids, relevant_ids, k)
    return out


# ════════════════════════════════════════════════════════════════════════
# 2. LLM-as-judge — semantic answer scoring
# ════════════════════════════════════════════════════════════════════════

_JUDGE_SYSTEM = (
    "你是严格的答案质量评审。根据【参考答案】给【待评答案】打分。"
    "只看事实正确性和是否覆盖关键信息，不看措辞差异（同义表达算对）。"
    "只输出 JSON：{\"score\": 0-5 的整数, \"reason\": \"简短理由\"}。"
)


def judge_answer(query: str, answer: str, reference: str, llm_fn: Any) -> dict:
    """Score one answer 0-5 against a reference using an LLM judge.

    Returns {"score": int 0-5, "normalized": 0-1, "reason": str} or
    {"score": None} if no judge available.
    """
    if not llm_fn or not reference:
        return {"score": None, "normalized": None, "reason": "no judge/reference"}
    prompt = (
        f"问题：{query}\n\n参考答案：{reference}\n\n待评答案：{answer[:2000]}\n\n"
        "请打分（JSON）："
    )
    try:
        if hasattr(llm_fn, "quick_call"):
            raw = llm_fn.quick_call(_JUDGE_SYSTEM, prompt, 200)
        elif callable(llm_fn):
            raw = llm_fn(f"{_JUDGE_SYSTEM}\n\n{prompt}")
        else:
            return {"score": None, "normalized": None, "reason": "judge not callable"}
        raw = (raw or "").strip()
        s, e = raw.find("{"), raw.rfind("}")
        if s < 0 or e < 0:
            return {"score": None, "normalized": None, "reason": "judge returned no JSON"}
        data = json.loads(raw[s:e + 1])
        score = int(data.get("score", 0))
        score = max(0, min(5, score))
        return {"score": score, "normalized": round(score / 5.0, 3),
                "reason": str(data.get("reason", ""))[:200]}
    except Exception as e:
        return {"score": None, "normalized": None, "reason": f"judge error: {str(e)[:80]}"}


# ════════════════════════════════════════════════════════════════════════
# 2b. Component-level RAG metrics (v17 Phase 22) — RAGAS-style, via LLM judge
# ════════════════════════════════════════════════════════════════════════
# These four metrics localise WHERE quality is lost — retrieval vs generation —
# instead of only a single end-to-end pass/fail. All degrade to None when no judge
# (or no answer) is available, and tolerate a judge that returns an unrelated JSON
# (missing keys → None), so existing simple mock judges never crash.

def _clamp01(v):
    """Coerce a judge-returned value to a float in [0,1], or None if unusable."""
    try:
        if v is None:
            return None
        f = float(v)
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return None


_COMPONENT_SYSTEM = (
    "你是严格的 RAG 质量诊断器。根据【问题】【检索片段】【答案】（可能还有【参考答案】），"
    "对以下维度各打 0-1 分（小数）：\n"
    "faithfulness：答案中的事实陈述能在检索片段中找到支持的比例（无支持=编造，分低）。\n"
    "answer_relevancy：答案切题地回答问题的程度（答非所问=分低）。\n"
    "context_precision：检索片段中与问题真正相关的比例（噪声多=分低）。\n"
    "context_recall：回答所需信息已被检索片段覆盖的比例（无参考答案则填 null）。\n"
    "只输出 JSON："
    "{\"faithfulness\":0-1,\"answer_relevancy\":0-1,\"context_precision\":0-1,"
    "\"context_recall\":0-1或null,\"reason\":\"简短\"}"
)

_COMPONENT_KEYS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def rag_component_metrics(query: str, answer: str, sources: list,
                          reference: str, llm_fn: Any) -> dict:
    """Compute RAGAS-style component metrics in a single judge call.

    Returns {faithfulness, answer_relevancy, context_precision, context_recall,
    reason}. Any value is None when not derivable (no judge, parse failure, or the
    judge omitted that key). One call (not four) to keep the 100-case eval cheap.
    """
    none = {k: None for k in _COMPONENT_KEYS}
    none["reason"] = ""
    if not llm_fn or not answer:
        none["reason"] = "no judge/answer"
        return none
    ctx = "\n".join(
        f"[{i + 1}] {str(s.get('text') or s.get('snippet') or '')[:500]}"
        for i, s in enumerate((sources or [])[:6])
    ) or "（无检索片段）"
    prompt = (
        f"问题：{query}\n\n检索片段：\n{ctx}\n\n答案：{answer[:2000]}\n\n"
        + (f"参考答案：{reference[:1000]}\n\n" if reference else "")
        + "请打分（JSON）："
    )
    try:
        if hasattr(llm_fn, "quick_call"):
            raw = llm_fn.quick_call(_COMPONENT_SYSTEM, prompt, 250)
        elif callable(llm_fn):
            raw = llm_fn(f"{_COMPONENT_SYSTEM}\n\n{prompt}")
        else:
            none["reason"] = "judge not callable"
            return none
        raw = (raw or "").strip()
        s, e = raw.find("{"), raw.rfind("}")
        if s < 0 or e < 0:
            none["reason"] = "judge returned no JSON"
            return none
        data = json.loads(raw[s:e + 1])
        out = {k: _clamp01(data.get(k)) for k in _COMPONENT_KEYS}
        out["reason"] = str(data.get("reason", ""))[:200]
        return out
    except Exception as ex:
        none["reason"] = f"component error: {str(ex)[:80]}"
        return none


_RUBRIC_SYSTEM = (
    "你是严格的答案验收器。判断【待评答案】是否真正满足【验收标准】。"
    "只看语义/功能是否满足，不要求出现任何特定字面词（同义、等价实现都算满足）。"
    "只输出 JSON：{\"pass\": true 或 false, \"reason\": \"简短理由\"}"
)


def rubric_check(answer: str, rubric: str, llm_fn: Any,
                 hints: list | None = None) -> dict:
    """Semantic acceptance check: does `answer` satisfy `rubric`?

    Replaces brittle substring matching (e.g. requiring the literal 'def'). `hints`
    are passed only as guidance to the judge, never hard-matched. Returns
    {"pass": bool|None, "reason": str}; pass is None when no judge/rubric or on
    parse failure, so callers can fall back to keyword checks.
    """
    if not llm_fn or not rubric:
        return {"pass": None, "reason": "no judge/rubric"}
    hint_s = (f"（参考要点，仅供判断、不要求字面出现：{', '.join(map(str, hints))}）"
              if hints else "")
    prompt = (f"验收标准：{rubric}{hint_s}\n\n待评答案：{answer[:2000]}\n\n请判定（JSON）：")
    try:
        if hasattr(llm_fn, "quick_call"):
            raw = llm_fn.quick_call(_RUBRIC_SYSTEM, prompt, 150)
        elif callable(llm_fn):
            raw = llm_fn(f"{_RUBRIC_SYSTEM}\n\n{prompt}")
        else:
            return {"pass": None, "reason": "judge not callable"}
        raw = (raw or "").strip()
        s, e = raw.find("{"), raw.rfind("}")
        if s < 0 or e < 0:
            return {"pass": None, "reason": "rubric returned no JSON"}
        data = json.loads(raw[s:e + 1])
        if "pass" not in data:
            return {"pass": None, "reason": "rubric JSON missing 'pass'"}
        return {"pass": bool(data.get("pass")), "reason": str(data.get("reason", ""))[:200]}
    except Exception as ex:
        return {"pass": None, "reason": f"rubric error: {str(ex)[:80]}"}


# ════════════════════════════════════════════════════════════════════════
# 2c. Execution-based code verification (v17 Phase 23) — the SWE-bench standard
# ════════════════════════════════════════════════════════════════════════
# For code questions, the gold-standard judge is not "contains the word def" and
# not even an LLM's opinion — it's whether the code ACTUALLY RUNS (and, optionally,
# produces the expected output). This is deterministic, needs no LLM, and is exactly
# how SWE-bench / τ-bench verify code: run it, check it works.
#
# SAFETY: this executes model-generated code, so it is GATED behind the env var
# HASHMM_EVAL_EXEC=1 (off by default) and sandboxed in a subprocess with a hard
# timeout and a throwaway temp cwd. When disabled, returns ok=None so callers fall
# back to the rubric/keyword checks (no behaviour change unless explicitly enabled).

_CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def _exec_enabled() -> bool:
    return os.environ.get("HASHMM_EVAL_EXEC", "") in ("1", "true", "True", "yes")


def _extract_python_candidates(answer: str) -> list[str]:
    """Return runnable code candidates from an answer, most-likely-complete first.

    A chat answer often has ONE complete example block plus small illustrative
    fragments (e.g. `sorted(d.items()...)` referencing an undefined `d`). Running all
    blocks concatenated makes a fragment crash the whole check. So we try the LARGEST
    block first (usually the full example), then the concatenation as a fallback for
    answers that legitimately split one program across blocks.
    """
    blocks = [b.strip() for b in _CODE_BLOCK_RE.findall(answer or "") if b.strip()]
    if blocks:
        cands = [max(blocks, key=len)]                      # primary: the largest block
        if len(blocks) > 1:
            cands.append("\n\n".join(blocks))               # fallback: everything joined
        return cands
    if answer and re.search(r"\b(def |import |sorted\(|lambda |print\()", answer):
        return [answer.strip()]
    return []


def _run_one(code: str, timeout: int):
    with tempfile.TemporaryDirectory() as td:
        return subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True, text=True, timeout=timeout, cwd=td,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"},
        )


def run_code_check(answer: str, expect: list | None = None, timeout: int = 5) -> dict:
    """Execute the code in `answer` and report whether it runs (and emits `expect`).

    Returns {"ok": True/False/None, "reason": str}. ok is None when execution is
    disabled (HASHMM_EVAL_EXEC unset) or no code block is found — callers then fall
    back to other checks. True = ran cleanly (and produced all `expect` substrings);
    False = syntax/runtime error, timeout, or missing expected output. Tries the
    primary (largest) block first so trailing fragments don't cause false failures.
    """
    if not _exec_enabled():
        return {"ok": None, "reason": "code exec disabled (set HASHMM_EVAL_EXEC=1)"}
    candidates = _extract_python_candidates(answer)
    if not candidates:
        return {"ok": None, "reason": "no python code found"}
    last_reason = ""
    for code in candidates:
        try:
            proc = _run_one(code, timeout)
        except subprocess.TimeoutExpired:
            last_reason = f"timeout >{timeout}s"
            continue
        except Exception as ex:
            last_reason = f"exec error: {str(ex)[:120]}"
            continue
        if proc.returncode != 0:
            last_reason = f"runtime error: {(proc.stderr or '')[-160:]}"
            continue
        out = proc.stdout or ""
        if expect:
            missing = [e for e in expect if str(e) not in out]
            if missing:
                last_reason = f"ran but missing output: {missing}"
                continue
        return {"ok": True, "reason": "executed cleanly" + (" + output matched" if expect else "")}
    return {"ok": False, "reason": last_reason or "did not run cleanly"}


# ════════════════════════════════════════════════════════════════════════
# 3. Run persistence + regression comparison
# ════════════════════════════════════════════════════════════════════════

def save_run(run: dict, tag: str = "") -> str:
    """Persist an eval run to disk for later comparison. Returns run_id."""
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"{int(time.time())}_{tag}" if tag else str(int(time.time()))
    run = {**run, "run_id": run_id, "saved_at": time.time(), "tag": tag}
    with open(_RUNS_DIR / f"{run_id}.json", "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=2)
    return run_id


def list_runs(limit: int = 20) -> list[dict]:
    if not _RUNS_DIR.exists():
        return []
    files = sorted(_RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            out.append({"run_id": d.get("run_id"), "tag": d.get("tag", ""),
                        "saved_at": d.get("saved_at"),
                        "avg_score": d.get("summary", {}).get("avg_score"),
                        "pass_rate": d.get("summary", {}).get("pass_rate"),
                        "avg_judge_score": d.get("summary", {}).get("avg_judge_score")})
        except Exception:
            continue
    return out


def load_run(run_id: str) -> dict | None:
    p = _RUNS_DIR / f"{run_id}.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def compare_runs(baseline_id: str, candidate_id: str) -> dict:
    """Diff two runs per-case to catch regressions.

    Returns overall deltas + a list of cases that REGRESSED (got worse) and
    cases that IMPROVED.
    """
    base = load_run(baseline_id)
    cand = load_run(candidate_id)
    if not base or not cand:
        return {"error": "run not found"}

    def _by_case(run):
        return {r["case_id"]: r for r in run.get("results", [])}

    b, c = _by_case(base), _by_case(cand)
    regressed, improved, unchanged = [], [], []
    for cid in set(b) | set(c):
        br = b.get(cid, {})
        cr = c.get(cid, {})
        bs = br.get("score", 0)
        cs = cr.get("score", 0)
        delta = round(cs - bs, 3)
        rec = {
            "case_id": cid, "baseline": bs, "candidate": cs, "delta": delta,
            "query": cr.get("query") or br.get("query", ""),
            # v16 Phase 13: side-by-side diff data
            "baseline_answer": (br.get("answer") or "")[:800],
            "candidate_answer": (cr.get("answer") or "")[:800],
            "baseline_judge": br.get("judge_score"),
            "candidate_judge": cr.get("judge_score"),
            "baseline_sources": [s.get("filename", "") for s in (br.get("sources") or [])][:5],
            "candidate_sources": [s.get("filename", "") for s in (cr.get("sources") or [])][:5],
        }
        if delta < -0.01:
            regressed.append(rec)
        elif delta > 0.01:
            improved.append(rec)
        else:
            unchanged.append(rec)

    regressed.sort(key=lambda x: x["delta"])
    bsum = base.get("summary", {})
    csum = cand.get("summary", {})
    return {
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "avg_score_delta": round(csum.get("avg_score", 0) - bsum.get("avg_score", 0), 3),
        "pass_rate_delta": round(csum.get("pass_rate", 0) - bsum.get("pass_rate", 0), 3),
        "regressed": regressed,
        "improved": improved,
        "unchanged_count": len(unchanged),
        "verdict": ("REGRESSION" if regressed and
                    csum.get("avg_score", 0) < bsum.get("avg_score", 0)
                    else "OK"),
    }
