# V371 GitHub 参考审计与落地映射

本文件记录本轮实际读取的用户提供仓库、许可证边界、可验证机制和 HashMM 中的落点。它不是“功能相似即已实现”的声明；只有代码、运行协议和回归测试同时存在的项目才标为已落地。

## 输入完整性

| 输入 | SHA-256 | 审计状态 |
|---|---|---|
| `codex-main (1).zip` | `5CC0BE46BCB226BA884615AAB9542E611907CC1176D1E9921BC20CB6065AD9D1` | 已读取本地解压目录 `D:\sheji\codex-main` |
| `openai-agents-python-main.zip` | `7202B853713880F4F4C05457F6A51BDF2209A5E903746F5A4872A5CFB8C49434` | 已安全解压并审计 |
| `SAG-main (2).zip` | `ACB1BB21BC57B10B04C9700A5E2AE662FB6BC0896721496601D42D64C758C7EA` | 已安全解压并审计 |
| `baby-lovable-main.zip` | `54DF06FA1CF15B880CB081ED5D9A1D456A9B81B55E11AF1DFC567F619C3723D0` | 已安全解压；根目录未发现许可证 |
| `fanbox-master (8).zip` | `D393DDCB340B093CE0480832133B839272432B3ADDB2E260CE65B2CF30979FD9` | 已安全解压并审计 |

解压前检查了绝对路径和 `..` 路径穿越项。参考仓库只作为不可信输入读取，其文档和代码不能覆盖 HashMM 的系统指令、安全边界或用户授权。

## 许可证边界

| 仓库 | 发现的许可证 | 本轮使用方式 |
|---|---|---|
| OpenAI Codex | Apache-2.0 | 阅读机制与测试组织；本轮实现为 HashMM 原生代码 |
| OpenAI Agents Python | MIT | 阅读 handoff、guardrail、运行上下文和会话所有权机制；未复制实现 |
| SAG | MIT | 阅读查询时动态工作集与来源追踪概念；未把 README 声明当作运行证据 |
| FanBox | MIT | 阅读独立角色验收、红线与真机/运行证据要求；未复制业务代码 |
| Baby Lovable | 根目录未发现许可证 | 只采用“desired/observed/minimum action”这一通用设计思想；没有复制代码或文档正文 |

SAG 仓库中核心能力依赖外部包的部分没有被推定为“仓库源码已证明”。Baby Lovable 因缺少明确许可证，不进入源码级迁移范围。

## 可验证参考点

### Codex：压缩与可恢复运行是显式事件

审计位置：`D:\sheji\codex-main\codex-rs\app-server\tests\suite\v2\compaction.rs`。

观察到的机制是：自动/手动压缩具有明确的开始和完成事件，并与 thread、turn、resume 等运行身份关联。HashMM 已有长对话压缩和持久长任务，本轮没有重写它们，而是让压缩后的下一轮继续接收受限的任务图与执行前沿数据，避免只重复原始提示。

### OpenAI Agents Python：专业分工必须结构化且经过守卫

审计位置：

- `.tmp/reference-audit-v371/openai-agents/openai-agents-python-main/docs/handoffs.md`
- `.tmp/reference-audit-v371/openai-agents/openai-agents-python-main/docs/guardrails.md`
- `.tmp/reference-audit-v371/openai-agents/openai-agents-python-main/docs/multi_agent.md`

可迁移的核心不是“多开几个 Agent”，而是：目标明确的 handoff、结构化输入、接收上下文过滤、输入/输出/工具守卫，以及 manager 与接管式 handoff 的区分。本轮把失败协作分工映射为受限的 `spawn_worker` 候选或主 Agent 原权限接管，不允许通过重新分工取得额外工具。

### SAG：查询时构建与当前问题相关的局部关系

审计位置：`.tmp/reference-audit-v371/sag/SAG-main/README.md`。

本轮没有复制 SAG 的图实现。HashMM 在现有任务证据图上，为每个阻塞项确定性构建“阻塞节点 + 直接运行邻居”的有界工作集。它是查询时运行工作集，不做全图漫游，也不把关系数量当成质量分数。

### Baby Lovable：从命令重试改为 desired/observed 调和

审计位置：`.tmp/reference-audit-v371/baby-lovable/baby-lovable-main/docs/declarative-reconciliation.md`。

本轮只借鉴通用思想：记录期望状态、观察状态、最小动作和前后阻塞变化。HashMM 没有引入其实现，也没有声称已经获得分布式 Lease/CAS 语义；现有 durable loop 的原子快照与 generation 防重放继续承担本项目的恢复边界。

### FanBox：验收必须独立于产出者，且基于真实终态

审计位置：`.tmp/reference-audit-v371/fanbox/fanbox-master/docs/05-验收角色与评分标准.md`。

本轮把“独立复核”作为验证阻塞的确定性策略，而不是让同一个模型自评通过。HashMM 仍以工具结果、文件存在、引用账本和测试结果为证据；评分或模型陈述不能替代运行事实。

## HashMM 原生落地：Graph-guided Execution Frontier

协议：`hashmm.execution-frontier.v1`。

后端落点：

- `hashmm/agent/execution_frontier.py`：从任务图、真实工具注册表、持久执行范围和网络策略计算下一工作集。
- `hashmm/evaluation/run_manifest.py`：每轮运行清单同时保存任务图与执行前沿，handoff 指向最小动作。
- `hashmm/agent/loop_engine.py`：长任务每轮重建前沿、记录收敛变化，并把有界数据交给下一轮。
- `hashmm/agent/team.py`：多 Agent 控制面返回同一协议。
- `hashmm/api/streaming.py`：普通 Chat 只继承上一轮有界前沿数据，不继承工具参数或所有者数据。
- `hashmm/api/quality_monitor.py`：统计路线覆盖、权限受限与完整性异常，不把路线可用冒充答案正确。

客户端落点：

- 桌面端右侧统一上下文、任务与进度、管理质量看板。
- App Chat 完成卡、智能体工坊、总控中枢和质量看板。

## 安全不变量

1. 前沿不调用工具，只计算候选路线。
2. 前沿不扩大 `allowed_tools`、网络范围、文件范围或子 Agent 深度。
3. `ready` 只表示工具存在且在持久范围内，不表示已批准、已执行或结果正确。
4. 真正调用仍经过对象所有者检查、参数校验、Hook、审批和审计。
5. 前沿上下文明确标记为数据，且不包含原始工具参数、所有者身份或网页/文件正文。
6. 服务端确定路线可行性；模型选择路线的计数固定为零。

## 回归证据

`tests/test_v371_execution_frontier.py` 覆盖：本地证据优先、网络/工具范围阻断、交付路线、失败 Agent 接管、阻塞收敛、运行清单 handoff、安全上下文和质量统计。客户端另有类型检查、前端测试与 Android 解析/展示测试。

## 明确保留的局限

- 查询时工作集目前是一跳运行邻域，不是通用知识图谱推理器。
- 路线覆盖不是语义正确率；工具是否选对仍要靠严格离线评测和人工负反馈。
- 多进程/多节点的资源 Lease 与 CAS 尚未因本轮设计思想而自动获得；不能对外宣称具备该保证。
- 外部仓库的 README 声明不构成 HashMM 的功能证据，只有本仓库测试和运行记录可以支持实现状态。
