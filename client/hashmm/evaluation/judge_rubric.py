"""hashmm/evaluation/judge_rubric.py — LLM-as-Judge 量规裁判（V273）。

严格按面试资料 3.3 的"judge prompt 四要素"落地：
  角色设定 + 评分维度 + **1–5 分逐档定义（每档配示例锚点）** + 指定输出格式(JSON)。
资料原话："量规要逐档下定义——不是光说 1–5 分，而是把 1 分长什么样、5 分长什么样
逐档写清（create scoring rubrics with examples）"。
另按资料提醒：不同工具同名指标标度不可比（MLflow 1-5 / Ragas 0-1 / LlamaIndex YES-NO），
所以**本项目内统一 1-5 标度**，分数只在本项目内部纵向比较。

两套内置量规：
  answer_quality —— 评"一次回答"（正确性/依据扎根/完整性/清晰度）
  agent_run     —— 评"一次 Agent 运行"（任务完成/工具使用/效率/自我纠错）
永不抛错；无 LLM 时返回 skip 语义（ok=False + detail 说明），绝不编造分数。
"""
from __future__ import annotations

import json
import re
from typing import Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evaluation.judge_rubric")

RUBRICS: dict[str, dict] = {
    "answer_quality": {
        "role": "你是严格的答案质量裁判，只依据给定材料评分，不脑补事实。",
        "dims": {
            "correctness": {
                "label": "正确性",
                "anchors": {
                    "1": "核心结论错误或与材料冲突。例：材料说增长 12%，答案写下降。",
                    "2": "主要结论对但含明显事实错误（错数字/错主体各一处以上）。",
                    "3": "结论正确，细节有一处小瑕疵（如年份写错但不影响结论）。",
                    "4": "结论与细节全部正确，个别表述不够精确。",
                    "5": "完全正确且精确：数字、主体、时间、因果关系全部与材料一致。",
                },
            },
            "grounding": {
                "label": "依据扎根",
                "anchors": {
                    "1": "关键主张在材料中完全找不到出处（凭空生成）。",
                    "2": "少数主张有出处，多数无法在材料中定位。",
                    "3": "主要主张可在材料定位，但未注明来源/引用混乱。",
                    "4": "主张基本都能定位到材料，引用大体清晰。",
                    "5": "每个事实性主张都可精确定位到材料片段，引用规范。",
                },
            },
            "completeness": {
                "label": "完整性",
                "anchors": {
                    "1": "只回应了问题的一小部分，多数诉求被忽略。",
                    "2": "回应了主要诉求，遗漏 2 个以上明确子问题。",
                    "3": "覆盖全部明确诉求，深度浅（每条一两句带过）。",
                    "4": "覆盖全部诉求且多数有展开，个别点略浅。",
                    "5": "全部诉求逐条充分回答，含必要的边界条件与例外说明。",
                },
            },
            "clarity": {
                "label": "清晰度",
                "anchors": {
                    "1": "结构混乱，读者无法据此行动。",
                    "2": "能看懂但组织差，重点淹没在冗文里。",
                    "3": "结构合理，个别段落啰嗦或跳跃。",
                    "4": "结构清晰、详略得当，几乎无冗余。",
                    "5": "结构堪称范文：先结论后展开，术语首现即释义，可直接执行。",
                },
            },
        },
    },
    "agent_run": {
        "role": "你是 Agent 运行裁判，依据任务目标与运行轨迹评分，关注做没做成、做得省不省。",
        "dims": {
            "task_completion": {
                "label": "任务完成",
                "anchors": {
                    "1": "任务失败且无可用产出。",
                    "2": "产出与目标偏差大，需人重做大半。",
                    "3": "达成主要目标，次要要求有缺口。",
                    "4": "目标全部达成，产出可直接使用，小修即可。",
                    "5": "全部达成且超出预期（含验证/边界处理），零返工。",
                },
            },
            "tool_use": {
                "label": "工具使用",
                "anchors": {
                    "1": "选错工具或参数错误导致失败，且未纠正。",
                    "2": "多次误调/漏必填参数，靠运气完成。",
                    "3": "工具选择正确，个别参数不优（如范围过大）。",
                    "4": "工具与参数全部正确，顺序合理。",
                    "5": "工具/参数/顺序全对，且对'不该调工具'的部分保持克制（无工具幻觉）。",
                },
            },
            "efficiency": {
                "label": "效率",
                "anchors": {
                    "1": "严重绕路：重复调用/无效循环占多数步骤。",
                    "2": "明显冗余步骤 ≥3 处。",
                    "3": "路径基本合理，有 1-2 处可省的步骤。",
                    "4": "路径干净，几乎无冗余。",
                    "5": "最短可行路径完成，还主动复用了已有结果。",
                },
            },
            "self_correction": {
                "label": "自我纠错",
                "anchors": {
                    "1": "出错后原样重试或放弃，无任何调整。",
                    "2": "察觉出错但纠正方向错误。",
                    "3": "能利用错误信息调整一次并通过。",
                    "4": "错误后快速定位原因并精准修正。",
                    "5": "预判风险提前规避，或出错后一次修正并补充验证。",
                },
            },
        },
    },
}


def _prompt(rubric_id: str, question: str, answer: str, contexts: list[str]) -> str:
    rb = RUBRICS[rubric_id]
    dims_txt = []
    for did, d in rb["dims"].items():
        lines = "\n".join(f"    {k} 分：{v}" for k, v in d["anchors"].items())
        dims_txt.append(f"- {did}（{d['label']}）逐档定义：\n{lines}")
    ctx = ("\n\n【可用材料】\n" + "\n---\n".join(c[:800] for c in contexts[:4])) if contexts else ""
    return (
        f"{rb['role']}\n\n"
        "按下列维度打 1-5 整数分，必须逐档对照定义，不许给未定义的分值：\n"
        + "\n".join(dims_txt)
        + f"\n\n【任务/问题】\n{str(question)[:1500]}{ctx}\n\n【被评对象】\n{str(answer)[:3000]}\n\n"
        "只输出 JSON（不要解释、不要 markdown 围栏）："
        '{"scores": {"<维度id>": <1-5>}, "reasons": {"<维度id>": "一句话理由，须引用被评对象原文片段"}}'
    )


def judge_with_rubric(question: str, answer: str, contexts: list[str] | None = None, *,
                      rubric: str = "answer_quality",
                      llm_fn: Callable[[str], str] | None = None) -> dict:
    """返回 {ok, scores:{dim:int}, avg, reasons, detail}；无 LLM → ok=False（skip 语义）。"""
    if rubric not in RUBRICS:
        return {"ok": False, "detail": f"未知量规 {rubric}", "scores": {}}
    if not callable(llm_fn):
        return {"ok": False, "detail": "未配置 LLM，量规裁判跳过（不编造分数）", "scores": {}}
    try:
        raw = str(llm_fn(_prompt(rubric, question, answer, contexts or [])) or "")
        m = re.search(r"\{[\s\S]*\}", raw)
        data = json.loads(m.group(0)) if m else {}
        dims = RUBRICS[rubric]["dims"]
        scores = {}
        for did in dims:
            v = data.get("scores", {}).get(did)
            if isinstance(v, (int, float)) and 1 <= v <= 5:
                scores[did] = int(v)
        if not scores:
            return {"ok": False, "detail": "裁判输出不可解析", "scores": {}, "raw": raw[:300]}
        avg = round(sum(scores.values()) / len(scores), 2)
        return {"ok": True, "scores": scores, "avg": avg,
                "reasons": data.get("reasons", {}),
                "detail": f"{RUBRICS[rubric]['role'][:6]}… 平均 {avg} 分（{len(scores)}/{len(dims)} 维）"}
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return {"ok": False, "detail": f"裁判执行失败：{e}", "scores": {}}
