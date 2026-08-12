# HashMM Agent 执行路径分支图 + 死代码确权（只读分析，未改任何代码）

你让我先把 streaming.py 里 ReactAgent / AgentLoop 和 orchestrator 的 AgentLoopHandler 三者理清再决定。
下面是逐个 grep 验证后的结论，**这一步没有改任何代码**。

## 一、真实入口与活的两条路径

```
POST /api/conversations/{conv_id}/stream          ← 唯一的对话 SSE 入口（server.py:747）
        │
        └── generate_sse_async()                  ← streaming.py:95，约 1100 行的主函数（活）
                │   分类用的是 hashmm.api.intent 的
                │   classify_rules / classify_fallback / intent_to_task_type（server.py:47 传入）
                │
                ├──【活】AgentLoop          streaming.py:331
                │     触发：needs_agent_loop and task_type != "direct_task"
                │     needs_agent_loop = 有文件意图 / 有 URL / 多步 / 文件型任务 /
                │                        真实世界任务(订票·规划·行程) / 对比(A和B) / 代码意图
                │     用途：复杂多步 / 要用工具的任务（文件、URL、规划、对比、代码）
                │     来源：hashmm.agent.loop.AgentLoop（1974 行的 5 阶段 Agent 主循环）
                │
                └──【活】ReactAgent         streaming.py:1121
                      触发：主 RAG 路径 return 之后的兜底分支，注释明确写「modify_task path」
                      用途：modify_task —— 改代码 / 改文件的 ReAct 回路
                      来源：hashmm.react_agent.ReactAgent
```

**这两条都是活的、各管各的场景**（一个管复杂任务、一个管改代码），不是冗余 —— 当初担心的「三胞胎重复」，
这两个其实是有意的多路径。

## 二、死的那条：整个 v10.0「Task Handlers + orchestrator + chain_executor」架构

```
hashmm/api/orchestrator.py        ← 死
   ├ classify()                   ← 没人 import（真正在用的 classify 在 hashmm.api.intent）
   └ create_handler()             ← 只被 server.py:45 import，但【全仓从未被调用】
        └ HANDLERS = {"complex_task": AgentLoopHandler, ...}   ← 只在这个死函数里被引用

hashmm/api/handlers/              ← 整包死（仅 base.SSEEvent 被 chain_executor 引用，而后者也死）
   ├ base.py        BaseHandler / SSEEvent / HeartbeatThread
   ├ direct.py      DirectHandler      ← 外部真实使用 0
   ├ knowledge.py   KnowledgeHandler   ← 外部真实使用 0
   ├ document.py    DocumentHandler    ← 外部「2 处」其实是 pptx_builder/tool_registry 里的注释，非代码
   ├ code.py        CodeHandler        ← 外部真实使用 0
   └ agent.py       AgentLoopHandler   ← 外部真实使用 0（只被死的 orchestrator 注册）

hashmm/api/chain_executor.py      ← 死（全仓没人调用；它 import 的 SSEEvent 只服务它自己）
```

### 判定依据（每条都 grep 验证过）
- `create_handler` 全仓只有 2 处：定义（orchestrator.py:134）+ import（server.py:45）。**没有任何调用点。**
- 5 个 handler 类的外部真实使用全是 0（DocumentHandler 的「2 处」是 docstring 注释）。
- `chain_executor` 全仓无人调用。
- handlers 包只被 orchestrator（死）和 chain_executor（死）import。
- `tests/` 对这些模块**零引用**；无 `importlib` / `__import__` 动态加载。

**结论**：v10.0 那套「按任务类型分发的 Handler 流水线 + orchestrator + chain_executor」已被
`generate_sse_async` 的内联逻辑整体取代，是历史遗留死代码。这正是完善方案说的「跨版本累积冗余」，而且比
预期更集中——能干净切除的就是这一坨。

## 三、建议（等你拍板，**我还没动**）

可安全删除以下 3 块（行为零变化，因为是死代码）：
1. `hashmm/api/orchestrator.py`
2. `hashmm/api/handlers/`（整个包：base/direct/knowledge/document/code/agent）
3. `hashmm/api/chain_executor.py`

删除时**唯一要同步改的活文件**：`hashmm/api/server.py:45` 那行
`from hashmm.api.orchestrator import create_handler` —— 删掉这行 import（它 import 进来的东西本来就没用）。
删完我会用 `py_compile` + 实际 import `server` / `streaming` 验证不破。

> 注意区分：`hashmm/agent/orchestrator.py` 的 `SubAgentOrchestrator` 是**另一个、活的**模块
> （streaming.py:506 在用），**不在**删除范围内。别和 `hashmm/api/orchestrator.py` 搞混。

要删就回我一声，我立刻执行并验证；想留着（比如你以后想复活 Handler 架构）也行，那我就转去做别的（第五个面板
「定时任务」或别的收敛）。
