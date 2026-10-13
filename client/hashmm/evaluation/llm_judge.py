"""hashmm/evaluation/llm_judge.py — V103.51 「质量门」核心：LLM-as-judge 答案质量打分。

为什么要这一层（对标 Claude Code / OpenAI evals / Anthropic 的 model-graded evals）
-----------------------------------------------------------------------------
到 V103.50，门（``gate.py``）只做契约检查：``must_contain_any`` 子串命中、``min_sources``
来源数、``min_length`` 长度。这能测「答对没 / 该不该拒答」，但测不了「答得好不好」——
一个塞满关键词却语无伦次的答案能过，一个措辞稍异但更优的诚实拒答会被误判（正是
V103.50 的 antihalluc_03：模型说「未提供」被 9 词表漏判）。大厂的标准做法是
**model-graded eval**：用一个强模型按评分量表（rubric）给答案的有用性 / 完整性 /
事实落地 / 条理打分，而不是字符串匹配。

本模块只做「产生分数」这一件事，且与项目已有的两层基础设施严丝合缝地对接：
  * 上游：``judge_calibration.py`` 已经造好了「用人工标注校准 judge」的全套（Cohen's
    kappa、TPR/TNR、过度讨好检测、长度偏置、阈值自动选择）。它一直缺的是**真正会
    打分的 judge**——本模块补上。judge 产出的 ``judge_score`` 正是它期待的输入字段。
  * 下游：``gate.py`` 的 ``check_contract`` 是硬性契约（拒答必须含拒答词、来源够数）。
    judge 不替代它——契约是「红线」，judge 是「红线之上还要答得好」。二者是 AND 关系：
    契约不过直接挂；契约过了，再按 judge 分数卡 ``judge_threshold``。

设计原则（与 llm_gateway / refusal_guard 一致）：
  1. **纯逻辑可单测**：prompt 构造（``build_judge_prompt``）、打分解析（``parse_judge_response``）、
     加权汇总（``aggregate_scores``）全是纯函数，不需要真模型即可测。
  2. **LLM 可注入**：``score_answer`` 接受 ``llm_fn`` 参数；不传时走 ``app_state.llm_fn``
     （与 chat_retrieval._get_llm 同一条获取路径）。离线 / 测试传假函数。
  3. **降级安全**：没有可用 LLM 或解析失败时，返回 ``available=False`` 的中性结果，
     **绝不**因为 judge 不可用就把答案判失败（那会让没配模型的环境全红）。是否把
     judge 计入门由调用方（gate）显式决定。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evaluation.llm_judge")


# ── 评分量表（rubric）──────────────────────────────────────────────────────
# 四个维度，对标 Claude Code「像个聪明助手」的可考核维度。每维 1..5 分。
# helpfulness   有用性：是否真正回答了问题、对用户有用。
# completeness  完整性：是否覆盖了问题的所有部分，无明显遗漏。
# grounding     事实落地：claims 是否有来源支撑 / 是否符合提供的上下文（拒答类此维考核
#               「是否恰当地承认不知道而非编造」）。
# coherence     条理：结构是否清晰、表达是否通顺、无自相矛盾。
RUBRIC_DIMENSIONS: tuple[str, ...] = ("helpfulness", "completeness", "grounding", "coherence")

# 各维权重（和为 1）。grounding 权重最高——这是 RAG 产品的命门，也是防幻觉的核心。
DEFAULT_WEIGHTS: dict[str, float] = {
    "helpfulness": 0.30,
    "completeness": 0.20,
    "grounding": 0.35,
    "coherence": 0.15,
}

SCORE_MIN, SCORE_MAX = 1, 5


@dataclass
class JudgeResult:
    """一次 judge 打分的结果。``score`` 归一化到 0..1（供 judge_calibration 直接消费）。"""
    available: bool                         # judge 是否真的跑了（LLM 可用且解析成功）
    score: float                            # 0..1 加权总分；available=False 时为中性 0.0
    dimensions: dict[str, int] = field(default_factory=dict)   # 每维 1..5 原始分
    rationale: str = ""                     # judge 的简短理由（便于人工复核 / 校准）
    raw: str = ""                           # judge 原始输出（排障用）

    def to_dict(self) -> dict:
        return {
            "judge_available": self.available,
            "judge_score": round(self.score, 4),
            "judge_dimensions": dict(self.dimensions),
            "judge_rationale": self.rationale,
        }


# ── 纯函数：prompt 构造 ────────────────────────────────────────────────────
def build_judge_prompt(query: str, answer: str, sources: Optional[list] = None,
                       expectation: str = "") -> str:
    """构造给 judge 模型的评分 prompt。

    ``expectation`` 是金标准案例可选的「期望要点」（case 里的 ``rubric`` / ``expect``
    字段）——给 judge 一个参照系，否则它只能凭常识判断。``sources`` 用于 grounding 维度：
    judge 据此判断 answer 的 claims 是否真有出处。
    """
    src_block = ""
    if sources:
        lines = []
        for i, s in enumerate(sources[:8], 1):
            if isinstance(s, dict):
                fn = s.get("filename", "") or s.get("source", "")
                txt = str(s.get("text", "") or s.get("content", ""))[:300]
                lines.append(f"[{i}] {fn}: {txt}")
            else:
                lines.append(f"[{i}] {str(s)[:300]}")
        src_block = "提供给被评答案的检索来源：\n" + "\n".join(lines) + "\n\n"

    expect_block = f"该问题的期望要点（参考，非逐字）：\n{expectation}\n\n" if expectation else ""

    return (
        "你是严格、公正的答案质量评审。请按四个维度为「被评答案」打分，每维 1 到 5 分整数：\n"
        "- helpfulness（有用性）：是否真正回答了用户问题、对用户有实际帮助。\n"
        "- completeness（完整性）：是否覆盖问题的各个部分，有无明显遗漏。\n"
        "- grounding（事实落地）：答案中的事实/数字是否有上述来源支撑；若来源不含答案，"
        "诚实说明「未提供/无法回答」应给高分，凭空编造应给低分。\n"
        "- coherence（条理）：结构是否清晰、表达是否通顺、有无自相矛盾。\n\n"
        "评分准则：5=优秀，4=良好，3=合格，2=较差，1=很差。对编造事实(幻觉)的答案，"
        "grounding 必须给 1。对恰当的诚实拒答(承认信息不存在)，grounding 应给 4 或 5。\n\n"
        f"{expect_block}{src_block}"
        f"用户问题：\n{query}\n\n"
        f"被评答案：\n{answer}\n\n"
        "只输出一个 JSON 对象，不要解释、不要 markdown 代码块，格式严格如下：\n"
        '{\"helpfulness\":<1-5>,\"completeness\":<1-5>,\"grounding\":<1-5>,'
        '\"coherence\":<1-5>,\"rationale\":\"<一句话理由>\"}'
    )


# ── 纯函数：响应解析 ────────────────────────────────────────────────────────
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)


def parse_judge_response(text: str) -> Optional[dict]:
    """从 judge 模型输出里抽出四维分数 + 理由。容错：剥 ```json 围栏、抓第一个 JSON 对象、
    分数夹到 1..5。完全无法解析 → None（调用方据此降级为不可用，而非判失败）。"""
    if not text:
        return None
    s = str(text).strip()
    # 剥 markdown 代码围栏
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S).strip()
    m = _JSON_OBJ_RE.search(s)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    dims: dict[str, int] = {}
    for d in RUBRIC_DIMENSIONS:
        v = obj.get(d)
        try:
            iv = int(round(float(v)))
        except (TypeError, ValueError):
            return None  # 缺维度 → 视为解析失败，降级
        dims[d] = max(SCORE_MIN, min(SCORE_MAX, iv))
    rationale = str(obj.get("rationale", "") or "")[:400]
    return {"dimensions": dims, "rationale": rationale}


# ── 纯函数：加权汇总 → 0..1 ───────────────────────────────────────────────
def aggregate_scores(dimensions: dict[str, int],
                     weights: Optional[dict[str, float]] = None) -> float:
    """把四维 1..5 分按权重汇总并归一化到 0..1。

    归一化：每维 (raw-1)/(5-1) → 0..1，再按权重求和。这样 5 分=1.0、1 分=0.0、3 分=0.5，
    与 judge_calibration 的 0..1 阈值语义一致。
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_w = sum(w.get(d, 0.0) for d in RUBRIC_DIMENSIONS) or 1.0
    acc = 0.0
    for d in RUBRIC_DIMENSIONS:
        raw = dimensions.get(d)
        if raw is None:
            continue
        norm = (raw - SCORE_MIN) / (SCORE_MAX - SCORE_MIN)   # 0..1
        acc += w.get(d, 0.0) * norm
    return acc / total_w


# ── 组合：调用 LLM 打分（LLM 可注入；不可用则安全降级）────────────────────
def _resolve_llm(llm_fn: Optional[Callable]):
    if llm_fn is not None:
        return llm_fn
    try:
        from hashmm.api import app_state
        return getattr(app_state, "llm_fn", None)
    except Exception as e:
        log_suppressed(logger, e)
        return None


def score_answer(query: str, answer: str, sources: Optional[list] = None,
                 expectation: str = "", *, llm_fn: Optional[Callable] = None,
                 weights: Optional[dict[str, float]] = None) -> JudgeResult:
    """对单条答案做 LLM 质量打分。

    返回 ``JudgeResult``。当无可用 LLM、答案为空、或 judge 输出无法解析时，返回
    ``available=False`` 的中性结果——**不会**因 judge 故障而把答案判失败。
    """
    if not (answer or "").strip():
        # 空答案不必请模型——直接 0 分但标记可用（空答案本就是质量问题）。
        return JudgeResult(available=True, score=0.0,
                           dimensions={d: SCORE_MIN for d in RUBRIC_DIMENSIONS},
                           rationale="empty answer")

    fn = _resolve_llm(llm_fn)
    if fn is None:
        return JudgeResult(available=False, score=0.0, rationale="no LLM available")

    prompt = build_judge_prompt(query, answer, sources, expectation)
    try:
        if hasattr(fn, "quick_call"):
            raw = fn.quick_call("你是严格公正的答案质量评审", prompt, max_tokens=200)
        else:
            raw = fn(prompt)
    except Exception as e:
        log_suppressed(logger, e)
        return JudgeResult(available=False, score=0.0, rationale=f"judge call failed: {e}")

    parsed = parse_judge_response(raw or "")
    if parsed is None:
        return JudgeResult(available=False, score=0.0, raw=str(raw or "")[:400],
                           rationale="unparseable judge output")

    dims = parsed["dimensions"]
    score = aggregate_scores(dims, weights)
    return JudgeResult(available=True, score=score, dimensions=dims,
                       rationale=parsed["rationale"], raw=str(raw or "")[:400])
