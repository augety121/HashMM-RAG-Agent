"""Worker Agent — 隔离的、单一职责的子 agent（对标 CC subagent / Microsoft orchestrator-worker）。

设计原则（来自大厂实践 + 血泪教训）：
  1. Worker 是【无状态、一次性】的——不是常驻人格。用完即弃。
  2. Worker 有【自己独立的上下文窗口】——它的中间检索/思考不污染主 agent 的上下文。
     主 agent 只拿到 worker 的【最终总结】，不是它的全部过程。
  3. Worker 工具【受限】——只给它完成本职任务需要的工具（如检索 worker 只能 kb_search）。
  4. Worker 数量【硬上限】——避免 0.95^N 可靠性塌缩（串 10 个 agent 只剩 60% 可靠）。
  5. 默认【不】用 worker——能主 agent 一个人干完就一个人干完。只有明确需要并行/隔离时才 spawn。

这不是一个花哨的多 agent swarm，而是"主 agent 在需要时派一个临时专员去查一件事"。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.worker")

# Worker 比主循环更短——它只做一件事
WORKER_MAX_ITERATIONS = 3
WORKER_MAX_TOOL_CALLS = 4

# 不同类型 worker 的工具白名单（受限工具集）
WORKER_TOOLSETS = {
    "research": ["kb_search", "web_search", "kg_query"],   # 只能查，不能写
    "analysis": ["kb_search"],                              # 分析为主，可补检索
    "code": ["execute_code"],                               # 只能跑代码
    "writer": ["create_file", "kb_search"],                 # V80: 写作专员——产出文件，可补查事实
}


class Worker:
    """单一职责的隔离 worker agent。

    用法：
        w = Worker(llm_fn, role="research", tools_filter=[...])
        result = await w.run("查找小米2025年汽车业务收入", parent_context="")
        # result 是一个简短的字符串总结，可直接喂回主 agent
    """

    def __init__(
        self,
        llm_fn: Any,
        role: str = "research",
        user_id: str = "",
        max_iterations: int = WORKER_MAX_ITERATIONS,
    ):
        self.llm_fn = llm_fn
        self.role = role if role in WORKER_TOOLSETS else "research"
        self.user_id = user_id
        self.max_iterations = max_iterations
        self._allowed = set(WORKER_TOOLSETS[self.role])
        # 复用主循环的工具执行器和 schema，但只暴露白名单内的
        self._executors = self._get_executors()
        self._tools = self._get_allowed_tool_schemas()

    def _get_executors(self) -> dict:
        try:
            from hashmm.api.tool_registry import get_executor_map
            return get_executor_map()
        except ImportError:
            return {}

    def _get_allowed_tool_schemas(self) -> list[dict]:
        from hashmm.agent.loop import AGENT_TOOLS
        return [
            t for t in AGENT_TOOLS
            if t.get("function", {}).get("name") in self._allowed
        ]

    def _system_prompt(self) -> str:
        role_desc = {
            "research": "你是检索专员。你的唯一任务是用检索工具找到所需的事实数据，然后用要点形式简洁汇报。",
            "analysis": "你是分析专员。基于已有数据做分析，必要时补充检索。输出简洁结论。",
            "code": "你是代码执行专员。运行代码完成计算或数据处理，汇报结果。",
            "writer": "你是写作专员。基于「已知上下文」里的材料把内容写成文件交付"
                      "（用 create_file），材料不够时可补一次检索。先写文件再汇报文件名。",
        }
        return (
            f"{role_desc.get(self.role, role_desc['research'])}\n\n"
            "重要：\n"
            "- 你只负责这一个子任务，不要发散。\n"
            "- 最多调用工具 2-3 次，拿到信息就汇报。\n"
            "- 用标准 function calling 调用工具，不要输出 XML 文本。\n"
            "- 最终用 5 行以内的要点汇报你的发现，不要长篇大论。\n"
        )

    async def run(self, task: str, parent_context: str = "", on_step=None) -> dict:
        """执行子任务，返回 {"summary": str, "tool_calls": int, "elapsed_ms": int}。

        Worker 的全部中间过程都在这里消化，主 agent 只拿到 summary。
        """
        t0 = time.time()
        if self.llm_fn is None or not hasattr(self.llm_fn, "call_with_tools"):
            return {"summary": "（worker 无法运行：LLM 未就绪）", "tool_calls": 0, "steps": [],
                    "elapsed_ms": 0}

        messages = [{"role": "system", "content": self._system_prompt()}]
        if parent_context:
            messages.append({"role": "user", "content": f"已知上下文：\n{parent_context[:2000]}"})
        messages.append({"role": "user", "content": f"子任务：{task}"})

        from hashmm.agent.loop import parse_text_tool_calls

        tool_calls_made = 0
        final_text = ""
        steps: list[str] = []  # V51: 子任务执行轨迹（供主 agent 时间线展示）
        # V58: 子代理接入与主循环同一条守卫管线（预算/连续去重统一语义；
        # 权限走 worker 自有白名单，故 permissions=None）。
        from hashmm.agent.tool_pipeline import ToolPipeline, TurnState
        from hashmm.agent.tool_pipeline import _SEARCH_TOOLS as _WS, _EXEC_TOOLS as _WE
        _wpipe = ToolPipeline()
        _wturn = TurnState()

        for _ in range(self.max_iterations):
            use_tools = self._tools if tool_calls_made < WORKER_MAX_TOOL_CALLS else None
            try:
                resp = await asyncio.to_thread(self.llm_fn.call_with_tools, messages, use_tools)
                response = resp.message if hasattr(resp, "message") else resp
            except Exception as e:
                logger.warning(f"[Worker:{self.role}] LLM 调用失败: {e}")
                return {"summary": f"（worker 执行出错: {str(e)[:80]}）",
                        "tool_calls": tool_calls_made, "steps": steps,
                        "elapsed_ms": round((time.time() - t0) * 1000)}

            tool_calls = getattr(response, "tool_calls", None)
            content = getattr(response, "content", "") or ""

            # 文本工具调用兜底
            if not tool_calls and content and "<invoke" in content:
                parsed, cleaned = parse_text_tool_calls(content)
                if parsed:
                    tool_calls = parsed
                    content = cleaned

            if not tool_calls:
                final_text = content
                break

            # 记录 assistant 消息
            messages.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {"id": getattr(tc, "id", f"c{i}"), "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for i, tc in enumerate(tool_calls)
                ],
            })

            for tc in tool_calls:
                tool_calls_made += 1
                name = tc.function.name
                try:
                    _args_brief = str(json.loads(tc.function.arguments))[:60]
                except Exception:
                    _args_brief = ""
                steps.append(f"{name}({_args_brief})" if _args_brief else name)
                if on_step is not None:
                    try:
                        on_step(steps[-1])   # V55: 实时透出子任务步骤
                    except Exception:
                        pass
                # 强制工具白名单——worker 不能越权
                if name not in self._allowed:
                    result_text = f"（工具 {name} 不在本 worker 权限内，已拒绝）"
                else:
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, AttributeError):
                        args = {}
                    # V58: 守卫管线裁决（计数语义与主循环一致：search 先加、exec 后加）
                    if name in _WS:
                        _wturn.search_calls += 1
                    try:
                        _ck = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                    except (TypeError, ValueError):
                        _ck = (name, str(args))
                    _dec = _wpipe.evaluate(name, args, _ck, _wturn, permissions=None)
                    if name in _WE:
                        _wturn.exec_calls += 1
                    if _dec is not None:
                        result_text = str((_dec.result or {}).get("message", "（已被守卫拦截）"))
                    else:
                        result_text = await self._exec(name, args)
                        _wturn.last_call_key = _ck
                        _wturn.last_result_text = str(result_text)[:2000]
                messages.append({
                    "role": "tool",
                    "tool_call_id": getattr(tc, "id", "c0"),
                    "content": result_text[:3000],
                })

        return {
            "summary": final_text.strip() or "（worker 未产出明确结论）",
            "tool_calls": tool_calls_made,
            "steps": steps,
            "elapsed_ms": round((time.time() - t0) * 1000),
        }

    async def _exec(self, name: str, args: dict) -> str:
        executor = self._executors.get(name)
        if not executor:
            return f"（未知工具: {name}）"
        ctx = {"user_id": self.user_id, "conv_id": ""}
        try:
            result = await asyncio.to_thread(executor, args, ctx)
            if isinstance(result, str):
                return result[:3000]
            if isinstance(result, dict):
                return str(result.get("message", "") or result.get("data", "") or result)[:3000]
            return str(result)[:3000]
        except Exception as e:
            return f"（工具执行失败: {str(e)[:100]}）"
