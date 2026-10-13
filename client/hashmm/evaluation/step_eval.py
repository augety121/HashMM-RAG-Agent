"""方案6（B4）— 步级评测：把"只看最终结果"升级为"每步单独打分 + 归因瓶颈"。

RAGAS 类只看最终答案，多步里哪一步坏看不出（评测专文 2026-04 指出其漏多类失败）。
本模块从 agent 事件流（tool_start/tool_done/trace/token）切出 5 个阶段并各自打分：

  路由 routing      —— 工具选择是否得当（该检索时检索了、没在空转/反复报错）
  检索 retrieval    —— 检索调用是否真拿回了非空结果
  重排 rerank       —— 检索质量（需要几次改写/有无无进展打转——越少越好）
  合成 synthesis    —— 是否产出了实质答案（非空、非兜底道歉）
  验证 verification —— 收尾质量门（verify/citation/faithfulness/DoD）是否通过

每步 → [0,1] 分 + 是否适用 + 归因原因；再给总分与"瓶颈步"（最低分的适用步）。
纯函数、不抛异常、用 mock 事件流即可单测。可选接 llm_judge 做 RAGAS 式答案质量维度
（faithfulness/answer-relevance/context-precision），默认仅结构化打分（无需 LLM，沙箱可跑）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# 各类工具归类（按事件里的 name 匹配）
_RETRIEVAL_TOOLS = ("kb_search", "deep_search", "web_search", "deep_research")
_BAD_RESULT_MARKERS = ("未找到", "没有找到", "no results", "无相关", "0 条结果", "检索失败")
_FALLBACK_ANSWER_MARKERS = ("抱歉，我在处理这个任务", "没能获取到足够的信息", "无法回答")

STEP_STAGES = ("routing", "retrieval", "rerank", "synthesis", "verification")


@dataclass
class StageScore:
    score: float = 1.0
    applicable: bool = True
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"score": round(self.score, 4), "applicable": self.applicable,
                "reasons": list(self.reasons)}


@dataclass
class StepReport:
    stages: dict = field(default_factory=dict)   # name -> StageScore
    overall: float = 1.0
    bottleneck: Optional[str] = None             # 最低分的适用步
    judge: Optional[dict] = None                 # 可选 RAGAS 式答案质量

    def to_dict(self) -> dict:
        return {
            "stages": {k: v.to_dict() for k, v in self.stages.items()},
            "overall": round(self.overall, 4),
            "bottleneck": self.bottleneck,
            "judge": self.judge,
        }


# ── 事件流切片辅助 ──

def _iter(events):
    """容忍 (type, data) 二元组事件；非法项跳过。"""
    for ev in (events or []):
        try:
            et, ed = ev
        except Exception:
            continue
        yield et, (ed if isinstance(ed, dict) else {"_": ed})


def _tool_dones(events, names=None):
    out = []
    for et, ed in _iter(events):
        if et == "tool_done" and (names is None or ed.get("name") in names):
            out.append(ed)
    return out


def _trace_nodes(events, node):
    return [ed for et, ed in _iter(events) if et == "trace" and ed.get("node") == node]


def _answer_text(events):
    return "".join(str(ed.get("_", "")) for et, ed in _iter(events) if et == "token")


# ── 各阶段打分（纯函数）──

def score_retrieval(events) -> StageScore:
    """检索：检索类工具调用是否真拿回了非空、非"未找到"的结果。"""
    calls = _tool_dones(events, _RETRIEVAL_TOOLS)
    if not calls:
        return StageScore(score=1.0, applicable=False, reasons=["本回合未发生检索"])
    good = 0
    for c in calls:
        status = c.get("status", "ok")
        res = str(c.get("result", "") or "")
        bad = (status == "error") or (not res.strip()) or any(
            m in res.lower() or m in res for m in _BAD_RESULT_MARKERS)
        if not bad:
            good += 1
    score = good / len(calls)
    reasons = []
    if score < 1.0:
        reasons.append(f"{len(calls)-good}/{len(calls)} 次检索为空/失败/未命中")
    return StageScore(score=round(score, 4), applicable=True, reasons=reasons)


def score_rerank(events) -> StageScore:
    """重排/检索质量：需要的改写次数 + 无进展打转次数越多，质量越低。

    用 retrieval_adapt（低质检索触发的改写指引）与 loop(无进展) trace 作负信号。
    """
    calls = _tool_dones(events, _RETRIEVAL_TOOLS)
    if not calls:
        return StageScore(score=1.0, applicable=False, reasons=["本回合无检索，无需重排"])
    rewrites = len(_trace_nodes(events, "retrieval_adapt"))
    # loop 节点里含"无新增证据/停止打转"的算无进展
    noprog = sum(1 for ed in _trace_nodes(events, "loop")
                 if "无新增证据" in str(ed.get("detail", "")) or "打转" in str(ed.get("detail", "")))
    penalty = min(1.0, 0.34 * rewrites + 0.5 * noprog)
    score = max(0.0, 1.0 - penalty)
    reasons = []
    if rewrites:
        reasons.append(f"检索改写 {rewrites} 次")
    if noprog:
        reasons.append(f"无进展打转 {noprog} 次")
    return StageScore(score=round(score, 4), applicable=True, reasons=reasons)


def score_synthesis(events, answer: str = "") -> StageScore:
    """合成：是否产出了实质答案（非空、长度足够、不是兜底道歉）。"""
    ans = (answer or _answer_text(events) or "").strip()
    if not ans:
        return StageScore(score=0.0, applicable=True, reasons=["未产出任何答案正文"])
    if any(m in ans for m in _FALLBACK_ANSWER_MARKERS):
        return StageScore(score=0.3, applicable=True, reasons=["回答是兜底道歉文案（未能有效作答）"])
    if len(ans) < 12:
        return StageScore(score=0.7, applicable=True, reasons=["答案过短，可能信息不足"])
    return StageScore(score=1.0, applicable=True, reasons=[])


def score_verification(events) -> StageScore:
    """验证：收尾质量门（verify/citation/faithfulness/DoD）有没有发现并暴露问题。

    门"通过 ✓"加分；门"待核/无效/未完成"扣分。门压根没跑视为不适用（不奖不罚）。
    """
    nodes = ("verify", "citation", "faithfulness", "dod")
    traces = []
    for n in nodes:
        traces += [(n, ed) for ed in _trace_nodes(events, n)]
    if not traces:
        return StageScore(score=1.0, applicable=False, reasons=["未触发收尾质量门"])
    issues = 0
    for _n, ed in traces:
        d = str(ed.get("detail", ""))
        if ("待核" in d or "无效" in d or "未完成" in d or "未通过" in d
                or "不一致" in d or "存疑" in d or "缺" in d):
            issues += 1
    score = max(0.0, 1.0 - issues / max(1, len(traces)))
    reasons = [f"{issues}/{len(traces)} 个质量门发现问题"] if issues else []
    return StageScore(score=round(score, 4), applicable=True, reasons=reasons)


def score_routing(events) -> StageScore:
    """路由：工具选择是否得当——空转（反复报错/同名工具刷屏）扣分；有产出且无明显空转给高分。"""
    dones = _tool_dones(events)
    errors = sum(1 for d in dones if d.get("status") == "error")
    # 同名工具被调用次数（粗略探测"反复同样操作"）
    from collections import Counter
    name_counts = Counter(d.get("name") for d in dones if d.get("name"))
    repeats = sum(c - 3 for c in name_counts.values() if c > 3)   # 单工具超 3 次的超额部分
    penalty = min(1.0, 0.2 * errors + 0.15 * repeats)
    score = max(0.0, 1.0 - penalty)
    reasons = []
    if errors:
        reasons.append(f"工具失败 {errors} 次")
    if repeats:
        reasons.append("同类工具反复调用（疑似空转）")
    return StageScore(score=round(score, 4), applicable=True, reasons=reasons)


# ── 合成报告 ──

def score_steps(events, *, query: str = "", answer: str = "",
                judge_fn: Optional[Callable[[str, str], Optional[dict]]] = None) -> StepReport:
    """对一回合事件流做步级打分，返回 StepReport（含瓶颈步）。永不抛异常。

    judge_fn(query, answer) -> {score, dimensions...} 可选；给了则附 RAGAS 式答案质量维度
    （需 LLM，真机用）。默认仅结构化打分，沙箱可全测。
    """
    try:
        ans = answer or _answer_text(events)
        stages = {
            "routing": score_routing(events),
            "retrieval": score_retrieval(events),
            "rerank": score_rerank(events),
            "synthesis": score_synthesis(events, ans),
            "verification": score_verification(events),
        }
        applicable = [(k, s) for k, s in stages.items() if s.applicable]
        if applicable:
            overall = sum(s.score for _, s in applicable) / len(applicable)
            bottleneck = min(applicable, key=lambda kv: kv[1].score)[0]
            # 若瓶颈步其实满分（无短板），则不报瓶颈
            if stages[bottleneck].score >= 0.999:
                bottleneck = None
        else:
            overall, bottleneck = 1.0, None

        judge = None
        if judge_fn is not None and ans:
            try:
                judge = judge_fn(query, ans)
            except Exception:
                judge = None

        return StepReport(stages=stages, overall=round(overall, 4),
                          bottleneck=bottleneck, judge=judge)
    except Exception:
        return StepReport()


def aggregate_step_reports(reports) -> dict:
    """把多回合的步级结果聚合：各阶段均分 + 瓶颈步频次（定位系统级瓶颈在哪步）。

    入参每项可为 StepReport 对象或其 to_dict() 字典（agent_bench 里流转的是字典）。
    """
    from collections import Counter

    def _norm(r):
        """归一成 {stages:{name:{score,applicable}}, bottleneck}。"""
        if isinstance(r, StepReport):
            return ({k: {"score": v.score, "applicable": v.applicable}
                     for k, v in r.stages.items()}, r.bottleneck)
        if isinstance(r, dict):
            return (r.get("stages", {}) or {}, r.get("bottleneck"))
        return ({}, None)

    sums = {s: 0.0 for s in STEP_STAGES}
    cnts = {s: 0 for s in STEP_STAGES}
    bottlenecks = Counter()
    n = 0
    for r in (reports or []):
        stages, bottleneck = _norm(r)
        n += 1
        for name, sc in stages.items():
            if name in sums and sc.get("applicable"):
                sums[name] += float(sc.get("score", 0.0))
                cnts[name] += 1
        if bottleneck:
            bottlenecks[bottleneck] += 1
    stage_avg = {s: (round(sums[s] / cnts[s], 4) if cnts[s] else None) for s in STEP_STAGES}
    return {
        "n": n,
        "stage_avg": stage_avg,
        "bottleneck_counts": dict(bottlenecks),
        "top_bottleneck": (bottlenecks.most_common(1)[0][0] if bottlenecks else None),
    }
