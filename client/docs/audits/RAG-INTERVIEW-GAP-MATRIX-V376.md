# 面试实战资料到 HashMM RAG 的证据矩阵（V376）

本矩阵用于避免“看到一个术语就再造一套模块”。只有已经接入 Chat/Agent 主链、能由测试或运行证据验证的机制才标为已接入。

| 资料中的关键机制 | 当前主链证据 | 结论 |
|---|---|---|
| 语义/固定分块与重叠 | `hashmm/pipeline/chunker.py` 支持结构块、邻接上下文和固定重叠；`hashmm/retrieval/parent_child.py` 提供父子块 | 已接入索引能力；不再新增平行分块器 |
| 稠密+稀疏混合检索 | `hashmm/retrieval/hybrid_router.py` 双路检索与 RRF；`hashmm/retrieval_pipeline.py` 进入主检索 | 已接入 Chat 主链 |
| 多查询、HyDE 与纠错检索 | `hashmm/chat_retrieval.py`、`hashmm/api/streaming.py` 的多查询/HyDE/CRAG 路径 | 已接入但受功能开关与模型可用性约束 |
| 重排与粗到细 | 主链候选融合后调用 reranker；不可用时保持原融合顺序并记录方法 | 已接入；不把降级宣称为重排成功 |
| 图增强检索 | `hashmm/retrieval/graph_engineering.py`、KG 邻域、社区和证据图 | 已接入检索与任务证据；图扩展不能跨文档越权 |
| 查询缓存 | `hashmm/agent/cache.py` 与 `hashmm/pipeline_cache.py` 的 LRU+TTL | 已接入；瞬态空结果不写入长期缓存 |
| 忠实度与 RAGAS 维度 | `hashmm/evaluation/faithfulness.py`、`deep_eval.py`、质量看板 | 已接入；无 judge/无上下文时保持不可评估而非伪造分数 |
| 长上下文压缩 | 对话压缩、分层记忆和运行上下文注入 | 已接入 Chat/长任务；摘要不是原始证据替代品 |
| 多智能体分工 | 团队预览、并行/流水线、证据图、完成门与用户验收 | 已接入真实运行；角色结果必须回到同一会话 |
| 权限与审计 | 任务执行范围、网络策略、工具白名单、所有者检查和审计记录 | 已接入；子智能体只能缩窄权限 |
| 程序化验收 | completion gate、step evaluation、固定数据集与长时任务测试 | 已接入；模型自述“完成”不作为通过证据 |

## 本轮选择

V376 没有再增加一套 RAG 页面或重复检索器。重点是把既有 RAG、Graph Engineering、Agent 运行、Artifact 和 App 协作串进同一工作记录，并修复产物无法落盘、移动端错误假空态和高频无效轮询。这样新增能力真正服务 Chat 和长任务，而不是继续扩大“有页面但不可用”的表面积。

