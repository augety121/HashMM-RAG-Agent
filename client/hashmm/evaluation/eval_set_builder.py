"""Build an evaluation golden set from the *current* corpus — not hand-written
小米/网易 cases.

Why this module exists
----------------------
``data/eval/golden_cases.json`` was authored by hand against the Xiaomi/NetEase
financial corpus (queries like "苹果公司2024年营收" as a refusal case). That binds
evaluation to one corpus: a customer who ingests their own documents has no way
to measure quality on *their* data.

This pipeline produces a corpus-grounded golden set with three case types, all
derived from whatever was actually ingested:

  - **factual**  : reuse ``casegen.generate_cases_from_chunks`` — the LLM reads a
    real chunk and writes a Q whose answer is in that chunk (+ reference + must_contain).
  - **refusal**  : auto-built from the corpus vocabulary (Phase 40). We take a
    real subject type present in the corpus and pair it with a plausible-but-
    ABSENT subject, so the system *should* refuse ("某不存在公司的营收"). Absent
    subjects come from a neutral pool filtered to exclude anything in the corpus
    vocab — guaranteeing the subject truly isn't covered.
  - (comparison/timeline can be layered later; kept out to stay reliable.)

The output is a complete golden file that the existing evaluator loads as-is.

Honesty / safety
----------------
- Factual generation needs a real ``llm_fn`` (DeepSeek or local Qwen); without
  one, only refusal cases (which need no LLM) are produced, and the caller is told.
- Nothing here auto-overwrites your live golden set unless you pass an explicit
  output path / ``--write``. Generated cases are *candidates* — a human should
  skim them, exactly like the existing casegen flow.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Callable

from hashmm.evaluation.casegen import generate_cases_from_chunks
from hashmm.utils import get_logger

logger = get_logger("hashmm.evaluation.eval_set_builder")

# Honest-refusal markers a correct "I don't have that" answer should contain.
# (Mirrors the seed refusal cases so the evaluator scores them the same way.)
_REFUSAL_MARKERS = ["没有", "未提及", "无法", "未找到", "暂无", "不包含", "未涉及", "未披露", "查无"]

# A neutral pool of plausible org/subject names used to build refusal cases.
# Any of these that happen to appear in the corpus vocab are filtered out, so the
# chosen subjects are guaranteed ABSENT from the corpus → the system must refuse.
_ABSENT_SUBJECT_POOL = [
    "苹果公司", "三星电子", "亚马逊", "谷歌", "微软", "英特尔", "索尼", "丰田汽车",
    "辉瑞制药", "西门子", "波音公司", "壳牌石油", "雀巢集团", "可口可乐", "麦当劳",
    "星巴克", "耐克公司", "联合利华", "通用电气", "福特汽车",
]

# Question templates for refusal cases (subject is absent → must refuse).
_REFUSAL_TEMPLATES = [
    "{subj}2024年的营收是多少",
    "{subj}的最新产品有哪些",
    "{subj}的董事长是谁",
    "请介绍一下{subj}的主营业务",
]


def build_refusal_cases(
    corpus_vocab_terms: set[str] | None = None,
    n: int = 12,
    seed: int = 0,
) -> list[dict]:
    """Build refusal golden cases whose subject is guaranteed absent from corpus.

    No LLM required. Subjects are drawn from a neutral pool, excluding anything
    in the corpus vocabulary, so a correct system must answer "not covered".
    """
    vocab = corpus_vocab_terms or set()
    # Keep only pool subjects that do NOT appear (even as substring) in the corpus.
    absent = [s for s in _ABSENT_SUBJECT_POOL
              if not any(s in v or v in s for v in vocab)]
    if not absent:
        absent = list(_ABSENT_SUBJECT_POOL)  # vocab huge/edge case → use pool as-is
    rng = random.Random(seed)
    rng.shuffle(absent)

    cases: list[dict] = []
    i = 0
    while len(cases) < n and absent:
        subj = absent[i % len(absent)]
        tpl = _REFUSAL_TEMPLATES[i % len(_REFUSAL_TEMPLATES)]
        cases.append({
            "id": f"refuse_gen_{len(cases) + 1:03d}",
            "query": tpl.format(subj=subj),
            "category": "refusal",
            "must_contain_any": list(_REFUSAL_MARKERS),
            "min_sources": 0,
            "split": "heldout",
        })
        i += 1
        if i > n * len(_REFUSAL_TEMPLATES):  # safety against infinite loop
            break
    logger.info(f"[EvalSet] built {len(cases)} refusal cases "
                f"(absent subjects, {len(vocab)} vocab terms excluded)")
    return cases


def build_eval_set(
    corpus: list[dict],
    llm_fn: Callable | None = None,
    n_factual: int = 40,
    n_refusal: int = 12,
    corpus_vocab_terms: set[str] | None = None,
    seed: int = 0,
) -> dict:
    """Build a corpus-grounded golden set. Returns {cases, meta}.

    factual cases need an llm_fn; refusal cases don't. Caller decides whether to
    write the result to ``data/eval/golden_cases.json``.
    """
    factual: list[dict] = []
    if llm_fn and corpus:
        factual = generate_cases_from_chunks(corpus, llm_fn, n=n_factual, seed=seed)
        # mark split so they participate in train/heldout reporting
        for c in factual:
            c.setdefault("split", "train")
    elif not llm_fn:
        logger.warning("[EvalSet] no llm_fn → skipping factual cases (refusal only)")

    if corpus_vocab_terms is None:
        # Lazy-load the persisted vocab so refusal subjects are truly absent.
        try:
            from hashmm.corpus_vocab import load_corpus_vocab
            corpus_vocab_terms = load_corpus_vocab()
        except Exception:
            corpus_vocab_terms = set()

    refusal = build_refusal_cases(corpus_vocab_terms, n=n_refusal, seed=seed)

    # Dedup ids defensively.
    cases = factual + refusal
    seen, deduped = set(), []
    for c in cases:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        deduped.append(c)

    meta = {
        "n_factual": len(factual),
        "n_refusal": len(refusal),
        "n_total": len(deduped),
        "vocab_terms_used": len(corpus_vocab_terms or set()),
        "llm_used": bool(llm_fn),
    }
    logger.info(f"[EvalSet] built golden set: {meta}")
    return {"cases": deduped, "meta": meta}


def write_eval_set(result: dict, path: Path | str = "data/eval/golden_cases.json") -> Path:
    """Write the generated cases to a golden file (overwrites). Caller's choice."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result["cases"], ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[EvalSet] wrote {len(result['cases'])} cases → {path}")
    return path
