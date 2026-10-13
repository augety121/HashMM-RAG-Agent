# HashMM 配置开关清单

> 由 `hashmm/settings.py` 自动生成。所有开关默认值即'零行为变化'基线。

> 注：本表登记核心高频开关；项目共有约 129 个 HASHMM_ 开关，其余为内部细调。


## 核心/环境

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_ENV` | production | str | 运行环境 (production/dev) |
| `HASHMM_DB_PATH` | (空) | path | SQLite 数据库路径（空=默认 data 下） |
| `HASHMM_DB_POOL_SIZE` | 16 | int | DB 连接池大小 |
| `HASHMM_CORS_ORIGINS` | (空) | str | 允许的 CORS 源（逗号分隔） |
| `HASHMM_REQUIRE_AUTH` | 0 | bool | 是否强制所有接口鉴权 |
| `HASHMM_ALLOW_INSECURE` | 0 | bool | 允许不安全配置启动（不建议生产） |

## 安全/鉴权

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_JWT_SECRET` | (空) | secret | JWT 签名密钥（生产必设随机长串） |
| `HASHMM_API_KEY` | (空) | secret | 对外 /v1 API key |
| `HASHMM_MCP_TOKEN` | (空) | secret | MCP server 鉴权 token |
| `HASHMM_METRICS_TOKEN` | (空) | secret | /metrics 鉴权 token |
| `HASHMM_METRICS_PUBLIC` | 0 | bool | 公开 /metrics（不鉴权，仅内网） |
| `HASHMM_TOOL_APPROVAL` | 0 | bool | 工具调用需审批 |
| `HASHMM_AUDIT_TOOLS` | 0 | bool | 审计工具调用 |
| `HASHMM_AUDIT_DIR` | (空) | path | 审计日志目录 |

## F11 LLM 路由

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_LLM_ROUTING` | 0 | bool | F11 云-本地路由总开关 |
| `HASHMM_LOCAL_LLM_PATH` | (空) | path | 本地 LLM 模型路径（F11 必需） |
| `HASHMM_LLM_TASK_ROUTING` | (空) | str | 任务→后端路由覆盖(JSON) |
| `HASHMM_PRIVACY_LOCAL` | 0 | bool | 隐私模式：全部走本地，不外发云端 |

## 检索

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_RETRIEVAL_STRATEGY` | (空) | str | 强制检索策略 |
| `HASHMM_KG_RETRIEVAL` | 0 | bool | 启用 KG 增强检索 |
| `HASHMM_AGENTIC_RETRIEVAL` | 0 | bool | 启用 agentic 检索 |
| `HASHMM_SEARCH_BACKEND` | (空) | str | 强制联网搜索后端 |
| `HASHMM_WEB_FALLBACK` | 0 | bool | 检索不到时联网兜底 |

## 知识图谱

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_KG_LLM_EXTRACT` | 0 | bool | 用 LLM 抽取 KG 实体关系 |
| `HASHMM_KG_LLM_HF_PATH` | (空) | path | KG 抽取用本地 HF 模型路径 |
| `HASHMM_KG_LLM_MODEL` | (空) | str | KG 抽取用模型名 |
| `HASHMM_KG_LLM_MAX_CHUNKS` | 0 | int | KG 抽取最大 chunk 数 |
| `HASHMM_KG_AUTO` | 0 | bool | 自动建图 |
| `HASHMM_KG_GLEANINGS` | 0 | int | KG 多轮 gleaning 次数 |
| `HASHMM_KG_TEMPORAL` | 0 | bool | 时序/双时态 KG |
| `HASHMM_KG_EVOLUTION` | 0 | bool | 自进化 KG 待审区 |
| `HASHMM_KG_LAZY_SUMMARIES` | 0 | bool | 社区摘要懒加载 |
| `HASHMM_KG_RESOLVE_DROP_NOISE` | 0 | bool | 实体消解时丢弃噪声 |
| `HASHMM_KG_FEWSHOT_RICH` | 0 | bool | KG 抽取用丰富 few-shot |

## Agent

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_AGENTIC_RAG` | 0 | bool | 启用 agentic RAG |
| `HASHMM_AGENTIC_MAX_HOPS` | 3 | int | agentic 最大跳数 |
| `HASHMM_AGENTIC_LOCAL_LOOPS` | 0 | int | 本地 agent 循环数 |
| `HASHMM_AGENT_HITL` | 0 | bool | human-in-the-loop |
| `HASHMM_AGENT_TIMELINE` | 0 | bool | agent 运行时间线 |
| `HASHMM_AGENT_STATE_DIR` | (空) | path | agent 状态持久化目录 |
| `HASHMM_PARALLEL_TOOLS` | 0 | bool | 并行执行只读工具（对标 FuturesOrdered） |
| `HASHMM_PARALLEL_TOOLS_MAX` | 4 | int | 并行工具并发上限 |
| `HASHMM_PROMPT_CACHE` | 0 | bool | 确定性辅助任务的 prompt 结果缓存 |
| `HASHMM_PROMPT_CACHE_TTL` | 3600 | int | prompt 缓存 TTL（秒） |
| `HASHMM_SUBAGENTS` | 0 | bool | 启用子代理 |
| `HASHMM_CONFIDENCE` | 0 | bool | 启用置信度评估 |

## 平台能力

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_MCP_SERVER` | 0 | bool | MCP server 暴露端 |
| `HASHMM_PUBLIC_API` | 0 | bool | 对外 /v1 RESTful API |
| `HASHMM_PUBLIC_API_OPEN` | 0 | bool | 对外 API 免鉴权（仅内网） |
| `HASHMM_MULTI_TENANT` | 0 | bool | 多租户隔离 |
| `HASHMM_SCHEDULER` | 0 | bool | 定时任务调度 |
| `HASHMM_DESIGN_RENDER` | 0 | bool | 受控设计渲染器 |

## 评估

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_EVAL_EXEC` | 0 | bool | eval 执行代码工具 |
| `HASHMM_EVAL_HOLDOUT` | 0 | bool | eval held-out 保真 |

## 视觉模型（V86 · 截屏/图片理解）

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_VISION_BASE` | (空) | str | 视觉模型 Base URL（OpenAI 兼容，官方可省） |
| `HASHMM_VISION_KEY` | (空) | secret | 视觉模型 API Key（与 MODEL 同时配置才启用） |
| `HASHMM_VISION_MODEL` | (空) | str | 视觉模型名（如接 Codex / qwen-vl 时填） |

未配置时所有视觉入口安静短路（问答带截图会在轨迹里提示「未配置视觉模型，本轮按文字回答」）。配置后：问答栏截屏/图片附件做按用户问题的定向解读注入上下文；/api/upload 的图片泛分析也优先走该模型。

## 联网搜索

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_SERPER_API_KEY` | (空) | secret | Serper.dev API Key |
| `HASHMM_BING_API_KEY` | (空) | secret | Bing Search API Key |
| `HASHMM_TAVILY_API_KEY` | (空) | secret | Tavily API Key |

## 缓存

| 开关 | 默认 | 类型 | 说明 |
|---|---|---|---|
| `HASHMM_REDIS_HOST` | (空) | str | Redis 主机（空=内存缓存） |