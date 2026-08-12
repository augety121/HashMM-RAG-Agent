"""Sub-Agent Orchestrator — decompose complex tasks into isolated workers.

Inspired by Hermes Agent's contained sub-agents:
  - Each sub-task gets its own context window + tool set
  - Results are aggregated by the orchestrator
  - Prevents context overflow on multi-step tasks

Example: "对比小米和腾讯2024年财务，做分析报告PPT"
  → Worker 1: search 小米 financials (isolated)
  → Worker 2: search 腾讯 financials (isolated)
  → Worker 3: compare + outline (gets Worker 1+2 results)
  → Worker 4: generate PPTX (gets outline)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Generator

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.orchestrator")


@dataclass
class SubTask:
    """A single sub-task with its own context."""
    id: str
    description: str
    tool_hint: str = ""            # "search" | "analyze" | "generate" | "code"
    input_data: str = ""           # Context from parent/previous tasks
    result: str = ""               # Output after execution
    status: str = "pending"        # pending | running | done | error
    elapsed_ms: int = 0


@dataclass
class TaskPlan:
    """Decomposed task plan with ordered sub-tasks."""
    original_query: str
    subtasks: list[SubTask] = field(default_factory=list)
    final_result: str = ""
    strategy: str = ""


class SubAgentOrchestrator:
    """Decomposes complex queries into sub-agent tasks."""

    # Patterns that suggest multi-step tasks
    DECOMPOSE_PATTERNS = [
        (r'(?:对比|比较).+(?:和|与|跟).+', "compare"),
        (r'(?:做|生成|创建).+(?:PPT|报告|文档|Excel)', "document"),
        (r'(?:分析).+(?:然后|并且|同时).+', "multi_step"),
        (r'(?:先|首先).+(?:然后|再|接着).+', "sequential"),
    ]

    def __init__(self, llm_fn: Any = None, search_fn: Any = None):
        self.llm_fn = llm_fn
        self.search_fn = search_fn

    def should_decompose(self, query: str) -> bool:
        """Check if query would benefit from sub-agent decomposition."""
        for pattern, _ in self.DECOMPOSE_PATTERNS:
            if re.search(pattern, query):
                return True
        # Long complex queries
        if len(query) > 100 and any(w in query for w in ["并且", "同时", "然后", "还要", "另外"]):
            return True
        return False

    def plan(self, query: str) -> TaskPlan:
        """Create a task plan by decomposing the query.

        Uses rule-based decomposition first. Falls back to LLM if needed.
        """
        plan = TaskPlan(original_query=query)

        # Rule-based decomposition
        # Pattern: "对比 A 和 B" → search A + search B + compare
        compare_match = re.search(r'(?:对比|比较)\s*(.+?)\s*(?:和|与|跟)\s*(.+?)(?:的|$)', query)
        if compare_match:
            entity_a = compare_match.group(1).strip()
            entity_b = compare_match.group(2).strip()
            # v12: Extract metrics/topic from original query for better search
            metrics_kw = []
            for kw in ["财务", "营收", "利润", "数据", "年报", "业绩", "收入", "增长", "市值",
                        "毛利", "净利", "净利润", "总收入", "总资产", "成本", "费用", "现金流"]:
                if kw in query:
                    metrics_kw.append(kw)
            year_match = re.search(r'(20\d{2})', query)
            year = year_match.group(1) if year_match else ""

            # If user asked for financials but no specific metrics, add defaults
            if any(w in query for w in ["财务", "数据", "对比"]) and len(metrics_kw) < 2:
                metrics_kw.extend(["营收", "净利润", "总收入"])
            topic = f"{year} {' '.join(metrics_kw)}".strip() or "营收 净利润 核心财务数据"

            plan.strategy = "compare"
            plan.subtasks = [
                SubTask(id="search_a", description=f"{entity_a} {topic}", tool_hint="search"),
                SubTask(id="search_b", description=f"{entity_b} {topic}", tool_hint="search"),
                SubTask(id="analyze", description=f"对比分析 {entity_a} 和 {entity_b} 的{topic}", tool_hint="analyze"),
            ]
            # Check if document generation is also requested
            if any(w in query for w in ["PPT", "报告", "文档", "Excel", "表格"]):
                doc_type = "PPT" if "PPT" in query else "报告" if "报告" in query else "文档"
                plan.subtasks.append(
                    SubTask(id="generate", description=f"生成{doc_type}", tool_hint="generate")
                )
            return plan

        # Pattern: sequential steps "先...然后..."
        if "然后" in query or "再" in query or "接着" in query:
            parts = re.split(r'[，,。；;]?\s*(?:然后|再|接着|并且|同时)\s*', query)
            plan.strategy = "sequential"
            for i, part in enumerate(parts):
                part = part.strip()
                if part:
                    hint = "search" if any(w in part for w in ["查", "找", "搜", "检索"]) else \
                           "generate" if any(w in part for w in ["做", "生成", "创建", "写"]) else \
                           "analyze"
                    plan.subtasks.append(SubTask(id=f"step_{i}", description=part, tool_hint=hint))
            return plan

        # Default: single task, no decomposition
        plan.strategy = "single"
        plan.subtasks = [SubTask(id="main", description=query, tool_hint="search")]
        return plan

    # ── Agent team: map each sub-task kind to a specialist role with a tool
    #    allowlist (Claude-Code-style Agent Teams). The role's allowlist is the
    #    SAME one Worker enforces, and is surfaced in the plan so the division of
    #    labour + permission boundary is transparent and auditable. ──
    _HINT_TO_ROLE = {
        "search": "research",
        "analyze": "analysis",
        "generate": "writer",
        "code": "code",
    }
    _ROLE_TOOLS = {
        "research": ["kb_search", "web_search", "kg_query"],
        "analysis": ["kb_search"],
        "writer": ["create_file", "create_document", "create_pptx_from_plan"],
        "code": ["execute_code"],
    }
    _ROLE_LABEL = {
        "research": "检索专员", "analysis": "分析专员",
        "writer": "文档专员", "code": "代码专员",
    }

    def role_for(self, tool_hint: str) -> str:
        return self._HINT_TO_ROLE.get(tool_hint, "research")

    def team_for_plan(self, plan: "TaskPlan") -> dict:
        """Describe the agent team a plan would use: which specialist runs each
        step and what tools it is (and isn't) allowed. Transparent + auditable.
        """
        members = []
        roles_used = []
        for i, t in enumerate(plan.subtasks):
            role = self.role_for(t.tool_hint)
            roles_used.append(role)
            members.append({
                "step": i + 1,
                "role": role,
                "role_label": self._ROLE_LABEL.get(role, role),
                "task": t.description,
                "allowed_tools": self._ROLE_TOOLS.get(role, []),
            })
        # distinct roles, preserving order
        distinct = list(dict.fromkeys(roles_used))
        from hashmm.agent.mesh import admit_mesh_work
        requested_mode = "pipeline" if plan.strategy in {"sequential", "single"} else "auto"
        admission = admit_mesh_work(
            goal=plan.original_query,
            roles=[
                {
                    "id": task.id,
                    "role": self.role_for(task.tool_hint),
                    "task": task.description,
                }
                for task in plan.subtasks
            ],
            requested_mode=requested_mode,
            adapter="legacy_orchestrator",
        )
        return {
            "n_members": len(members),
            "roles": distinct,
            "members": members,
            "mesh_admission": admission,
            "resolved_mode": admission["resolved_mode"],
        }

    # ── Plan Mode (Claude-Code-style: preview → confirm → execute) ──
    # Side-effecting step kinds: these change state (files, code, external) and
    # are what "plan mode" exists to gate. Pure search/analyze need no approval.
    _SIDE_EFFECT_HINTS = {"generate", "code", "write", "execute"}

    def plan_needs_confirmation(self, plan: "TaskPlan") -> bool:
        """A plan needs human confirmation only if it has side-effecting steps
        (document/file generation, code execution). Pure retrieval/analysis
        plans run freely — confirmation friction only where it matters."""
        return any(t.tool_hint in self._SIDE_EFFECT_HINTS for t in plan.subtasks)

    def plan_summary(self, plan: "TaskPlan") -> dict:
        """Human-readable preview of what the agent intends to do, for the
        confirm gate. Returns steps with a side-effect flag + an overall risk."""
        steps = []
        for i, t in enumerate(plan.subtasks):
            side = t.tool_hint in self._SIDE_EFFECT_HINTS
            steps.append({
                "step": i + 1,
                "description": t.description,
                "kind": t.tool_hint,
                "side_effect": side,
                "label": {"search": "检索", "analyze": "分析",
                          "generate": "生成文件/文档", "code": "执行代码"}.get(t.tool_hint, t.tool_hint),
            })
        needs = self.plan_needs_confirmation(plan)
        team = self.team_for_plan(plan)
        return {
            "query": plan.original_query,
            "strategy": plan.strategy,
            "resolved_mode": team["resolved_mode"],
            "steps": steps,
            "n_steps": len(steps),
            "needs_confirmation": needs,
            "risk": "high" if needs else "low",
            "team": team,
            "mesh_admission": team["mesh_admission"],
        }

    def execute_plan(
        self, plan: TaskPlan, history: list[dict] | None = None,
    ) -> Generator[tuple[str, dict], None, None]:
        """Execute a task plan, yielding progress events.

        Yields: (event_type, data) tuples
          - ("subtask_start", {"id": ..., "description": ...})
          - ("subtask_result", {"id": ..., "result": ..., "elapsed_ms": ...})
          - ("final", {"result": ..., "strategy": ...})
        """
        history = history or []
        accumulated_context = ""

        # 多 Agent 硬约束闸（大厂标准）：子任务上限 + 墙钟时限 + 连续失败终止。默认值见 mas_guard。
        from hashmm.agent.mas_guard import MasBudget
        _budget = MasBudget()

        for i, task in enumerate(plan.subtasks):
            # 开始每个子任务前检查硬约束：命中即提前收尾（用已完成的结果），不硬往下跑。
            _stop, _why = _budget.should_stop(i)
            if _stop:
                yield ("mas_stop", {"reason": _why, "completed": i,
                                    "total": len(plan.subtasks), **_budget.snapshot()})
                break
            task.status = "running"
            yield ("subtask_start", {"id": task.id, "description": task.description, "step": i + 1, "total": len(plan.subtasks)})

            t0 = time.time()
            # Agent-team isolation: scope tool access to this sub-task's role for
            # the duration of its execution (research can't write, etc). Enforced
            # by the role_scope hook at the tool entry point.
            _role = self.role_for(task.tool_hint)
            try:
                from hashmm.hooks import set_active_role
                set_active_role(_role, set(self._ROLE_TOOLS.get(_role, [])))
            except Exception as _e:
                log_suppressed(logger, _e)
            try:
                if task.tool_hint == "search" and self.search_fn:
                    # Use retrieval pipeline
                    result = self._execute_search(task.description, accumulated_context)
                elif task.tool_hint == "analyze" and self.llm_fn:
                    # Use LLM with accumulated context
                    result = self._execute_analysis(task.description, accumulated_context)
                elif task.tool_hint == "generate" and self.llm_fn:
                    result = self._execute_generation(task.description, accumulated_context)
                else:
                    # Default: LLM direct
                    result = self._execute_llm(task.description, accumulated_context)

                task.result = result
                task.status = "done"
                accumulated_context += f"\n\n[{task.description}的结果]:\n{result[:2000]}"
            except Exception as e:
                task.result = f"执行失败: {str(e)[:200]}"
                task.status = "error"
                result = task.result
            finally:
                try:
                    from hashmm.hooks import set_active_role
                    set_active_role(None, None)  # clear role scope after the step
                except Exception as _e:
                    log_suppressed(logger, _e)

            task.elapsed_ms = round((time.time() - t0) * 1000)
            _budget.record(task.status == "done")   # 登记成败，用于连续失败终止
            # SubagentStop hook: fires when this sub-agent finishes, so a registered
            # hook can aggregate / log / verify the worker's output. No-op when none
            # registered → zero behaviour change. Never raises.
            try:
                from hashmm.hooks import run_subagent_stop_hooks
                run_subagent_stop_hooks(task.id, result, {
                    "status": task.status, "role": _role,
                    "description": task.description, "elapsed_ms": task.elapsed_ms,
                })
            except Exception as _e:
                log_suppressed(logger, _e)
            yield ("subtask_result", {
                "id": task.id, "result": result[:500],
                "elapsed_ms": task.elapsed_ms, "status": task.status,
            })

        # Final result is the last successful task's result
        done_tasks = [t for t in plan.subtasks if t.status == "done"]
        plan.final_result = done_tasks[-1].result if done_tasks else "所有子任务执行失败"
        yield ("final", {"result": plan.final_result, "strategy": plan.strategy})

    # ── Execution helpers ──

    def _execute_search(self, description: str, context: str) -> str:
        """Execute a search sub-task with web search fallback."""
        local_result = ""

        # Step 1: Local knowledge base search
        if self.search_fn:
            try:
                results = self.search_fn(description)
                if isinstance(results, list) and results:
                    # Check retrieval quality
                    top_score = results[0].get("score", 0) if results else -999
                    if top_score > -2.0:  # Good quality threshold
                        local_result = "\n".join(
                            f"[{r.get('filename', '')} p.{r.get('page', '')}] {r.get('text', '')[:300]}"
                            for r in results[:5]
                        )
                elif isinstance(results, str):
                    local_result = results[:2000]
            except Exception as _e:
                log_suppressed(logger, _e)

        # Step 2: If local results are poor, try web search
        if not local_result or len(local_result.strip()) < 100:
            web_result = self._web_search_fallback(description)
            if web_result:
                local_result = (local_result + "\n\n[联网搜索补充]\n" + web_result) if local_result else web_result

        if local_result:
            return local_result
        return self._execute_llm(description, context)

    def _web_search_fallback(self, query: str) -> str:
        """v12: Web search fallback when local knowledge is insufficient."""
        try:
            from hashmm.api.tool_registry import execute_tool
            result = execute_tool("web_search", {"query": query, "num_results": 3}, {})
            if result and not result.startswith("Error") and not result.startswith("❌") and not result.startswith("失败 ·"):
                return result[:3000]
        except Exception as _e:
            log_suppressed(logger, _e)
        return ""

    def _execute_analysis(self, description: str, context: str) -> str:
        """Execute an analysis sub-task using LLM."""
        prompt = (
            f"请根据以下已收集的信息进行分析：\n\n{context[:4000]}\n\n"
            f"分析任务：{description}\n\n"
            "请给出结构化的分析结果。"
        )
        return self._call_llm(prompt)

    def _execute_generation(self, description: str, context: str) -> str:
        """Execute a document generation sub-task — actually creates PPTX/DOCX files."""
        # Determine document type
        is_ppt = any(w in description.lower() for w in ["ppt", "演示", "幻灯片", "slides"])
        is_word = any(w in description.lower() for w in ["word", "docx", "文档", "报告"])

        if is_ppt or is_word:
            try:
                from hashmm.tools import get_doc_generator
                gen = get_doc_generator(self.llm_fn)
                if is_ppt:
                    result = gen.generate_pptx(description, data_context=context[:6000])
                else:
                    result = gen.generate_docx(description, data_context=context[:6000])

                if result.get("ok"):
                    return (
                        f"{result.get('message', '文件生成成功')}\n"
                        f"下载链接: {result.get('download_url', '')}\n"
                        f"文件名: {result.get('filename', '')}"
                    )
                else:
                    return f"文件生成失败：{result.get('message', '未知错误')}"
            except Exception as e:
                logger.warning(f"Document generation failed: {e}")
                # Fall through to LLM-based outline

        # Fallback: generate structured outline with LLM
        prompt = (
            f"请根据以下分析结果，{description}：\n\n{context[:4000]}\n\n"
            "请给出结构化的内容大纲，包含具体数据和要点。"
        )
        return self._call_llm(prompt)

    def _execute_llm(self, description: str, context: str) -> str:
        """Generic LLM execution."""
        prompt = description
        if context:
            prompt = f"背景信息：\n{context[:3000]}\n\n任务：{description}"
        return self._call_llm(prompt)

    def _call_llm(self, prompt: str) -> str:
        """Call LLM with a single prompt."""
        if not self.llm_fn:
            return "LLM 未配置"
        try:
            if hasattr(self.llm_fn, 'quick_call'):
                return self.llm_fn.quick_call("你是专业分析助手。", prompt, max_tok=2000)
            elif hasattr(self.llm_fn, 'chat'):
                return self.llm_fn.chat([
                    {"role": "system", "content": "你是专业分析助手。"},
                    {"role": "user", "content": prompt},
                ])
            return str(self.llm_fn(prompt))
        except Exception as e:
            return f"LLM 调用失败: {str(e)[:100]}"
