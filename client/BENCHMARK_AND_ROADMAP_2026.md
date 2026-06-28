# HashMM 对标完善方案（2026-06-06 · 以真实代码 + 2026 最新竞品核对为准）

> 写作前我做了三件事，避免 AI 幻觉：
> 1. **重新核对你的真实代码**（5.8 万行 / 249 py），而非依赖文档描述；下面每条"你已有/你缺"都标了代码证据或核对结论。
> 2. **联网核对 2026 年 6 月竞品真实状态**：Claude Code harness、LightRAG、RAGFlow、R2R 的最新 release / 文档，标了来源与日期。
> 3. **只推可行、对你有用、且不违反你红线的项**（不碰 torch/vllm/faiss-gpu，默认关，可注入，永不抛错，不动已达标的 KG）。凡是"看着炫但对你 ROI 低/风险高"的，我明确标注"不建议"并说明理由。

---

## 一、核对中纠正的几处"文档与代码不符"（这些先讲，因为影响判断）

| 项 | 文档/印象说法 | 真实代码 | 影响 |
|---|---|---|---|
| MCP | "MCP 暴露/消费 ✅"（MASTER_PLAN §1 第 11 层） | 只做了**消费端**（`tools/mcp_client.py`：http/sse + JSON-RPC `tools/list`/`tools/call` 完整实现），**没有暴露端**（不能把自己暴露成 MCP server 给别人连）。`api/mcp.py` 只是内部工具注册表，不是协议层。 | "暴露端"是真实差距，且**可行、ROI 高**（见 §三-1） |
| F11 路由 | "未接 generate" | 早已是完整路由器，已接 4 个高频任务（title/multiquery/intent/keyword）；answer 按设计留云 | 已完成，方向被 LightRAG 的 role-specific LLM 印证（见 §三-2） |
| API | 默认能力 | 你是 **Web 控制台优先**，对外 RESTful **SDK/API-first 能力弱**（无 pip 包、无 `client.xxx()` 式 SDK） | R2R 的核心护城河正是这个，是你最大的"叙事缺口"（见 §三-3） |

---

## 二、对标全景：你的真实位置（2026 年 6 月）

### 2.1 Agent Harness —— 对标 Claude Code（2026 最新）
2026 年 CC harness 的真实结构（来源：mer.vin / developersdigest / blakecrosley，2026-03~05）已演进到：
**29 个 hook 生命周期点**、**Agent Teams（独立进程 + 双向通信 + 共享 task list）**、**Plugins/Marketplace（skills+hooks+subagents 打包分发）**、**子代理用更便宜模型做粗活**、**Claude Managed Agents（harness 即 REST API）**。

| 能力 | CC 2026 | HashMM 真实代码 | 差距判断 |
|---|---|---|---|
| Agent loop | ✅ | ✅ `agent/loop.py`+`nodes.py`+`graph.py`（LangGraph） | 持平 |
| 子代理（隔离上下文/只回摘要） | ✅ | ✅ `agent/orchestrator.py`(389行,有 SubTask/TaskPlan/team_for_plan/plan_needs_confirmation) | 持平 |
| **多生命周期 hooks** | ✅ 29 点 | ⚠️ `tool_governance.py` 只有 **PreTool 审批**，无 PostTool/PreCompact/SubagentStop | **真实差距，可行**（见 §三-4） |
| 上下文压缩 | ✅ PreCompact hook | ✅ `context_manager.py`(compact + 注入式 summarize_fn + pinned/recent) | 持平 |
| 权限/ACL | ✅ | ✅ `permissions.py`(7层)+`access_control.py` | 持平 |
| 子代理省成本（粗活用小模型） | ✅ | ⚠️ 有 F11 路由但**未按"子代理角色"路由** | 小增量（见 §三-2） |
| **Plugins/打包分发** | ✅ marketplace | ❌ 你有 skills 但无打包/分发机制 | **不建议**（你是单体应用，非 IDE 插件生态，ROI 低） |
| Agent Teams（独立进程双向） | ✅ 实验 | ❌ 你是单进程 master+worker | **不建议**（单卡单进程，强行多进程反而增加复杂度与显存压力） |

**结论**：你的 harness 对标 CC **真实差距只有 2 个值得补**：①MCP 暴露端；②多生命周期 hooks。其余要么持平，要么对你的形态（单卡本地单体）不适用。

### 2.2 RAG 框架 —— 对标 LightRAG / RAGFlow / R2R（2026 最新）

| 能力 | 竞品最新（来源/日期） | HashMM 真实代码 | 差距判断 |
|---|---|---|---|
| 混合检索+重排+RRF | 都有 | ✅ `retrieval_pipeline.py`(713行) | 持平 |
| GraphRAG | LightRAG/RAGFlow/R2R 都有 | ✅ `kg/*` 完整（你已达标，533/546） | 持平甚至更细 |
| **多角色 LLM 配置** | LightRAG 2026.05：EXTRACT/QUERY/KEYWORDS/VLM 各自独立模型（github.com/hkuds/lightrag） | ⚠️ 你有 F11 但是 env 开关、非"角色→模型"产品化配置 | **小增量，ROI 中**（见 §三-2） |
| **标准化 trace 导出** | LightRAG 2025.11 集成 Langfuse；RAGAS 评测 | ⚠️ 你有 trace 事件 + 自建 eval gate（更强），但无 OTel/Langfuse 标准导出 | **可行，ROI 中**（见 §三-5） |
| **多种 chunking 策略** | LightRAG 2026.05：Fix/Recursive/Vector/Paragraph 四选 | 需核对你的 chunking 是否可选 | 待核对（见 §三-6） |
| 多模态/深度解析 | LightRAG 合并 RagAnything(MinerU/Docling)；RAGFlow 强解析 | ❌（你 PROMPT 里 MinerU 是 Park 项） | **维持 Park**（红线：MinerU 重依赖，风险高） |
| **API-first / SDK** | R2R：`pip install r2r` + `client.retrieval.agent()`（18k★, YC） | ❌ 你是 Web 控制台优先 | **最大叙事缺口，ROI 最高**（见 §三-3） |
| 多租户 | R2R/RAGFlow 企业版有 | ⚠️ 你有 tenancy 雏形 + 本轮加了回归测试 | 已有安全网，可推进 |

---

## 三、建议清单（按 ROI 排序；每项含：为什么 / 可行性 / 风险 / 工作量）

### 🥇 1. MCP Server 暴露端（把 HashMM 暴露成标准 MCP server）
- **为什么**：这是你对标 CC/RAGFlow 的**真实硬差距**，且最契合你的定位。RAGFlow 2026 已支持"通过 MCP 暴露知识库"（github.com/infiniflow/ragflow，2026-04）。暴露成 MCP server 后，**Claude Code / Cursor / 任何 MCP 客户端都能直接把你的 RAG+KG 当工具调用** —— 这是"本地优先 RAG-Agent"叙事的放大器。
- **可行性**：高。你已有消费端 JSON-RPC 实现可复用；只需反向加一个 `/mcp` server 端点，暴露 `search`/`rag`/`kg_query` 几个工具，走 SSE 或 HTTP JSON-RPC。
- **风险**：低。新增端点，默认关（env 开），不碰主链。
- **工作量**：中（1 个新模块 + 1 个端点 + 鉴权复用）。
- **红线**：纯 Python（stdlib http/json 或你已有的 httpx），零新依赖。

### 🥈 2. F11 升级为"角色→模型"显式配置（对齐 LightRAG role-specific LLM）
- **为什么**：你 F11 已对，但 LightRAG 2026.05 把它产品化成"每个角色（抽取/查询/关键词/VLM）配独立模型"。你可以把 LOCAL_TASKS 的 env 开关升级成**一张可配置的"任务→模型"表**（在管理后台可视化配），既是功能也是卖点。子代理粗活用小模型也并进来（CC 2026 同款思路）。
- **可行性**：高。你已有 `model_router.py`(路由表) + `llm_router.py`(F11)，合并成一张可配置表即可。
- **风险**：低。默认空表=现状。
- **工作量**：中（后端配置表 + 前端一个配置面板）。

### 🥉 3. 对外 RESTful SDK / API-first 能力（对标 R2R 的护城河）
- **为什么**：R2R（18k★，YC）证明"API-first + 一行 `client.retrieval.agent()`"是 RAG 产品的核心护城河。你功能已全，但**对外只有 Web 控制台**，开发者无法 `pip install` 你的 client 去集成。补一层**稳定的对外 REST API + 一个轻量 Python client**，能让你的项目从"一个应用"变成"一个可被集成的平台"。
- **可行性**：中。你已有 FastAPI 184 条路由，多为内部用。需梳理出一组**稳定对外契约**（search/rag/agent/ingest），加 API key 鉴权，写一个薄 client。
- **风险**：中。要小心别把内部端点直接暴露（安全）。建议新开 `/v1/*` 命名空间，与内部 `/api/*` 隔离。
- **工作量**：大（契约设计 + 文档 + client 包）。**这是最值得做但最重的一项**，建议分阶段。

### 4. 多生命周期 Hooks（PostTool/PreCompact/SubagentStop，对齐 CC harness）
- **为什么**：CC harness 的确定性控制靠 29 个 hook 点；你只有 PreTool 审批。补 PostTool（审计/格式化）、PreCompact（压缩前归档）、SubagentStop（聚合子代理结果）能让你的 harness 叙事更完整。
- **可行性**：高。你已有 `tool_governance.py` 的 hook 框架，扩生命周期点即可。
- **风险**：低。默认关，每个 hook 是 no-op 除非注册。
- **工作量**：小-中。

### 5. 标准化 Trace 导出（OTel 或 Langfuse 风，对齐 LightRAG）
- **为什么**：你有 trace 事件 + dashboard 聚合（本轮已做），但无**标准化导出**。LightRAG 2025.11 集成 Langfuse。加一个 OTel span 导出（或 Langfuse 兼容的 JSON），能接进任何标准可观测后端。
- **可行性**：中。你的 trace 已有 node/phase/step_id（本轮时间线已铺好），转成 OTel span 是自然延伸。
- **风险**：低-中。OTel SDK 是纯 Python 但是新依赖——**需先 `pip download --no-deps` 验证不拖 torch**；不行就 stdlib 自实现一个 OTLP JSON 导出。
- **工作量**：中。

### 6. 多种 chunking 策略可选（对齐 LightRAG Fix/Recursive/Vector/Paragraph）
- **为什么**：LightRAG 2026.05 把 chunking 做成 4 选。若你现在是固定 chunking，加可选策略能提升不同语料的检索质量。
- **可行性**：需先核对你 `pipeline/ingest.py` 现在的 chunking 实现。**动 ingest 会影响索引**，按你红线**需先建检索回归测试再动**。
- **风险**：中（碰索引）。
- **工作量**：中。**建议放在 API/MCP 之后**。

### ❌ 明确不建议的（避免你浪费精力）
- **Plugins/Marketplace 打包分发**：你是单卡单体应用，不是 IDE 插件生态，做分发市场 ROI 极低。
- **Agent Teams 多独立进程**：单卡 24G 显存，多进程跑模型反而抢显存、增复杂度；你的 master+worker 单进程已够。
- **MinerU 深度解析**：维持 Park。重依赖、与你"零新重依赖"红线冲突，收益不确定。
- **换向量库/存储后端**（如 R2R 用 pgvector）：你 FAISS 已稳定达标，换无收益还高风险。

---

## 四、推荐的推进顺序（务实版）

1. **MCP Server 暴露端**（🥇 最契合定位、可行、低风险）— 先做这个，立刻让"可被任何 Agent 调用"成立。
2. **多生命周期 Hooks**（小而稳，补齐 harness 叙事）。
3. **F11 角色→模型可配置表**（把已有优势产品化）。
4. **标准化 Trace 导出**（先验证依赖红线）。
5. **对外 RESTful SDK / API-first**（最重、最有价值，分阶段做；这是从"应用"到"平台"的关键）。
6. （可选，需先建检索回归测试）多种 chunking 策略。

每一项仍遵守：默认关 / 可注入 / 永不抛错 / 关闭零变化 / 零新重依赖 / 不碰 torch·vllm·faiss-gpu / 动主检索链前先建回归测试 / 每步进深度功能测试。

---

## 五、一句话总结
你的功能完整度已经很高（harness ~10.5 层、GraphRAG 达标、eval 门禁强）。**真正能拉开差距的不是再堆 RAG 功能，而是"可被集成性"**：①MCP 暴露端（让别的 Agent 用你）+ ②API-first SDK（让开发者 `pip install` 你）。这两件把你从"一个好用的本地 RAG 应用"变成"一个能嵌进别人 Agent 生态的本地优先 RAG 平台"——这是 2026 年 R2R/RAGFlow 正在抢的位置，而你"单卡全本地 + 完整 harness + eval 门禁 + 可视化控制台"的组合，没有竞品同时具备。
