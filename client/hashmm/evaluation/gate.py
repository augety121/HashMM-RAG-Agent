"""v17 Phase 72 — evaluation gate runner (CI quality gate for retrieval/agent).

The 2026 best practice is to make evaluation a **gate**, not a one-off script:
every change to retrieval / the agent re-runs the golden + adversarial sets and
**fails CI if quality drops below an absolute threshold or regresses vs a saved
baseline**. This is what lets the upcoming retrieval changes (Contextual
Retrieval already landed; Agentic multi-hop next) be quantified and protected
from silent regression.

This runner is **provider-agnostic and fully injectable**:
- ``answer_fn(query) -> {"answer": str, "sources": list}`` (or a bare str) is
  supplied by the caller. In CI you wire your real pipeline; in unit tests we
  pass a stub. The runner core needs no models.
- An optional ``retrieve_fn(query) -> list[doc_id]`` enables recall@k / nDCG
  gating for id-labeled retrieval sets (the current golden set is contract-based:
  ``must_contain_any`` / ``min_sources`` / ``min_length``, which we evaluate too).

Gate logic:
- per-case **contract** pass/fail, aggregated to an overall + per-category pass rate;
- fail if overall rate < ``overall_min_pass_rate``;
- fail on **regression** vs an optional saved baseline (rate dropped by more than
  ``max_regression``).
- ``gate_main`` returns a CI exit code: 0 pass, 1 gate failed, 2 misconfigured.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed
from hashmm.evaluation.metrics import retrieval_metrics

logger = get_logger(__name__)

_EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CASE_FILES = [_EVAL_DIR / "golden_cases_100.json", _EVAL_DIR / "adversarial_cases.json",
                      _EVAL_DIR / "quality_cases.json"]

DEFAULT_THRESHOLDS = {
    "overall_min_pass_rate": 0.90,   # absolute floor
    "max_regression": 0.05,          # vs baseline, per overall/category
    "recall_at_k": 5,                # which k to gate on (id-labeled cases only)
    "min_recall": 0.70,              # floor for recall@k (id-labeled cases only)
    # V103.51 「质量门」: LLM-as-judge 阈值。judge 只对显式开启的 case 生效
    # (case["judge"]=true 或带 case["rubric"]/case["expect"])，且仅在契约已通过后追加。
    # judge 不可用(无模型/解析失败)时该维不计入——绝不因 judge 故障判失败。
    "judge_threshold": 0.60,         # judge 0..1 总分下限(合格≈3/5 起步)
    "judge_enabled": True,           # 总开关；关掉则退回纯契约门(与 V103.50 行为一致)
}


# ── case loading ─────────────────────────────────────────────────────────
def load_cases(paths: list[str | Path] | None = None) -> list[dict]:
    """Load + concat case files (JSON list of case dicts). Missing files skipped."""
    paths = paths or DEFAULT_CASE_FILES
    cases: list[dict] = []
    for p in paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            if isinstance(data, list):
                cases.extend(data)
        except FileNotFoundError:
            logger.warning(f"eval case file not found: {p}")
        except Exception as e:
            log_suppressed(logger, e)
    return cases


# ── per-case contract evaluation ─────────────────────────────────────────
@dataclass
class CaseResult:
    id: str
    category: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    n_sources: int = 0
    metrics: dict = field(default_factory=dict)
    # V103.51: LLM-judge 质量分(0..1)。judge_applied=False 表示该 case 未启用 judge
    # 或 judge 不可用——这两种情况下 judge_score 不参与判定。
    judge_score: float | None = None
    judge_applied: bool = False
    judge_dimensions: dict = field(default_factory=dict)


def check_contract(case: dict, answer: str, sources: list) -> tuple[bool, list[str]]:
    """Evaluate the answer contract a golden/adversarial case encodes.

    Applicable checks (each only when the case specifies it):
      - must_contain_any: answer contains at least one of the substrings;
      - min_sources: number of grounded sources >= min_sources;
      - min_length: answer length >= min_length.
    """
    reasons: list[str] = []
    ans = (answer or "")
    mca = case.get("must_contain_any")
    if mca:
        if not any(sub in ans for sub in mca):
            reasons.append(f"missing must_contain_any (any of {len(mca)} substrings)")
    min_sources = case.get("min_sources")
    if isinstance(min_sources, int):
        if len(sources or []) < min_sources:
            reasons.append(f"sources {len(sources or [])} < min_sources {min_sources}")
    min_length = case.get("min_length")
    if isinstance(min_length, int):
        if len(ans.strip()) < min_length:
            reasons.append(f"answer length {len(ans.strip())} < min_length {min_length}")
    return (len(reasons) == 0, reasons)


def _normalize_answer(out: Any) -> tuple[str, list]:
    """Accept answer_fn returning a dict {answer, sources} or a bare string."""
    if isinstance(out, dict):
        return str(out.get("answer", "")), list(out.get("sources", []) or [])
    return (out if isinstance(out, str) else str(out or "")), []


def _case_wants_judge(case: dict, th: dict) -> bool:
    """该 case 是否启用 LLM-judge：总开关开 且 (case 显式 judge=true 或带 rubric/expect)。
    默认不对所有 case 开 judge——只对标注了质量期望的 case 追加质量门，避免给纯
    greeting/factual 这类已被契约充分覆盖的 case 增加无谓的模型调用与波动。"""
    if not th.get("judge_enabled", True):
        return False
    if case.get("judge") is True:
        return True
    return bool(case.get("rubric") or case.get("expect"))


def evaluate_case(case: dict, answer_fn: Callable | None,
                  retrieve_fn: Callable | None = None,
                  thresholds: dict | None = None,
                  judge_fn: Callable | None = None) -> CaseResult:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    cid = str(case.get("id", "?"))
    cat = str(case.get("category", "uncategorized"))
    reasons: list[str] = []
    passed = True
    n_sources = 0
    metrics: dict = {}
    judge_score: float | None = None
    judge_applied = False
    judge_dims: dict = {}
    answer, sources = "", []

    # Contract checks (need an answer_fn).
    if answer_fn is not None:
        try:
            answer, sources = _normalize_answer(answer_fn(case.get("query", "")))
        except Exception as e:
            log_suppressed(logger, e)
            return CaseResult(cid, cat, False, [f"answer_fn raised: {e}"], 0, {})
        n_sources = len(sources)
        ok, why = check_contract(case, answer, sources)
        passed = passed and ok
        reasons += why

    # Optional id-based recall gate (only for cases with labeled relevant ids).
    rel = case.get("relevant_ids") or case.get("relevant_doc_ids")
    if rel and retrieve_fn is not None:
        try:
            retrieved = list(retrieve_fn(case.get("query", "")) or [])
            metrics = retrieval_metrics([str(x) for x in retrieved], [str(x) for x in rel],
                                        ks=(th["recall_at_k"],))
            r = metrics.get(f"recall@{th['recall_at_k']}", 0.0)
            if r < th["min_recall"]:
                passed = False
                reasons.append(f"recall@{th['recall_at_k']} {r:.2f} < {th['min_recall']}")
        except Exception as e:
            log_suppressed(logger, e)

    # V103.51 「质量门」: LLM-as-judge。仅当 (1) case 启用 judge、(2) 有 answer、
    # (3) 契约已通过 时追加。judge 不可用(无模型/解析失败)→ 不计入,passed 不变。
    # 顺序保证: judge 是「契约红线之上还要答得好」,不会把一个契约就该挂的 case 救活,
    # 也不会因 judge 故障误杀。
    if answer_fn is not None and _case_wants_judge(case, th):
        try:
            from hashmm.evaluation.llm_judge import score_answer
            jr = score_answer(case.get("query", ""), answer, sources,
                              expectation=str(case.get("rubric") or case.get("expect") or ""),
                              llm_fn=judge_fn)
            if jr.available:
                judge_applied = True
                judge_score = jr.score
                judge_dims = dict(jr.dimensions)
                if passed and jr.score < th.get("judge_threshold", 0.60):
                    passed = False
                    reasons.append(
                        f"judge_score {jr.score:.2f} < judge_threshold "
                        f"{th.get('judge_threshold', 0.60)} ({jr.rationale[:80]})")
        except Exception as e:
            log_suppressed(logger, e)

    return CaseResult(cid, cat, passed, reasons, n_sources, metrics,
                      judge_score=judge_score, judge_applied=judge_applied,
                      judge_dimensions=judge_dims)


# ── gate ─────────────────────────────────────────────────────────────────
@dataclass
class GateReport:
    n: int
    passed: bool
    overall_pass_rate: float
    by_category: dict
    breaches: list[str]
    regressions: list[str]
    case_results: list[CaseResult]

    def to_dict(self) -> dict:
        judged = [c for c in self.case_results if c.judge_applied]
        judge_summary = None
        if judged:
            avg = sum((c.judge_score or 0.0) for c in judged) / len(judged)
            judge_summary = {
                "n_judged": len(judged),
                "avg_judge_score": round(avg, 4),
                "min_judge_score": round(min((c.judge_score or 0.0) for c in judged), 4),
            }
        return {
            "n": self.n, "passed": self.passed,
            "overall_pass_rate": round(self.overall_pass_rate, 4),
            "by_category": self.by_category,
            "breaches": self.breaches, "regressions": self.regressions,
            "judge": judge_summary,
            "failures": [{"id": c.id, "category": c.category, "reasons": c.reasons,
                          "judge_score": c.judge_score}
                         for c in self.case_results if not c.passed],
        }


def _rates(results: list[CaseResult]) -> tuple[float, dict]:
    n = len(results)
    overall = sum(1 for r in results if r.passed) / n if n else 0.0
    by: dict = {}
    for r in results:
        b = by.setdefault(r.category, {"pass": 0, "total": 0})
        b["total"] += 1
        if r.passed:
            b["pass"] += 1
    for cat, b in by.items():
        b["rate"] = round(b["pass"] / b["total"], 4) if b["total"] else 0.0
    return overall, by


def run_gate(cases: list[dict], answer_fn: Callable | None, *,
             retrieve_fn: Callable | None = None,
             thresholds: dict | None = None,
             baseline: dict | None = None,
             judge_fn: Callable | None = None) -> GateReport:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    results = [evaluate_case(c, answer_fn, retrieve_fn, th, judge_fn=judge_fn) for c in cases]
    overall, by_cat = _rates(results)

    breaches: list[str] = []
    if overall < th["overall_min_pass_rate"]:
        breaches.append(f"overall_pass_rate {overall:.3f} < {th['overall_min_pass_rate']}")

    regressions: list[str] = []
    if baseline:
        base_overall = baseline.get("overall_pass_rate")
        if isinstance(base_overall, (int, float)) and overall < base_overall - th["max_regression"]:
            regressions.append(f"overall regressed {base_overall:.3f} → {overall:.3f}")
        base_by = baseline.get("by_category", {})
        for cat, b in by_cat.items():
            bc = base_by.get(cat, {}).get("rate")
            if isinstance(bc, (int, float)) and b["rate"] < bc - th["max_regression"]:
                regressions.append(f"[{cat}] regressed {bc:.3f} → {b['rate']:.3f}")

    passed = not breaches and not regressions
    return GateReport(len(results), passed, overall, by_cat, breaches, regressions, results)


# ── baseline persistence ─────────────────────────────────────────────────
def save_baseline(report: GateReport, path: str | Path) -> None:
    Path(path).write_text(json.dumps({
        "overall_pass_rate": round(report.overall_pass_rate, 4),
        "by_category": report.by_category,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load_baseline(path: str | Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


# ── CI entrypoint ──────────────────────────────────────────────────────────
def gate_main(answer_fn: Callable | None, *, retrieve_fn: Callable | None = None,
              case_paths: list[str | Path] | None = None,
              thresholds: dict | None = None,
              baseline_path: str | Path | None = None,
              update_baseline: bool = False,
              judge_fn: Callable | None = None) -> int:
    """Run the gate and return a CI exit code (0 pass / 1 fail / 2 misconfigured)."""
    if answer_fn is None and retrieve_fn is None:
        print("eval-gate: no answer_fn/retrieve_fn wired. Pass your pipeline in. "
              "See V17_PHASE72 changelog for the 5-line recipe.")
        return 2
    cases = load_cases(case_paths)
    if not cases:
        print("eval-gate: no cases loaded.")
        return 2
    baseline = load_baseline(baseline_path) if (baseline_path and not update_baseline) else None
    report = run_gate(cases, answer_fn, retrieve_fn=retrieve_fn,
                      thresholds=thresholds, baseline=baseline, judge_fn=judge_fn)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    if update_baseline and baseline_path:
        save_baseline(report, baseline_path)
        print(f"eval-gate: baseline written to {baseline_path}")
        return 0
    print(f"eval-gate: {'PASS' if report.passed else 'FAIL'} "
          f"(overall {report.overall_pass_rate:.3f}, n={report.n})")
    return 0 if report.passed else 1


def try_build_answer_fn() -> Callable | None:
    """Best-effort wiring of a real answer_fn from the retrieval pipeline.

    Returns None when models/index aren't available (e.g. CI without GPU) so the
    caller can decide what to do. Sources come from the retrieval bridge; answer
    generation must be wired to your LLM (left to the caller's recipe)."""
    try:
        from hashmm.retriever_bridge import get_pipeline, kb_search_bridge
        if get_pipeline() is None:
            return None

        def _answer_fn(query: str) -> dict:
            res = kb_search_bridge({"query": query})
            return {"answer": "", "sources": res.get("results", [])}
        return _answer_fn
    except Exception as e:
        log_suppressed(logger, e)
        return None


# ── runnable CLI (V103.41) ────────────────────────────────────────────────
# 把质量门做成「一条命令」：对金标准案例跑契约（must_contain_any / min_sources /
# min_length）+ recall@k，回归基线就退出码 1。这是方案 P0「立验证者」的可执行入口。
#
#   真机（配好知识库 + 模型）：
#     python -m hashmm.evaluation.gate --gate 0.85 --baseline hashmm/evaluation/baseline.json
#     首次跑加 --update-baseline 写基线；之后每次跑对比基线、回归就红。
#   CI / 无模型环境（仅自检 harness 接线，不需要索引/模型）：
#     python -m hashmm.evaluation.gate --stub
def _build_rag_answer_fn():
    """query -> {answer, sources}：最小同步 RAG（复用真实检索 + 真实 LLM）。

    不是完整流式路径（无工具/编排），但覆盖金标准案例真正考的核心：检索是否召回、
    答案是否落在来源上、库外问题是否如实说「没有」。模型/索引不可用时返回 None。
    """
    try:
        from hashmm.retriever_bridge import get_pipeline, kb_search_bridge
        if get_pipeline() is None:
            return None
        from hashmm.api.core.services import ServiceRegistry
        ServiceRegistry.ensure_loaded()
        try:
            from hashmm.api.retrieval import SYS as _SYS
            kb_prompt = _SYS.get("kb", "")
        except Exception:
            kb_prompt = "基于以下检索结果回答问题；检索结果不足以回答时，明确说「未找到」。"

        def _answer(query: str) -> dict:
            res = kb_search_bridge({"query": query})
            sources = res.get("results", []) or []
            ctx = "\n\n".join(
                f"[{i + 1}] {((s.get('text') or s.get('content') or '') if isinstance(s, dict) else str(s))[:800]}"
                for i, s in enumerate(sources[:8])
            )
            prompt = f"{kb_prompt}\n\n检索结果：\n{ctx or '（无检索结果）'}\n\n问题：{query}\n请基于检索结果回答。"
            try:
                answer = ServiceRegistry.call_llm(prompt) or ""
            except Exception as _e:
                log_suppressed(logger, _e)
                answer = ""
            return {"answer": answer, "sources": sources}
        return _answer
    except Exception as e:
        log_suppressed(logger, e)
        return None


def _build_retrieve_fn():
    """query -> list[doc_id]：给有 relevant_docs 标注的案例算 recall@k / nDCG。"""
    try:
        from hashmm.retriever_bridge import get_pipeline, kb_search_bridge
        if get_pipeline() is None:
            return None

        def _retrieve(query: str) -> list:
            res = kb_search_bridge({"query": query})
            ids = []
            for r in res.get("results", []) or []:
                if isinstance(r, dict):
                    did = r.get("doc_id") or r.get("id") or r.get("source") or r.get("document")
                    if did is not None:
                        ids.append(did)
            return ids
        return _retrieve
    except Exception as e:
        log_suppressed(logger, e)
        return None


def _build_http_judge_fn(base_url: str, token: str | None = None):
    """构造 HTTP 模式下的 judge LLM：打**正在运行的后端**的 /api/llm/tools 拿一次补全。

    judge 与被评后端用同一个部署的模型——这是 model-graded eval 的常见做法（grader 用强
    模型；这里至少保证 judge 真能跑、与产品同源）。返回一个 ``quick_call(system,user,...)``
    兼容对象，便于 llm_judge.score_answer 直接调用。任何失败都返回空串（→ judge 标记不可用，
    不会误判）。
    """
    import json as _json
    import urllib.request as _ur
    base = base_url.rstrip("/")

    class _HttpJudge:
        def quick_call(self, system: str, user: str, max_tokens: int = 200) -> str:
            body = _json.dumps({
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens, "temperature": 0,
            }).encode("utf-8")
            headers = {"Content-Type": "application/json", "X-HashMM-Eval": "1"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            req = _ur.Request(base + "/api/llm/tools", data=body, headers=headers)
            try:
                with _ur.urlopen(req, timeout=60) as resp:
                    d = _json.loads(resp.read().decode("utf-8"))
                # 兼容多种返回形状：{answer}/{content}/{choices:[{message:{content}}]}
                if isinstance(d, dict):
                    if d.get("answer"):
                        return str(d["answer"])
                    if d.get("content"):
                        return str(d["content"])
                    ch = d.get("choices")
                    if isinstance(ch, list) and ch:
                        msg = (ch[0] or {}).get("message") or {}
                        return str(msg.get("content", ""))
                return str(d)
            except Exception as e:
                log_suppressed(logger, e)
                return ""

        def __call__(self, prompt: str) -> str:
            return self.quick_call("你是严格公正的答案质量评审", prompt)

    return _HttpJudge()


def _build_local_judge_fn():
    """本地模式 judge：复用 app_state.llm_fn（与被评 RAG 同一模型）。无则 None。"""
    try:
        from hashmm.api.core.services import ServiceRegistry
        ServiceRegistry.ensure_loaded()
        fn = getattr(ServiceRegistry, "llm_fn", None)
        return fn
    except Exception as e:
        log_suppressed(logger, e)
        return None


def _build_http_answer_fn(base_url: str, token: str | None = None):
    """query -> {answer, sources}：打一个**正在运行的后端**的 /api/chat。

    用它测「跑起来的后端」——AutoDL / 容器 / 打包 app 的本地后端（默认 127.0.0.1:17680）。
    测试机只要能发 HTTP 就行，**不需要 transformers / 模型 / GPU**——真实检索+生成在后端那边跑。
    这样同一套金标准能测任何部署，且测的就是用户实际走的那条路径。
    """
    import json as _json
    import urllib.request as _ur
    base = base_url.rstrip("/")

    def _answer(query: str) -> dict:
        body = _json.dumps({"message": query}).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-HashMM-Eval": "1"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = _ur.Request(base + "/api/chat", data=body, headers=headers)
        try:
            with _ur.urlopen(req, timeout=120) as resp:
                d = _json.loads(resp.read().decode("utf-8"))
            return {"answer": d.get("answer", "") or "", "sources": d.get("sources", []) or []}
        except Exception as e:
            log_suppressed(logger, e)
            return {"answer": "", "sources": []}
    return _answer


def _build_stream_answer_fn(base_url: str, token: str | None = None, dump_dir: str | None = None):
    """query -> {answer, sources}：打**用户实际走的流式端点** /api/conversations/{id}/stream，
    解析 SSE（token 事件累加成答案，done 事件取 sources）。这才是用户真实体验的那条路径。

    dump_dir：若指定，把每条 query 的原始 SSE 事件流 + 解析结果落盘，便于排查
    “为什么这条答案是空/过短/缺关键词”——这是定位 length-18 / length-61 这类
    问题的决定性手段（直接看后端到底发了哪些事件、token 收了几个字）。
    """
    import json as _sj
    import urllib.request as _ur
    import uuid as _uuid
    base = base_url.rstrip("/")
    _dump_n = [0]  # 闭包计数器，给 dump 文件编号

    if dump_dir:
        import os as _os
        try:
            _os.makedirs(dump_dir, exist_ok=True)
        except Exception as _e:
            log_suppressed(logger, _e)

    def _answer(query: str) -> dict:
        import time as _t
        conv = _uuid.uuid4().hex
        body = _sj.dumps({"message": query}).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-HashMM-Eval": "1"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = _ur.Request(f"{base}/api/conversations/{conv}/stream", data=body, headers=headers)
        parts: list[str] = []
        sources: list = []
        file_urls: list[str] = []      # V103.47: 代码/文档类答案的正文在文件里
        file_previews: list[str] = []  # 文件事件自带的预览片段（兜底）
        _raw_events: list[str] = []    # V103.48: --dump 用，记录每一行原始 SSE
        _event_counts: dict = {}       # 各类事件计数（token/delta/trace/...）
        _start = _t.time()
        _MAX_SEC = 120  # 单条墙钟上限：后端卡住（如没配模型）也不会无限等
        try:
            with _ur.urlopen(req, timeout=60) as resp:
                cur = None
                for raw in resp:
                    if _t.time() - _start > _MAX_SEC:
                        logger.warning(f"stream 单条超过 {_MAX_SEC}s，跳过（后端可能没配模型/卡住）")
                        break
                    line = raw.decode("utf-8", "ignore").rstrip("\r\n")
                    if dump_dir and line.strip():
                        _raw_events.append(line)
                    if line.startswith("event:"):
                        cur = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            d = _sj.loads(line[5:].strip())
                        except Exception:
                            continue
                        if dump_dir and cur:
                            _event_counts[cur] = _event_counts.get(cur, 0) + 1
                        if cur == "token" or "content" in d:
                            parts.append(str(d.get("content", "")))
                        # V103.47: 代码任务把代码存成可下载文件，正文只在文件里。
                        # 评测用户真实体验 = 应当把文件正文计入答案，否则只看到
                        # "已生成文件…"的短旁白，min_length / must_contain 全误判。
                        if cur == "file":
                            _u = d.get("download_url") or d.get("url") or ""
                            if _u:
                                file_urls.append(_u)
                            # V103.48: 优先用文件事件自带的完整正文（代码/文本），
                            # 不必再去抓 URL；这是代码类答案最可靠的来源。
                            _ct = d.get("_content") or d.get("content") or ""
                            if _ct:
                                file_previews.append(str(_ct))
                            else:
                                _pv = d.get("_preview") or d.get("preview") or ""
                                if _pv:
                                    file_previews.append(str(_pv))
                        # 预发来源事件（done 里的 sources 多为空，真正来源在这里）。
                        if cur == "sources" and d.get("sources"):
                            sources = d.get("sources") or sources
                        if cur == "done" and d.get("sources"):
                            sources = d.get("sources") or sources
                            break
        except Exception as e:
            log_suppressed(logger, e)

        answer = "".join(parts)
        # 把文件正文拼进答案。优先用文件事件自带的完整正文（file_previews，V103.48
        # 起对 create_file 带完整 content）；自带正文缺失时，才去抓 URL（best-effort，
        # 只抓文本类，二进制 docx/pdf 抓回是乱码会污染判定）。代码任务的代码就是答案。
        _TEXT_EXT = (".py", ".js", ".ts", ".java", ".cpp", ".c", ".h", ".go",
                     ".rs", ".rb", ".sh", ".sql", ".r", ".m", ".txt", ".md",
                     ".json", ".csv", ".yaml", ".yml", ".html", ".css", ".xml")
        _file_bodies: list[str] = list(file_previews)  # 自带正文优先
        if not _file_bodies:
            for _u in file_urls[:4]:
                _ul = _u.lower().split("?")[0]
                if not _ul.endswith(_TEXT_EXT):
                    continue
                _full = _u if _u.startswith("http") else f"{base}{_u if _u.startswith('/') else '/' + _u}"
                try:
                    _fr = _ur.Request(_full, headers=({"Authorization": f"Bearer {token}"} if token else {}))
                    with _ur.urlopen(_fr, timeout=30) as _fresp:
                        _body = _fresp.read().decode("utf-8", "ignore")
                        if _body.strip():
                            _file_bodies.append(_body)
                except Exception as _e:
                    log_suppressed(logger, _e)
        if _file_bodies:
            answer = (answer + "\n\n" + "\n\n".join(_file_bodies)).strip()

        # V103.48: --dump —— 把这条 query 的诊断信息落盘。包含：原始 SSE 事件流、
        # 各类事件计数、token 拼出的正文、抓到的文件数、最终答案长度。一眼看出
        # “答案为什么短”：是 token 没几个字（生成短）、还是走了 clarify/早返回、
        # 还是代码进了文件没被算进来。
        if dump_dir:
            _dump_n[0] += 1
            try:
                import os as _os
                _safe = "".join(ch if ch.isalnum() else "_" for ch in query[:30])
                _fp = _os.path.join(dump_dir, f"{_dump_n[0]:03d}_{_safe}.txt")
                with open(_fp, "w", encoding="utf-8") as _f:
                    _f.write(f"QUERY: {query}\n")
                    _f.write(f"事件计数: {_event_counts}\n")
                    _f.write(f"token拼出正文长度: {len(''.join(parts))}\n")
                    _f.write(f"抓到文件正文数: {len(_file_bodies)}\n")
                    _f.write(f"最终答案长度: {len(answer.strip())}\n")
                    _f.write(f"sources数: {len(sources)}\n")
                    _f.write("=" * 60 + "\n最终答案:\n" + answer + "\n")
                    _f.write("=" * 60 + "\n原始SSE事件流:\n")
                    _f.write("\n".join(_raw_events))
            except Exception as _e:
                log_suppressed(logger, _e)

        return {"answer": answer, "sources": sources}
    return _answer


def _preflight() -> tuple[bool, list[str]]:
    """跑真实门前的前置检查：索引非空 + LLM 已配置。返回 (ok, 警告列表)。"""
    warns: list[str] = []
    try:
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        nvec = 0
        if pipe is not None:
            vi = getattr(pipe, "vector_index", None)
            nvec = getattr(vi, "total_vectors", 0) or 0
        if nvec == 0:
            warns.append("检索索引为空（0 向量）——没有指向你的知识库，需要检索的案例都会失败。")
    except Exception as e:
        warns.append(f"无法读取检索管线（{type(e).__name__}）。")
    try:
        from hashmm.api.core.services import ServiceRegistry
        ServiceRegistry.ensure_loaded()
        if getattr(ServiceRegistry, "llm_fn", None) is None:
            warns.append("未配置可用 LLM——答案会是空的，含 must_contain_any / min_length 的案例都会失败。")
    except Exception as e:
        warns.append(f"无法读取 LLM 配置（{type(e).__name__}）。")
    return (len(warns) == 0, warns)


if __name__ == "__main__":
    import argparse
    import sys as _sys

    ap = argparse.ArgumentParser(
        description="HashMM 评估质量门：对金标准案例跑契约 + recall 检查，回归就退出码 1（方案 P0 验证者）。")
    ap.add_argument("--cases", nargs="*", default=None,
                    help="案例 JSON 路径；默认 golden_cases_100.json + adversarial_cases.json")
    ap.add_argument("--gate", type=float, default=None, help="总体最低通过率（默认 0.90）")
    ap.add_argument("--min-recall", type=float, default=None, help="recall@k 下限（默认 0.70）")
    ap.add_argument("--baseline", default=None, help="基线 JSON 路径（用于回归判定）")
    ap.add_argument("--update-baseline", action="store_true", help="把本次结果写成基线")
    ap.add_argument("--stub", action="store_true",
                    help="不接真实管线，用桩 answer_fn 自检 harness（CI 用，无需模型/索引）")
    ap.add_argument("--api", default=None,
                    help="对一个正在运行的后端测（如 http://127.0.0.1:17680 或 ）；测试机无需 transformers/模型")
    ap.add_argument("--token", default=None, help="后端需要鉴权时的 Bearer token（或用 --user/--password 自动登录）")
    ap.add_argument("--user", default=None, help="HTTP 模式自动登录用的用户名（如 admin）")
    ap.add_argument("--password", default=None, help="HTTP 模式自动登录用的密码")
    ap.add_argument("--force-empty", action="store_true",
                    help="即使探针发现后端没配 LLM 也强行跑并写基线（默认拒绝，避免假基线）。")
    ap.add_argument("--dump", nargs="?", const="eval_dump", default=None,
                    help="把每条 case 的原始 SSE 事件流+解析结果落盘到指定目录（默认 eval_dump/），用于排查为什么某条答案是空/过短/缺关键词。仅 --stream/--api 模式有效。")
    ap.add_argument("--stream", action="store_true",
                    help="HTTP 模式下打用户实际走的流式端点 /api/conversations/{id}/stream（更贴近真实体验；默认打 /api/chat）")
    ap.add_argument("--judge", dest="judge", action="store_true", default=None,
                    help="开启 LLM-as-judge 质量门：对带 judge/rubric 的案例追加有用性/完整性/落地/条理打分（V103.51）")
    ap.add_argument("--no-judge", dest="judge", action="store_false",
                    help="关闭 LLM-judge，退回纯契约门（与 V103.50 行为一致）")
    ap.add_argument("--judge-threshold", type=float, default=None,
                    help="judge 0..1 总分下限（默认 0.60≈3/5）；低于即判该案例不达质量标准")
    args = ap.parse_args()

    th = dict(DEFAULT_THRESHOLDS)
    if args.gate is not None:
        th["overall_min_pass_rate"] = args.gate
    if args.min_recall is not None:
        th["min_recall"] = args.min_recall
    if args.judge is not None:
        th["judge_enabled"] = bool(args.judge)
    if args.judge_threshold is not None:
        th["judge_threshold"] = args.judge_threshold

    if args.stub:
        # CI 自检：验证案例可加载 + harness 可跑通，不据分数判定
        cases = load_cases(args.cases)
        if not cases:
            print("eval-gate selftest: FAIL（案例加载不到）")
            _sys.exit(2)

        def _stub_answer(q):
            return {"answer": q, "sources": [{"doc_id": "stub"}]}

        rep = run_gate(cases, _stub_answer, retrieve_fn=lambda q: ["stub"],
                       thresholds={**th, "overall_min_pass_rate": 0.0, "min_recall": 0.0})
        print(f"eval-gate selftest: harness OK（加载 {rep.n} 案例，接线正常）")
        _sys.exit(0)

    if args.api:
        # HTTP 模式：测一个正在运行的后端（AutoDL / 容器 / 打包 app 的本地后端），测试机无需模型
        import urllib.request as _ur
        import json as _hj
        base = args.api.rstrip("/")
        # 1) 探活
        try:
            with _ur.urlopen(base + "/api/health", timeout=15) as _r:
                _r.read()
            print(f"eval-gate: 后端 {base} 可达")
        except Exception as _e:
            print(f"eval-gate: 连不上后端 {base}（{type(_e).__name__}）。请确认后端在跑、URL/端口正确、网络/防火墙放行。")
            _sys.exit(2)
        # 2) 自动登录拿 token（可选）
        token = args.token
        if not token and args.user:
            try:
                _b = _hj.dumps({"username": args.user, "password": args.password or ""}).encode("utf-8")
                _rq = _ur.Request(base + "/api/auth/login", data=_b, headers={"Content-Type": "application/json"})
                with _ur.urlopen(_rq, timeout=20) as _r:
                    token = (_hj.loads(_r.read().decode("utf-8")) or {}).get("token")
                print("eval-gate: 登录成功，带 token 测" if token else "eval-gate: 登录未返回 token，改用未登录身份测")
            except Exception as _e:
                print(f"eval-gate: 登录失败（{type(_e).__name__}），改用未登录身份测（部分能力可能降级）。")
        # 3) 报知识库规模（空后端就提醒，避免又白测）
        try:
            _rq2 = _ur.Request(base + "/api/corpus/stats")
            if token:
                _rq2.add_header("Authorization", f"Bearer {token}")
            with _ur.urlopen(_rq2, timeout=15) as _r:
                _nchunks = (_hj.loads(_r.read().decode("utf-8")) or {}).get("total_chunks", None)
            if _nchunks is not None:
                print(f"eval-gate: 后端知识库 {_nchunks} 条切片")
                if not _nchunks:
                    print("eval-gate: 注意——后端知识库为空（0 切片），需要检索/来源的案例都会失败。"
                          "确认这个后端确实加载了你的索引，再跑才有意义。")
        except Exception:
            pass
        # V103.49: LLM 探针 —— 发一个简单问题，若答案是"LLM 未配置/未就绪"，说明
        # 后端根本没配模型（常见于 DB 损坏重建后没重配）。此时整轮答案都会是空/占位，
        # 跑出来的基线毫无意义。大声报错，并默认拒绝写基线（除非 --force-empty）。
        _probe_fn = (_build_stream_answer_fn(base, token, dump_dir=None) if args.stream
                     else _build_http_answer_fn(base, token))
        try:
            _probe = (_probe_fn("用一句话介绍你自己") or {}).get("answer", "")
        except Exception:
            _probe = ""
        _LLM_DOWN_MARKERS = ("LLM 未配置", "LLM 未就绪", "模型尚未加载", "在管理后台添加模型",
                             "在管理后台配置", "API Key", "Base URL 设置")
        if _probe and any(m in _probe for m in _LLM_DOWN_MARKERS):
            print("\n" + "=" * 64)
            print("eval-gate: ❌ 后端没有可用的 LLM！探针答案是占位文案：")
            print(f"           「{_probe[:60]}」")
            print("           这说明后端没配模型（常见于 DB 损坏重建后未重配）。")
            print("           整轮答案都会是空/占位，跑出来的分数没有意义。")
            print("           修复：到管理后台配模型，或设环境变量 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL 后重启。")
            print("           （V103.49 起模型配置有镜像，配一次后 DB 再损坏会自动恢复。）")
            print("=" * 64 + "\n")
            if args.update_baseline and not getattr(args, "force_empty", False):
                print("eval-gate: 已【拒绝】把这个空环境结果写成基线（避免假基线误导）。"
                      "确需强写请加 --force-empty。")
                _sys.exit(2)
        print(f"eval-gate: 开始把金标准问题逐条发给后端…（共 112 条，每条要等后端 LLM 生成，可能几分钟）")
        if args.stream:
            print("eval-gate: 走流式端点 /api/conversations/{id}/stream（用户真实路径）")
            if args.dump:
                print(f"eval-gate: --dump 已开启，每条 case 的原始事件流将落盘到 {args.dump}/")
            _hfn = _build_stream_answer_fn(base, token, dump_dir=args.dump)
        else:
            _hfn = _build_http_answer_fn(base, token)
        _jfn = _build_http_judge_fn(base, token) if th.get("judge_enabled", True) else None
        if _jfn is not None:
            print("eval-gate: LLM-judge 质量门已开启（对带 judge/rubric 的案例追加质量打分）")
        code = gate_main(_hfn, retrieve_fn=None, case_paths=args.cases,
                         thresholds=th, baseline_path=args.baseline,
                         update_baseline=args.update_baseline, judge_fn=_jfn)
        _sys.exit(code)

    answer_fn = _build_rag_answer_fn()
    retrieve_fn = _build_retrieve_fn()
    if answer_fn is None and retrieve_fn is None:
        print("eval-gate: 真实检索管线不可用（缺索引/模型）。请在配好知识库的真机上运行，"
              "或加 --stub 仅自检 harness。")
        _sys.exit(2)
    # 预检：空索引 / 没配模型时，分数没有意义——大声告警，并拒绝写基线（避免污染回归判定）
    _ok, _warns = _preflight()
    if _warns:
        print("=" * 64)
        print("注意：eval-gate 预检发现环境问题，本次分数没有参考意义：")
        for _w in _warns:
            print("  - " + _w)
        print("修复后再跑：①pip install transformers FlagEmbedding ②在管理后台配置可用模型 "
              "③确保检索索引指向你的知识库（非空）。")
        print("=" * 64)
        if args.update_baseline:
            print("已中止：拒绝在空/未配置环境下写基线（会让以后的回归判定全部失真）。"
                  "配好环境后再加 --update-baseline。")
            _sys.exit(2)
    _jfn = _build_local_judge_fn() if th.get("judge_enabled", True) else None
    code = gate_main(answer_fn, retrieve_fn=retrieve_fn, case_paths=args.cases,
                     thresholds=th, baseline_path=args.baseline,
                     update_baseline=args.update_baseline, judge_fn=_jfn)
    _sys.exit(code)
