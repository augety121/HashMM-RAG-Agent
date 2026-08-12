"""方案4（B5）— 不确定性闸：把多路信号合成一个"该多有信心"的统一判定。

与忠实度合约（evaluation/faithfulness.py）互补、正交：
  - 忠实度回答"答案接没接地（每句有没有证据支撑）"；
  - 不确定性回答"证据够不够强、该不该让用户存疑"。

组合三类信号（呼应 UncertaintyRAG arXiv 2410.02719 的思路）：
  1) 置信度——直接复用 agent/confidence.py（已综合 top_score/来源数/实体命中/接地率/对冲措辞）；
  2) 检索分离散度——top 分的强度 + top1/top2 的间隔（一个清晰的强匹配 ⇒ 证据扎实；
     一堆都弱或挤在低分 ⇒ 证据虚）；
  3) 答案自一致性——对同一问题多次采样的答案彼此是否一致（不一致 ⇒ 模型在猜）。

策略：低不确定 → 照常答；中 → 答但显式标注存疑；高 → 标"资料不足/存疑"并（可选）触发一次再检索。
全部纯函数、不抛异常、信号缺失就用现有的子集平均——与 confidence.py 同一套稳健约定。
低误报设计：只有明确不确定才标注，其余不打扰（和忠实度门一致）。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Optional

from hashmm.utils import get_logger
from hashmm.agent import confidence as _conf

logger = get_logger(__name__)


# ── 开关与阈值（env 可调；默认值偏保守，低误报）──

def uncertainty_enabled() -> bool:
    """是否在实时路径标注不确定性（默认 ON——纯词法、零额外延迟，只在明确不确定时标）。"""
    return os.environ.get("HASHMM_UNCERTAINTY", "1").strip().lower() in ("1", "true", "yes", "on")


def _f(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, default))
    except Exception:
        return default


def high_uncertainty_threshold() -> float:
    """≥ 此值 → 高不确定（标"资料不足/存疑"）。"""
    return _f("HASHMM_UNCERTAINTY_HIGH", 0.60)


def medium_uncertainty_threshold() -> float:
    """≥ 此值 → 中不确定（标"置信中等，请核对"）。"""
    return _f("HASHMM_UNCERTAINTY_MEDIUM", 0.40)


def _score_midpoint() -> float:
    # 与 confidence.py 对齐：相关度处于 web-fallback 阈值时映射到 ~0.5。
    return _f("HASHMM_CONFIDENCE_SCORE_MIDPOINT", _f("HASHMM_WEB_FALLBACK_THRESHOLD", 2.5))


def _logistic(x: float, midpoint: float, scale: float = 1.5) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-(x - midpoint) / max(1e-6, scale)))
    except OverflowError:
        return 0.0 if x < midpoint else 1.0


# ── 纯信号 1：检索分离散度 → "证据扎实度" [0,1] ──

def retrieval_solidity(scores) -> Optional[float]:
    """从一组检索/重排分判断证据有多扎实，返回 [0,1]（越高越扎实），无分→None。

    直觉：① 最高分越高越扎实（logistic 归一，midpoint 对齐 grounded 阈值）；
    ② top1 与 top2 的间隔越大说明"有一个明显胜出的强匹配"，越扎实；
    一堆都弱 / 挤在相近低分（无清晰胜出）→ 扎实度低。
    """
    vals = []
    for s in (scores or []):
        if s is None:
            continue
        try:
            vals.append(float(s))
        except (TypeError, ValueError):
            continue   # 跳过非数值脏值，用现有子集（与 confidence.py 同约定）
    if not vals:
        return None
    vals.sort(reverse=True)
    top = vals[0]
    top_norm = _logistic(top, _score_midpoint())
    if len(vals) >= 2:
        margin = top - vals[1]
        margin_norm = _logistic(margin, 0.0, scale=max(1e-6, _score_midpoint() * 0.5))
    else:
        margin_norm = 0.6   # 只有一条来源：不奖不罚，中性偏上
    return round(min(1.0, max(0.0, 0.65 * top_norm + 0.35 * margin_norm)), 4)


# ── 纯信号 2：答案自一致性 [0,1] ──

def _tokenize(text: str):
    """复用忠实度模块的分词（CJK 2-gram + 英文词），避免两套实现漂移。"""
    try:
        from hashmm.evaluation.faithfulness import tokenize as _ft
        return set(_ft(text))
    except Exception:
        # 退化：极简分词，保证本模块即便忠实度模块缺失也能跑
        import re
        toks = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
        cjk = re.findall(r"[\u4e00-\u9fff]", text or "")
        toks |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
        return toks


def self_consistency(samples) -> Optional[float]:
    """对同一问题的多份采样答案，求两两词法一致度的平均，返回 [0,1]，<2 份→None。

    高一致 ⇒ 模型稳定地给同一答案（更可信）；低一致 ⇒ 在猜（应存疑）。
    用 Jaccard 词法重叠近似"是否在说同一件事"；纯函数、可单测。
    """
    texts = [str(s or "").strip() for s in (samples or []) if str(s or "").strip()]
    if len(texts) < 2:
        return None
    toks = [_tokenize(t) for t in texts]
    sims = []
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            a, b = toks[i], toks[j]
            if not a and not b:
                sims.append(1.0); continue
            inter = len(a & b)
            union = len(a | b) or 1
            sims.append(inter / union)
    if not sims:
        return None
    return round(sum(sims) / len(sims), 4)


# ── 合成判定 ──

@dataclass
class UncertaintyReport:
    uncertainty: float = 0.5            # [0,1]，越高越不确定
    level: str = "low"                 # low / medium / high
    decision: str = "answer"           # answer / hedge / insufficient
    confidence: Optional[float] = None
    solidity: Optional[float] = None
    consistency: Optional[float] = None
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "uncertainty": self.uncertainty, "level": self.level, "decision": self.decision,
            "confidence": self.confidence, "solidity": self.solidity,
            "consistency": self.consistency, "reasons": list(self.reasons),
        }


# 三信号权重（接地/置信最重，扎实度次之，自一致性作校准）。
_WEIGHTS = {"confidence": 1.0, "solidity": 0.7, "consistency": 0.6}


def assess_uncertainty(query: str, sources: list, answer: str = "", *,
                       grounding_ratio: Optional[float] = None,
                       samples=None, miss_fn=None,
                       high: Optional[float] = None,
                       medium: Optional[float] = None) -> UncertaintyReport:
    """综合 置信度 + 检索扎实度 + 自一致性 → 统一不确定度与处置决策。永不抛异常。

    - grounding_ratio：忠实度接地率（有则纳入 confidence，强信号）。
    - samples：对同一问题的多份采样答案（给了才算自一致性；不给则该信号缺省）。
    - miss_fn(query, sources)->bool：来源是否未提及问题实体（注入，复用现有检查）。
    """
    try:
        sources = sources or []
        conf = _conf.assess_retrieval(
            query, sources, miss_fn=miss_fn, grounding_ratio=grounding_ratio,
            answer_hedged=_conf.detect_hedging(answer),
        ).get("confidence")
        scores = []
        for s in sources:
            try:
                v = s.get("score") if isinstance(s, dict) else None
                if v is not None:
                    scores.append(float(v))
            except Exception:
                pass
        solidity = retrieval_solidity(scores)
        consistency = self_consistency(samples)

        # 把"信心/扎实/一致"加权平均成 certainty，再取 1-certainty 为不确定度。
        parts = []
        if conf is not None:
            parts.append((float(conf), _WEIGHTS["confidence"]))
        if solidity is not None:
            parts.append((float(solidity), _WEIGHTS["solidity"]))
        if consistency is not None:
            parts.append((float(consistency), _WEIGHTS["consistency"]))
        if parts:
            wsum = sum(w for _, w in parts)
            certainty = sum(v * w for v, w in parts) / wsum if wsum else 0.5
        else:
            certainty = 0.5
        uncertainty = round(1.0 - certainty, 4)

        hi = high if high is not None else high_uncertainty_threshold()
        med = medium if medium is not None else medium_uncertainty_threshold()
        if uncertainty >= hi:
            level, decision = "high", "insufficient"
        elif uncertainty >= med:
            level, decision = "medium", "hedge"
        else:
            level, decision = "low", "answer"

        reasons = []
        if conf is not None and conf < 0.4:
            reasons.append("综合置信度偏低")
        if solidity is not None and solidity < 0.4:
            reasons.append("检索证据不够扎实（最佳来源偏弱或无明显胜出）")
        if consistency is not None and consistency < 0.5:
            reasons.append("多次作答彼此不一致")
        if not sources:
            reasons.append("没有检索到支撑来源")

        return UncertaintyReport(
            uncertainty=uncertainty, level=level, decision=decision,
            confidence=conf, solidity=solidity, consistency=consistency, reasons=reasons,
        )
    except Exception as e:
        logger.debug(f"assess_uncertainty failed: {e}")
        return UncertaintyReport()


# 标注文案（咨询式，附在终答后；与 confidence.HEDGE_NOTE 风格一致）。
NOTE_INSUFFICIENT = "资料不足/存疑：以下回答缺乏足够的可靠资料支撑，请把它当作初步参考并务必核实关键信息。"
NOTE_HEDGE = "（注：本回答置信度中等，部分内容资料有限，请核对关键信息。）"


def should_mark(report: UncertaintyReport) -> bool:
    """是否需要在终答标注（低误报：仅 medium/high 才标）。"""
    return report.decision in ("hedge", "insufficient")


def build_uncertainty_note(report: UncertaintyReport) -> str:
    """根据决策给出要附加到终答的标注文本（low → 空串）。"""
    if report.decision == "insufficient":
        base = NOTE_INSUFFICIENT
    elif report.decision == "hedge":
        base = NOTE_HEDGE
    else:
        return ""
    if report.reasons:
        base += "（原因：" + "；".join(report.reasons[:3]) + "）"
    return base


def summarize_for_ui(report: UncertaintyReport) -> dict:
    """给前端的精简载荷（可据此显示不确定度徽标/存疑条）。"""
    return {
        "uncertainty": report.uncertainty,
        "level": report.level,
        "flagged": should_mark(report),
        "note": build_uncertainty_note(report),
        "reasons": list(report.reasons),
    }
