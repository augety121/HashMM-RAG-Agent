"""Evidence-first task method shared by Chat and AgentLoop.

This module deliberately contains process rules rather than domain knowledge.
They were distilled from repeated successful task-completion patterns in the
user-provided Fable5 transcripts and then constrained by HashMM's runtime
evidence policy: inspect real state, plan bounded work, treat failures as
evidence, verify with deterministic checks, and hand off limits explicitly.

The task contract never guesses hidden requirements.  Its goal is the user's
message, clipped only for storage/UI safety, and every criterion maps to a
runtime check that can be reported as passed, failed, or not evaluable.
"""
from __future__ import annotations

import re
from typing import Any


CONTRACT_SCHEMA = "hashmm.task-contract.v1"

_COMPLEX_SIGNALS = (
    "项目", "完整", "全面", "深入", "逐项", "继续完善", "排查", "修复",
    "实现", "改造", "重构", "调研", "对比", "报告", "方案", "规划", "多步",
    "测试", "验证", "部署", "打包", "生成文件", "长任务",
)


def clean_goal(value: str, limit: int = 600) -> str:
    """Keep the user-owned goal readable without inventing a summary."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def is_complex_task(user_goal: str, task_type: str = "") -> bool:
    """Conservative signal for work that benefits from a visible plan."""
    goal = str(user_goal or "")
    if str(task_type or "") in {
        "code_task", "file_task", "report_task", "research_task",
        "analysis_task", "modify_task", "doc_task",
    }:
        return True
    return len(goal) >= 120 or sum(1 for signal in _COMPLEX_SIGNALS if signal in goal) >= 2


def build_task_contract(
    *,
    user_goal: str,
    task_type: str,
    execution_mode: str,
    artifact_required: bool = False,
    evidence_expected: bool = False,
    requires_plan: bool | None = None,
    plan_items: list[dict] | None = None,
    run_id: str = "",
    conversation_id: str = "",
    acceptance: str = "",
    execution_scope_id: str = "",
) -> dict[str, Any]:
    """Build a deterministic, user-visible completion boundary."""
    plan = [
        {
            "id": clean_goal(
                str(item.get("id") or item.get("check_id") or f"step-{index + 1}"),
                96,
            ),
            "text": clean_goal(str(item.get("text") or ""), 180),
            "status": str(item.get("status") or "pending"),
        }
        for index, item in enumerate(plan_items or [])
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if requires_plan is None:
        requires_plan = is_complex_task(user_goal, task_type)
    if requires_plan and not plan:
        code_like = str(task_type or "") in {
            "code_task", "file_task", "modify_task",
        } or any(token in str(user_goal or "").lower() for token in (
            "代码", "仓库", "项目", "重构", "修复", "code", "repository",
            "refactor", "bug",
        ))
        if code_like:
            default_steps = (
                "读取真实入口、契约、依赖关系与构建链，确认现状和边界",
                "按架构边界完成一组可独立验证的代码修改",
                "运行定向测试，并根据失败证据修正实现",
                "复核差异、完整验证与发布边界，记录未验证限制",
            )
        else:
            default_steps = (
                "读取与目标直接相关的真实状态并确认完成边界",
                "完成一组可独立验证的修改或产出",
                "运行确定性验证并根据失败证据修正",
                "交付结果、证据和仍需继续的事项",
            )
        plan = [
            {"id": f"step-{index + 1}", "text": text, "status": "pending"}
            for index, text in enumerate(default_steps)
        ]

    criteria: list[dict[str, Any]] = [
        {"check_id": "output_delivery", "label": "交付非空且可读取的结果", "required": True},
    ]
    if requires_plan or plan:
        criteria.append({
            "check_id": "plan_closure",
            "label": "计划项全部完成或明确标记跳过",
            "required": True,
        })
    mode = str(execution_mode or "").lower()
    if "agent_team" in mode or "multi_agent" in mode:
        criteria.append({
            "check_id": "agent_completion",
            "label": "全部协作分工已完成且没有未解释的失败",
            "required": True,
        })
    elif any(token in mode for token in ("agent", "react", "tool", "orchestrator")):
        criteria.append({
            "check_id": "tool_execution",
            "label": "工具调用没有未解释的失败",
            "required": True,
        })
    if evidence_expected:
        criteria.extend([
            {"check_id": "claim_grounding", "label": "事实主张有可解析证据锚点", "required": True},
            {"check_id": "citation_integrity", "label": "引用编号属于本轮证据集合", "required": True},
        ])
    if artifact_required:
        criteria.append({
            "check_id": "artifact_delivery",
            "label": "要求的文件已在会话工作区验证存在",
            "required": True,
        })
    if str(acceptance or "").strip():
        criteria.append({
            "check_id": "user_acceptance",
            "label": clean_goal(acceptance, 240),
            "required": True,
            "source": "user",
        })

    return {
        "schema": CONTRACT_SCHEMA,
        "run_id": str(run_id or ""),
        "conversation_id": str(conversation_id or ""),
        "execution_scope_id": str(execution_scope_id or ""),
        "goal": clean_goal(user_goal),
        "goal_source": "user_message",
        "task_type": str(task_type or ""),
        "execution_mode": str(execution_mode or ""),
        "requires_plan": bool(requires_plan),
        "method": "inspect_plan_act_verify_handoff",
        "evidence_policy": "runtime_facts_only",
        "success_criteria": criteria,
        "plan": plan,
    }


def method_prompt(user_goal: str, task_type: str = "") -> str:
    """Return compact operating rules; never asks the model to expose CoT."""
    if not is_complex_task(user_goal, task_type):
        return (
            "\n\n## 任务完成纪律\n"
            "先给结果，再给必要依据；不知道或未验证的内容明确标注，不能把推测写成事实。"
            "不要声称执行过未实际执行的命令、检索或测试。"
        )
    return (
        "\n\n## 任务完成纪律（复杂任务）\n"
        "- 先读取与目标直接相关的真实状态、文件、接口或数据，再决定怎么改；已有能力先复用，"
        "不要凭印象重复造功能。\n"
        "- 先界定目标和完成条件；三步以上用 update_todo 建完整计划，按依赖顺序推进，"
        "每完成一项立即更新。\n"
        "- 工具报错、测试失败和接口返回都是诊断证据：定位具体根因，做最小修正，"
        "然后重跑同一验证；不能靠换一句模型回答掩盖失败。\n"
        "- 用户指出遗漏时，先审计相关范围找出系统性漏项，再修，不只补用户点名的一个表象。\n"
        "- 只有工具输出、持久化状态或确定性检查才能证明执行完成；模型自述、代码看起来正确、"
        "语法平衡都不能单独作为完成证据。\n"
        "- 交付时按“结果、已验证、未验证或限制、下一步”收口。中间更新简洁说明当前证据和下一动作，"
        "不要输出隐藏思维链，也不要用大段工具日志淹没用户。"
    )
