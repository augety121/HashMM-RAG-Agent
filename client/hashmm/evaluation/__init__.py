"""RAG Quality Evaluator — automated answer quality testing.

Maintains a set of golden test cases with expected answers/behaviors,
runs them against the live system, and produces a quality report.

Usage:
    evaluator = RAGEvaluator()
    report = evaluator.run_all()
    print(report["summary"])  # "17/20 passed (85%)"

API:
    POST /api/admin/eval/run     — run all golden tests
    GET  /api/admin/eval/cases   — list test cases
    POST /api/admin/eval/cases   — add a test case
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evaluation")

_CASES_PATH = Path("data/eval/golden_cases.json")

# Full-width digit → half-width translation
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def _norm_text(s: str) -> str:
    """Normalize for matching: lowercase, strip thousands separators / spaces /
    full-width digits, so "3,659" / "3659" / "３６５９" all compare equal.
    """
    if not s:
        return ""
    s = s.translate(_FULLWIDTH).lower()
    # remove thousands separators between digits: 3,659 → 3659
    s = re.sub(r"(?<=\d)[,，](?=\d)", "", s)
    # collapse spaces
    s = re.sub(r"\s+", "", s)
    return s


@dataclass
class TestCase:
    """A single golden test case."""
    __test__ = False  # 告诉 pytest：这是数据类不是测试类，别收集（消除 PytestCollectionWarning）
    id: str
    query: str
    category: str = "factual"        # factual / comparison / analytical / code
    must_contain: list[str] = field(default_factory=list)
    must_contain_any: list[str] = field(default_factory=list)  # OR: at least one
    must_not_contain: list[str] = field(default_factory=list)
    must_cite: bool = False
    min_sources: int = 0
    min_length: int = 0
    must_mention_all: list[str] = field(default_factory=list)
    retrieval_mode: str = "mix"
    # v15 Phase 8: enterprise eval
    relevant_docs: list[str] = field(default_factory=list)   # ids/filenames for Recall@k/MRR/NDCG
    reference_answer: str = ""                                # for LLM-as-judge
    # v17 Phase 22: semantic acceptance rubric. When set AND a judge is available,
    # a rubric judge decides the content checks instead of brittle keyword matching
    # (e.g. "correctly implements dict-sort-by-value" rather than 'contains "def"').
    rubric: str = ""
    # v17 Phase 23: execution-based verification for code questions. When code_runs
    # is True (and HASHMM_EVAL_EXEC=1), the answer's code is actually run; success
    # supersedes brittle keyword checks. code_expect = stdout substrings required.
    code_runs: bool = False
    code_expect: list[str] = field(default_factory=list)
    # v17 Phase 23: dataset split for held-out integrity. "train" cases may inform
    # tuning; "heldout" cases never do — the report compares the two to catch
    # overfitting to the eval set.
    split: str = "train"


@dataclass
class TestResult:
    """Result of a single test case evaluation."""
    case_id: str
    query: str
    passed: bool
    score: float               # 0.0 - 1.0
    checks: list[dict]         # [{name, passed, detail}]
    answer_length: int = 0
    num_sources: int = 0
    top_score: float = 0.0
    elapsed_ms: int = 0
    error: str = ""
    # v15 Phase 8
    retrieval_metrics: dict = field(default_factory=dict)
    judge_score: float | None = None
    judge_reason: str = ""
    # v16 Phase 13: drill-down data
    category: str = ""
    answer: str = ""                                  # full answer text (truncated on output)
    sources: list = field(default_factory=list)       # retrieved sources for inspection
    fail_reason: str = ""                             # auto-classified failure bucket
    # v17 Phase 22: RAGAS-style component metrics (faithfulness / answer_relevancy /
    # context_precision / context_recall). Values may be None when no judge.
    component_metrics: dict = field(default_factory=dict)


# ── Default golden test cases ──

DEFAULT_CASES = [
    {
        "id": "factual_01",
        "query": "小米2024年营收是多少",
        "category": "factual",
        "must_contain": ["3,659", "3659"],
        "must_cite": True,
        "min_sources": 1,
        "relevant_docs": ["小米集团"],
        "reference_answer": "小米集团2024年营收为人民币3,659亿元。",
    },
    {
        "id": "factual_02",
        "query": "小米2025年营收是多少",
        "category": "factual",
        "must_contain": ["4,573", "4573"],
        "must_cite": True,
        "min_sources": 1,
        "relevant_docs": ["小米集团"],
        "reference_answer": "小米集团2025年营收为人民币4,573亿元，同比增长约25%。",
    },
    {
        "id": "comparison_01",
        "query": "小米2025年和2024年营收对比",
        "category": "comparison",
        "must_mention_all": ["2024", "2025"],
        "min_length": 80,
        "must_cite": True,
        "relevant_docs": ["小米集团"],
        "reference_answer": "小米2025年营收4,573亿元，2024年为3,659亿元，同比增长约25%。",
    },
    {
        "id": "analytical_01",
        "query": "小米在AI领域的布局和战略",
        "category": "analytical",
        "min_length": 150,
        "relevant_docs": ["小米集团"],
        "reference_answer": "小米围绕'人车家全生态'布局AI，自研MiLM大语言模型，强调端侧轻量化部署，覆盖手机、汽车、IoT设备。",
    },
    {
        "id": "metric_01",
        "query": "小米的毛利率是多少",
        "category": "factual",
        "must_cite": True,
        "relevant_docs": ["小米集团"],
        "reference_answer": "小米集团2025年整体毛利率约22.3%，其中互联网服务毛利率约76.5%。",
    },
    {
        "id": "code_01",
        "query": "帮我写一个 Python 的快速排序",
        "category": "code",
        "must_contain": ["def ", "sort"],
        "min_length": 100,
        "min_sources": 0,
    },
    {
        "id": "greeting_01",
        "query": "你好",
        "category": "greeting",
        "min_sources": 0,
    },
    # ── v17 Phase 16: refusal / anti-hallucination cases ──
    # These test HONESTY: the corpus has no Tencent/Apple data, so a good system
    # should say "资料里没有" rather than fabricate. must_contain checks for
    # honest-refusal language; must_not_contain guards against fabricated numbers.
    {
        "id": "refusal_01",
        "query": "苹果公司2024年的营收是多少",
        "category": "refusal",
        "must_contain_any": ["没有", "未提及", "无法", "未找到", "暂无", "不包含", "未涉及"],
        "min_sources": 0,
    },
    {
        "id": "refusal_02",
        "query": "特斯拉2023年在中国卖了多少辆车",
        "category": "refusal",
        "must_contain_any": ["没有", "未提及", "无法", "未找到", "暂无", "不包含", "未涉及"],
        "min_sources": 0,
    },
    {
        "id": "antihalluc_01",
        "query": "小米2099年的营收预测是多少",
        "category": "refusal",
        "must_contain_any": ["没有", "未提及", "无法", "暂无", "未涉及", "无法预测"],
        "min_sources": 0,
    },
    # ── boundary / robustness ──
    {
        "id": "boundary_empty_topic",
        "query": "请概括一下文档的主要内容",
        "category": "analytical",
        "min_length": 50,
    },
    {
        "id": "multihop_01",
        "query": "小米2025年营收和2024年比增长了多少",
        "category": "comparison",
        "must_mention_all": ["2024", "2025"],
        "min_length": 40,
        "must_cite": True,
        "relevant_docs": ["小米集团"],
        "reference_answer": "小米2025年营收4,573亿元，2024年3,659亿元，增长约25%。",
    },
]


def load_all_bundled_cases() -> list["TestCase"]:
    """全面体检：合并所有内置用例集，按 query 去重，只取 TestCase 已知字段。

    覆盖 factual / comparison / analytical / code / refusal / multihop / temporal /
    greeting / adversarial 等全部维度，让「一次跑全、一次定位所有问题」成为可能
    （对标 RAGAS/DeepEval 的多维评测，但适配本项目的 golden 用例体系）。
    """
    import json as _json
    base = Path(__file__).parent
    files = [
        "agent_hard_cases.json",       # 困难·智能体级 v1：忠实/拒答/反事实/多跳/综合/指令/鲁棒/安全/多步/校准
        "agent_hard_cases_v2.json",    # 困难·智能体级 v2：长任务/深度推理/问题解决/代码/工具推理/复杂约束/硬多跳（对标 Claude/Codex）
        "golden_cases_100.json",       # 主集：检索/生成/拒答/代码
        "golden_cases_general.json",   # 通用场景
        "adversarial_cases.json",      # 对抗/越狱/提示注入（安全鲁棒性）
        "quality_cases.json",          # 质量/讲解类
    ]
    valid = set(TestCase.__dataclass_fields__.keys())
    seen: set[str] = set()
    out: list[TestCase] = []
    for fn in files:
        p = base / fn
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8") as f:
                raw = _json.load(f)
            items = raw if isinstance(raw, list) else raw.get("cases", [])
            for c in items:
                q = (c.get("query") or "").strip()
                if not q or q in seen:
                    continue
                seen.add(q)
                filtered = {k: v for k, v in c.items() if k in valid}
                try:
                    out.append(TestCase(**filtered))
                except Exception as _ce:
                    logger.warning(f"skip case in {fn}: {_ce}")
        except Exception as e:
            logger.warning(f"load bundled {fn} failed: {e}")
    logger.info(f"[eval] 全面体检合并用例 {len(out)} 条（来自 {len(files)} 个集）")
    return out


def load_agentic_cases() -> list["TestCase"]:
    """只加载「困难·智能体级」用例集（v1+v2，全部带参考答案+rubric）。
    用于单独跑这批高难度题——比全面体检快得多，便于针对长思考/长任务/解决问题能力定向打分。"""
    import json as _json
    base = Path(__file__).parent
    files = ["agent_hard_cases.json", "agent_hard_cases_v2.json"]
    valid = set(TestCase.__dataclass_fields__.keys())
    seen: set[str] = set()
    out: list[TestCase] = []
    for fn in files:
        p = base / fn
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8") as f:
                raw = _json.load(f)
            items = raw if isinstance(raw, list) else raw.get("cases", [])
            for c in items:
                q = (c.get("query") or "").strip()
                if not q or q in seen:
                    continue
                seen.add(q)
                filtered = {k: v for k, v in c.items() if k in valid}
                try:
                    out.append(TestCase(**filtered))
                except Exception as _ce:
                    logger.warning(f"skip agentic case in {fn}: {_ce}")
        except Exception as e:
            logger.warning(f"load agentic {fn} failed: {e}")
    logger.info(f"[eval] 困难智能体用例集 {len(out)} 条（v1+v2）")
    return out


class RAGEvaluator:
    """Run golden tests and produce quality reports."""

    def __init__(self):
        self._cases: list[TestCase] = []
        self._load_cases()

    def _backfill_rubrics_from_bundled(self) -> None:
        """v17 Phase 22b: a rubric is eval *methodology* shipped with the code, not
        user content. The user's data/eval/golden_cases.json may predate the rubric
        field, so backfill any EMPTY rubric from the bundled set (matched by id),
        in-memory. This makes the semantic check (e.g. code_10) take effect on the
        next run WITHOUT requiring a manual migration step. Only fills empty rubrics
        — never overwrites a user-set one. Logged transparently.
        """
        try:
            bundled = Path(__file__).parent / "golden_cases_100.json"
            if not bundled.exists():
                return
            with open(bundled, encoding="utf-8") as f:
                raw = json.load(f)
            ref = {c["id"]: c for c in raw}
            filled = 0
            for case in self._cases:
                b = ref.get(case.id)
                if not b:
                    continue
                touched = False
                if not getattr(case, "rubric", "") and b.get("rubric"):
                    case.rubric = b["rubric"]
                    touched = True
                if not getattr(case, "code_runs", False) and b.get("code_runs"):
                    case.code_runs = True
                    if b.get("code_expect") and not case.code_expect:
                        case.code_expect = list(b["code_expect"])
                    touched = True
                filled += 1 if touched else 0
            if filled:
                logger.info(f"[Phase22b] backfilled eval methodology for {filled} case(s) from bundled set")
        except Exception as e:
            logger.warning(f"rubric backfill skipped: {e}")

    def _load_cases(self):
        """Load cases: data/eval/golden_cases.json → bundled 100-case set → defaults."""
        if _CASES_PATH.exists():
            try:
                with open(_CASES_PATH, encoding="utf-8") as f:
                    raw = json.load(f)
                self._cases = [TestCase(**c) for c in raw]
                logger.info(f"Loaded {len(self._cases)} golden cases from {_CASES_PATH}")
                self._backfill_rubrics_from_bundled()
                return
            except Exception as e:
                logger.warning(f"Failed to load golden cases: {e}")

        # v17 Phase 17: bundled 100-case golden set (ships inside the package,
        # not in data/ which is excluded from deploys). Used when the user hasn't
        # created their own data/eval/golden_cases.json yet.
        bundled = Path(__file__).parent / "golden_cases_100.json"
        if bundled.exists():
            try:
                with open(bundled, encoding="utf-8") as f:
                    raw = json.load(f)
                self._cases = [TestCase(**c) for c in raw]
                logger.info(f"Loaded {len(self._cases)} bundled golden cases")
                return
            except Exception as e:
                logger.warning(f"Failed to load bundled golden cases: {e}")
        self._cases = [TestCase(**c) for c in DEFAULT_CASES]

    def save_cases(self):
        """Persist cases to disk."""
        _CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CASES_PATH, "w", encoding="utf-8") as f:
            json.dump([self._case_to_dict(c) for c in self._cases],
                      f, ensure_ascii=False, indent=2)

    def _case_to_dict(self, case: TestCase) -> dict:
        return {
            "id": case.id, "query": case.query, "category": case.category,
            "must_contain": case.must_contain, "must_not_contain": case.must_not_contain,
            "must_cite": case.must_cite, "min_sources": case.min_sources,
            "min_length": case.min_length, "must_mention_all": case.must_mention_all,
            "retrieval_mode": case.retrieval_mode,
            "relevant_docs": case.relevant_docs,
            "reference_answer": case.reference_answer,
            "rubric": case.rubric,
            "code_runs": case.code_runs,
            "code_expect": case.code_expect,
            "split": case.split,
        }

    @property
    def cases(self) -> list[TestCase]:
        return list(self._cases)

    def add_case(self, case_dict: dict) -> TestCase:
        """Add a new test case."""
        case = TestCase(**case_dict)
        # Remove existing with same ID
        self._cases = [c for c in self._cases if c.id != case.id]
        self._cases.append(case)
        self.save_cases()
        return case

    def remove_case(self, case_id: str) -> bool:
        before = len(self._cases)
        self._cases = [c for c in self._cases if c.id != case_id]
        if len(self._cases) < before:
            self.save_cases()
            return True
        return False

    def evaluate_single(self, case: TestCase, answer: str,
                        sources: list[dict]) -> TestResult:
        """Evaluate a single answer against its test case."""
        checks: list[dict] = []
        total_checks = 0
        passed_checks = 0

        answer_norm = _norm_text(answer)

        # 1. Must-contain keywords (number-aware: "3,659" matches "3659")
        for kw in case.must_contain:
            total_checks += 1
            found = _norm_text(kw) in answer_norm
            if found:
                passed_checks += 1
            checks.append({
                "name": f"contains '{kw}'",
                "passed": found,
                "detail": "found" if found else "missing",
            })

        # 1b. Must-contain-ANY (OR semantics) — used by refusal cases where any
        # one honesty phrase ("没有"/"未提及"/...) counts as a correct refusal.
        if case.must_contain_any:
            total_checks += 1
            hit = next((kw for kw in case.must_contain_any
                        if _norm_text(kw) in answer_norm), None)
            if hit:
                passed_checks += 1
            checks.append({
                "name": f"contains any of {case.must_contain_any}",
                "passed": hit is not None,
                "detail": f"matched '{hit}'" if hit else "none matched",
            })

        # 2. Must-not-contain
        for kw in case.must_not_contain:
            total_checks += 1
            found = kw.lower() in answer.lower()
            if not found:
                passed_checks += 1
            checks.append({
                "name": f"excludes '{kw}'",
                "passed": not found,
                "detail": "clean" if not found else "found (bad)",
            })

        # 3. Citation check
        if case.must_cite:
            total_checks += 1
            citations = re.findall(r'\[\d+\]', answer)
            has_cite = len(citations) > 0
            if has_cite:
                passed_checks += 1
            checks.append({
                "name": "has citations",
                "passed": has_cite,
                "detail": f"{len(citations)} citations" if has_cite else "none",
            })

        # 4. Minimum sources
        if case.min_sources > 0:
            total_checks += 1
            enough = len(sources) >= case.min_sources
            if enough:
                passed_checks += 1
            checks.append({
                "name": f"≥{case.min_sources} sources",
                "passed": enough,
                "detail": f"{len(sources)} sources",
            })

        # 5. Minimum length
        if case.min_length > 0:
            total_checks += 1
            long_enough = len(answer) >= case.min_length
            if long_enough:
                passed_checks += 1
            checks.append({
                "name": f"≥{case.min_length} chars",
                "passed": long_enough,
                "detail": f"{len(answer)} chars",
            })

        # 6. Must mention all entities
        for entity in case.must_mention_all:
            total_checks += 1
            mentioned = entity in answer
            if mentioned:
                passed_checks += 1
            checks.append({
                "name": f"mentions '{entity}'",
                "passed": mentioned,
                "detail": "found" if mentioned else "missing",
            })

        # Scoring: a case with NO checks (e.g. a greeting that only needs to
        # respond) is considered a pass — there was nothing to fail.
        # v17 Phase 23: execution-based code verification. When enabled and the code
        # actually runs (and emits any expected output), it supersedes the brittle
        # content keyword checks (contains/mentions) — running, correct code is the
        # real bar, not whether it contains the literal token 'def'. Offline, no LLM.
        if case.code_runs:
            from hashmm.evaluation import metrics as _M
            cc = _M.run_code_check(answer, case.code_expect or None)
            if cc.get("ok") is not None:
                checks = [c for c in checks
                          if not (c["name"].startswith("contains")
                                  or c["name"].startswith("mentions"))]
                checks.append({
                    "name": "code executes" + (" + output" if case.code_expect else ""),
                    "passed": bool(cc["ok"]),
                    "detail": cc.get("reason", "")[:140],
                })
                passed_checks = sum(1 for c in checks if c["passed"])
                total_checks = len(checks)

        if total_checks == 0:
            score = 1.0
        else:
            score = passed_checks / total_checks
        top_score = sources[0].get("score", 0.0) if sources else 0.0

        return TestResult(
            case_id=case.id,
            query=case.query,
            passed=score >= 0.8,  # 80% threshold
            score=round(score, 2),
            checks=checks,
            answer_length=len(answer),
            num_sources=len(sources),
            top_score=round(float(top_score), 4) if top_score else 0.0,
        )

    def run_all(self, chat_fn) -> dict:
        """Run all golden test cases.

        Args:
            chat_fn: Function that takes (query, mode) and returns (answer, sources)

        Returns:
            {"summary": "17/20 passed (85%)", "results": [...], "elapsed_ms": ...}
        """
        t0 = time.time()
        results = []

        for case in self._cases:
            try:
                t_start = time.time()
                answer, sources = chat_fn(case.query, case.retrieval_mode)
                elapsed = round((time.time() - t_start) * 1000)

                result = self.evaluate_single(case, answer, sources)
                result.elapsed_ms = elapsed
                results.append(result)
            except Exception as e:
                results.append(TestResult(
                    case_id=case.id, query=case.query,
                    passed=False, score=0.0, checks=[],
                    error=str(e)[:200],
                ))

        total_elapsed = round((time.time() - t0) * 1000)
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        pct = round(passed / max(total, 1) * 100)

        report = {
            "summary": f"{passed}/{total} passed ({pct}%)",
            "passed": passed,
            "total": total,
            "pass_rate": pct,
            "elapsed_ms": total_elapsed,
            "results": [
                {
                    "case_id": r.case_id, "query": r.query,
                    "passed": r.passed, "score": r.score,
                    "checks": r.checks, "answer_length": r.answer_length,
                    "num_sources": r.num_sources, "top_score": r.top_score,
                    "elapsed_ms": r.elapsed_ms, "error": r.error,
                }
                for r in results
            ],
            "by_category": self._group_by_category(results),
        }

        # Persist report
        try:
            report_dir = Path("data/eval/reports")
            report_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            with open(report_dir / f"report_{ts}.json", "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except Exception as _e:
            log_suppressed(logger, _e)

        logger.info(f"Eval complete: {report['summary']} in {total_elapsed}ms")
        return report

    def _apply_rubric(self, result, case, rubric_result) -> None:
        """v17 Phase 22: when a rubric was judged, replace the brittle content
        keyword checks (contains/mentions) with the single semantic rubric verdict,
        then recompute pass/score. Structural checks (citations, sources, length,
        excludes) are preserved — the rubric only governs CONTENT correctness.
        """
        def _is_content_check(name: str) -> bool:
            n = name or ""
            return n.startswith("contains") or n.startswith("mentions")

        kept = [c for c in (result.checks or []) if not _is_content_check(c.get("name", ""))]
        kept.append({
            "name": f"rubric: {case.rubric[:60]}",
            "passed": bool(rubric_result["pass"]),
            "detail": (rubric_result.get("reason", "") or "")[:140],
        })
        passed_n = sum(1 for c in kept if c.get("passed"))
        result.checks = kept
        result.score = round(passed_n / max(len(kept), 1), 2)
        result.passed = result.score >= 0.8

    def _apply_judge_primary(self, result, judge_norm, threshold: float = 0.6) -> None:
        """v17 Phase 24b: for a generative system the SEMANTIC judge — not brittle
        string/length matching — should decide whether the CONTENT is correct. When a
        judge score is available (and no rubric/code-exec gate already governs the
        case), the content keyword checks (contains/mentions) and the length floor
        (≥N chars) are DEMOTED to soft signals (shown, not scored); the judge verdict
        (judge_norm ≥ threshold) becomes the content gate. Structural/safety checks
        (has citations, ≥N sources, excludes <forbidden>) stay HARD. This both fixes
        false fails on correct-but-concise / differently-worded answers AND catches
        keyword-gaming (keywords present but the judge says the answer is wrong).
        """
        def _is_soft(name: str) -> bool:
            return (name.startswith("contains") or name.startswith("mentions")
                    or "chars" in name)  # min_length floor

        kept = []
        for c in (result.checks or []):
            if _is_soft(c.get("name", "")):
                c = {**c, "soft": True, "detail": (c.get("detail", "") + " (软:语义裁判已判定)").strip()}
            kept.append(c)
        kept.append({
            "name": f"语义正确(judge≥{threshold})",
            "passed": bool(judge_norm is not None and judge_norm >= threshold),
            "detail": f"judge={judge_norm}",
        })
        hard = [c for c in kept if not c.get("soft")]
        passed_n = sum(1 for c in hard if c.get("passed"))
        result.checks = kept
        result.score = round(passed_n / max(len(hard), 1), 2)
        result.passed = result.score >= 0.8

    def _classify_failure(self, case, result, sources) -> str:
        """v16: bucket a failed case so the user knows what to fix.

        Buckets:
          - 检索未召回   : no/low sources, or retrieval recall is 0
          - 召回未引用   : sources exist but answer lacks citations (must_cite failed)
          - 答案偏题     : sources ok + cited, but content checks failed (LLM drift)
          - 评审低分     : judge score low despite checks passing
          - 其他
        """
        try:
            # Cases that don't need retrieval (greeting/code/chat, or min_sources=0)
            # should never be bucketed as a retrieval miss.
            no_retrieval_needed = (
                getattr(case, "min_sources", 0) == 0
                and case.category in ("greeting", "code", "chat", "refusal", "")
                and not case.relevant_docs
            )
            n_src = len(sources or [])
            rm = result.retrieval_metrics or {}
            recall = rm.get("recall@5", rm.get("recall@3"))
            check_by = {c.get("name", ""): c.get("passed") for c in (result.checks or [])
                        if not c.get("soft")}
            cite_failed = any("citation" in k or "cite" in k for k, v in check_by.items() if not v)

            if not no_retrieval_needed and (n_src == 0 or (recall is not None and recall == 0)):
                return "检索未召回"
            if cite_failed:
                return "召回未引用"
            # v17 Phase 22b: fabrication ("编造未支持") only makes sense when the
            # answer was supposed to be grounded in retrieved sources. For cases that
            # need no retrieval (code/greeting/capability → n_src==0), a low
            # faithfulness is an artifact of empty context, NOT fabrication.
            cm = result.component_metrics or {}
            faith = cm.get("faithfulness")
            if not no_retrieval_needed and n_src > 0 and faith is not None and faith < 0.5:
                return "编造未支持"
            content_failed = any(
                ("contain" in k or "mention" in k or "rubric" in k or "语义正确" in k) and not v
                for k, v in check_by.items()
            )
            if content_failed:
                return "答案偏题"
            if result.judge_score is not None and result.judge_score < 0.6:
                return "评审低分"
            return "其他"
        except Exception:
            return "其他"

    def _grade_case(self, case, chat_fn, judge_llm, M, judge_primary,
                    judge_threshold, component_metrics):
        """Grade ONE case end-to-end -> a fully-populated TestResult. Self-contained
        (mutates only the local result, never shared state) so it is safe to call
        concurrently. Run-level aggregation happens in the caller from the returned
        results, so sequential and parallel runs yield identical numbers."""
        try:
            t_start = time.time()
            answer, sources = chat_fn(case.query, case.retrieval_mode)
            elapsed = round((time.time() - t_start) * 1000)
            result = self.evaluate_single(case, answer, sources)
            result.elapsed_ms = elapsed
            result.category = case.category
            result.answer = (answer or "")[:2000]
            result.sources = [
                {"filename": s.get("filename") or s.get("id") or "",
                 "score": round(float(s.get("score", 0) or 0), 4),
                 "snippet": str(s.get("text") or s.get("snippet") or "")[:200]}
                for s in (sources or [])[:8]
            ]

            if case.relevant_docs:
                retrieved_ids = [str(s.get("filename") or s.get("id") or "")
                                 for s in sources]
                result.retrieval_metrics = M.retrieval_metrics(retrieved_ids, case.relevant_docs)

            if judge_llm is not None:
                if case.reference_answer:
                    j = M.judge_answer(case.query, answer, case.reference_answer, judge_llm)
                    if j.get("normalized") is not None:
                        result.judge_score = j["normalized"]
                        result.judge_reason = j.get("reason", "")
                else:
                    try:
                        from hashmm.evaluation.llm_judge import score_answer as _score_answer
                        jr = _score_answer(case.query, answer, sources, "", llm_fn=judge_llm)
                        if jr.available:
                            result.judge_score = jr.score
                            result.judge_reason = jr.rationale
                    except Exception:
                        pass

            if component_metrics and judge_llm is not None and sources:
                result.component_metrics = M.rag_component_metrics(
                    case.query, answer, sources, case.reference_answer, judge_llm)

            if case.rubric and judge_llm is not None:
                rc = M.rubric_check(
                    answer, case.rubric, judge_llm,
                    hints=(case.must_contain or case.must_mention_all))
                if rc.get("pass") is not None:
                    self._apply_rubric(result, case, rc)
            elif (judge_primary and result.judge_score is not None
                  and not case.code_runs):
                self._apply_judge_primary(result, result.judge_score, judge_threshold)

            if (not result.passed and case.reference_answer
                    and result.judge_score is not None
                    and result.judge_score >= 0.7):
                safety_failed = any(
                    (not c.get("passed"))
                    and str(c.get("name", "")).startswith("excludes")
                    for c in (result.checks or [])
                )
                if not safety_failed:
                    result.checks = list(result.checks or []) + [{
                        "name": "语义正确兑底(有参考答案·judge≥0.7)",
                        "passed": True,
                        "detail": (f"judge={result.judge_score}；关键词/最小长度/来源数/"
                                   f"检索召回未命中已降级为软信号（检索质量见 retrieval 指标）"),
                    }]
                    result.passed = True
                    result.score = max(result.score, round(result.judge_score, 2))

            if not result.passed:
                result.fail_reason = self._classify_failure(case, result, sources)
            return result
        except Exception as e:
            return TestResult(case_id=case.id, query=case.query,
                              passed=False, score=0.0, checks=[],
                              error=str(e)[:200])

    def run_all_enhanced(self, chat_fn, judge_llm=None, save_tag: str = "",
                         component_metrics: bool = True,
                         judge_primary: bool = True, judge_threshold: float = 0.6) -> dict:
        """v15 Phase 8: run golden tests with retrieval metrics + LLM-judge.

        Args:
            chat_fn: (query, mode) -> (answer, sources). sources are dicts that
                     may carry 'filename'/'id' used for retrieval metrics.
            judge_llm: optional LLM for semantic answer scoring (judge).
            save_tag: if set, persist this run for later regression comparison.

        Returns a report dict with per-case keyword score, retrieval metrics,
        and judge score, plus an aggregate summary.
        """
        from hashmm.evaluation import metrics as M

        t0 = time.time()
        results = []
        agg_retr: dict[str, list[float]] = {}
        agg_comp: dict[str, list[float]] = {}
        judge_scores = []

        # Per-case grading is independent; sequential by default (byte-identical to
        # before) or bounded-concurrent when HASHMM_EVAL_CONCURRENCY>1. The slow parts
        # (RAG generation + DeepSeek judge) are exactly what the live server already
        # serves concurrently, so bounded parallelism is safe and cuts a ~100-min run to
        # a fraction. Aggregates are rebuilt from results below, so sequential and
        # parallel runs produce identical summary numbers.
        import os as _os
        try:
            _workers = int(_os.environ.get("HASHMM_EVAL_CONCURRENCY", "1") or "1")
        except ValueError:
            _workers = 1
        _workers = max(1, min(_workers, 8))
        if _workers <= 1:
            results = [self._grade_case(case, chat_fn, judge_llm, M, judge_primary,
                                        judge_threshold, component_metrics)
                       for case in self._cases]
        else:
            from concurrent.futures import ThreadPoolExecutor
            logger.info(f"[eval] 并发评测 workers={_workers}（{len(self._cases)} 例）")
            with ThreadPoolExecutor(max_workers=_workers) as _ex:
                results = list(_ex.map(
                    lambda _c: self._grade_case(_c, chat_fn, judge_llm, M, judge_primary,
                                                judge_threshold, component_metrics),
                    self._cases))

        # Rebuild aggregates from results — identical to the old per-iteration appends:
        # every value lives on its TestResult, and means/sums are order-independent.
        for r in results:
            for k, v in (r.retrieval_metrics or {}).items():
                agg_retr.setdefault(k, []).append(v)
            if r.judge_score is not None:
                judge_scores.append(r.judge_score)
            for k in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
                v = (r.component_metrics or {}).get(k)
                if v is not None:
                    agg_comp.setdefault(k, []).append(v)

        total_elapsed = round((time.time() - t0) * 1000)
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        avg_score = round(sum(r.score for r in results) / max(total, 1), 3)
        avg_retr = {k: round(sum(v) / len(v), 4) for k, v in agg_retr.items() if v}
        avg_judge = round(sum(judge_scores) / len(judge_scores), 3) if judge_scores else None
        # v17 Phase 22: aggregate component metrics (means over cases where computed)
        avg_comp = {k: round(sum(v) / len(v), 4) for k, v in agg_comp.items() if v}

        # v16 Phase 13: per-category breakdown (which kinds of questions do worst)
        by_cat: dict[str, dict] = {}
        for r in results:
            cat = r.category or "unknown"
            b = by_cat.setdefault(cat, {"total": 0, "passed": 0, "score_sum": 0.0,
                                        "retr": {}})
            b["total"] += 1
            b["passed"] += 1 if r.passed else 0
            b["score_sum"] += r.score
            for k, v in (r.retrieval_metrics or {}).items():
                b["retr"].setdefault(k, []).append(v)
        category_stats = {
            cat: {
                "total": b["total"], "passed": b["passed"],
                "pass_rate": round(b["passed"] / max(b["total"], 1), 3),
                "avg_score": round(b["score_sum"] / max(b["total"], 1), 3),
                "retrieval": {k: round(sum(v) / len(v), 4) for k, v in b["retr"].items() if v},
            }
            for cat, b in by_cat.items()
        }

        # v16 Phase 13: failure buckets (what to fix)
        fail_buckets: dict[str, int] = {}
        for r in results:
            if not r.passed and r.fail_reason:
                fail_buckets[r.fail_reason] = fail_buckets.get(r.fail_reason, 0) + 1

        # v17 Phase 23: held-out integrity — compare train vs heldout pass rates.
        # A large gap (heldout << train) means we've overfit the eval set / prompts.
        split_id = {c.id: getattr(c, "split", "train") for c in self._cases}
        split_agg: dict[str, dict] = {}
        for r in results:
            sp = split_id.get(r.case_id, "train")
            s = split_agg.setdefault(sp, {"total": 0, "passed": 0})
            s["total"] += 1
            s["passed"] += 1 if r.passed else 0
        split_breakdown = {
            sp: {"total": s["total"], "passed": s["passed"],
                 "pass_rate": round(s["passed"] / max(s["total"], 1), 3)}
            for sp, s in split_agg.items()
        }
        _tr = split_breakdown.get("train", {}).get("pass_rate")
        _ho = split_breakdown.get("heldout", {}).get("pass_rate")
        split_breakdown["overfit_gap"] = (round(_tr - _ho, 3)
                                          if _tr is not None and _ho is not None else None)

        # v17 Phase 17: layered scoring — separate the three quality layers so
        # you can see WHERE quality is lost (retrieval vs generation vs overall).
        #   retrieval layer: recall@5 over cases that have relevant_docs labels
        #   generation layer: judge score over cases that have a reference_answer
        #   end-to-end layer: pass_rate over all cases
        retr_layer = avg_retr.get("recall@5", avg_retr.get("recall@3"))
        layered = {
            "retrieval": {
                "metric": "recall@5",
                "score": retr_layer,
                "n_cases": sum(1 for c in self._cases if c.relevant_docs),
            },
            "generation": {
                "metric": "judge(0-1)",
                "score": avg_judge,
                "n_cases": len(judge_scores),
            },
            "end_to_end": {
                "metric": "pass_rate",
                "score": round(passed / max(total, 1), 3),
                "n_cases": total,
            },
        }

        # 把参考答案/评分标准带进每条结果，便于详细报告里看「期望 vs 实际 + 怎么判的」
        ref_by_id = {c.id: getattr(c, "reference_answer", "") for c in self._cases}
        rubric_by_id = {c.id: getattr(c, "rubric", "") for c in self._cases}
        report = {
            "summary": {
                "passed": passed, "total": total,
                "pass_rate": round(passed / max(total, 1), 3),
                "avg_score": avg_score,
                "avg_judge_score": avg_judge,
                "retrieval": avg_retr,
                # v17 Phase 22: RAGAS-style component metrics (where quality is lost)
                "component_metrics": avg_comp,
                # v17: layered breakdown (retrieval / generation / end-to-end)
                "layered": layered,
                # v16: coverage so the UI can explain empty judge/retrieval metrics
                "cases_with_reference": sum(1 for c in self._cases if c.reference_answer),
                "cases_with_relevant_docs": sum(1 for c in self._cases if c.relevant_docs),
                "judge_enabled": judge_llm is not None,
                "by_category": category_stats,
                "fail_buckets": fail_buckets,
                # v17 Phase 23: train vs held-out (overfitting integrity check)
                "split_breakdown": split_breakdown,
            },
            "results": [
                {"case_id": r.case_id, "query": r.query, "passed": r.passed,
                 "score": r.score, "checks": r.checks, "category": r.category,
                 "retrieval_metrics": r.retrieval_metrics,
                 "judge_score": r.judge_score, "judge_reason": r.judge_reason,
                 "component_metrics": r.component_metrics,
                 "elapsed_ms": r.elapsed_ms, "error": r.error,
                 "answer": r.answer, "sources": r.sources, "fail_reason": r.fail_reason,
                 "reference_answer": ref_by_id.get(r.case_id, ""),
                 "rubric": rubric_by_id.get(r.case_id, "")}
                for r in results
            ],
            "elapsed_ms": total_elapsed,
        }

        if save_tag:
            try:
                rid = M.save_run(report, tag=save_tag)
                report["run_id"] = rid
            except Exception as _e:
                log_suppressed(logger, _e)

        logger.info(f"Enhanced eval: {passed}/{total} passed, avg_score={avg_score}, "
                    f"judge={avg_judge}, retrieval={avg_retr}")
        return report

    def _group_by_category(self, results: list[TestResult]) -> dict:
        groups: dict[str, dict] = {}
        for r in results:
            cat = "unknown"
            for c in self._cases:
                if c.id == r.case_id:
                    cat = c.category
                    break
            if cat not in groups:
                groups[cat] = {"total": 0, "passed": 0}
            groups[cat]["total"] += 1
            if r.passed:
                groups[cat]["passed"] += 1
        return groups
