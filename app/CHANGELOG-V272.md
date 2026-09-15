# CHANGELOG V272 (App 1.10.46) — 测试中枢改跑 App 原生测试 · 历史排序与日期 · 点击丝滑

> 三件你反复强调的事，这版全部落地：① App 测试中枢**跑 App 自己的功能测试**（不再调后端那套桌面
> 测试）；② 历史记录**最新在顶** + 日期显示（昨天/前天/星期/年月日）；③ **点击丝滑**不卡顿。

---

## 1️⃣ 测试中枢 → App 原生测试引擎（测 App 自己，不是桌面端）

新增 `ui/workbench/NativeTestEngine.kt`：在**设备本地**直接跑 App 自身逻辑，不再调后端 `/api/selftest`。
`SelfTestScreen` 的数据源从后端换成它（复用同构结果类型，UI 几乎零改）。**12 个套件**：

| 分组 | 套件 | 测什么 |
| --- | --- | --- |
| 渲染·Markdown | md_basic / md_edge | 聊天渲染器 parseMarkdown：标题/列表/代码块/表格/引用 + 未闭合代码块/畸形表格/空输入不崩 |
| 消息·列表重构 | msg_dedupe / msg_reconcile | 相邻同内容去重、空占位保留、**覆盖时保留服务端还没落库的流式尾巴不闪没** |
| 时间·显示与排序 | time_display / time_sort | 今天/昨天/前天/星期/年月日、会话排序键(ISO/epoch/空) |
| 后台·直播状态机 | stream_accum | 内容单调累积、streaming 判据 |
| 缓存·序列化 | cache_json | 消息 JSON 往返不丢字段 |
| **压力测试** | stress_md / stress_dedupe / stress_sort / stress_time | 2万行畸形 Markdown、5万条消息去重、2万会话排序、10万次时间格式化——每项带耗时，超时即判失败 |

每条产出**多行详细日志**（打进结果，UI 展示完整不再截断 72 字）。压力项默认不勾。
本机端到端真跑：**11 PASS / 1 SKIP（cache_json 仅在无 kotlinx.serialization 的构建外环境跳过，装机即跑）**，
压力项 330ms / 67ms / 1458ms / 389ms 全过。

## 2️⃣ 历史记录：最新在顶 + 日期显示

**排序**（根治"最新的在最底下"）：`ChatListViewModel` 对缓存和同步结果都按 `updatedAt` 倒序；
新增 `ChatMessageOps.convSortKey` 把 ISO/epoch/空值统一解析成可比毫秒（updatedAt 空回退 createdAt）——
不再依赖缓存写入顺序，最新会话永远在顶。

**日期显示**（`ChatFormat.formatChatTime` 重写，严格按诉求）：今天 `今天 HH:mm`、昨天 `昨天 HH:mm`、
**前天 `前天 HH:mm`**、3–6 天 `周三 HH:mm`、**≥7 天 `yyyy-MM-dd`（年月日带年）**；另加
`formatChatTimeFull` 供查看完整 `yyyy-MM-dd HH:mm`。本机真跑 7/7（含前天、年月日、完整时间戳）。

## 3️⃣ 点击丝滑：打开会话不再批量动画卡顿

病根：打开会话时历史消息是一整批，每条都淡入+上移 → 首屏 N 条同时动画，明显卡顿。
**修复**：进场动画只给**新消息**——`MessageBubble` 在首次组合时捕获是否该动画，历史批量渲染
tween 0ms 秒显、新追加消息才淡入；`ChatDetailScreen` 用 `loadedOnce` 标志区分首批与后续。
点进会话瞬间见内容、丝滑。（Markdown 解析早已 `remember(content)` 缓存、消息列表早有 key，本版补上
动画这一环。）

## 版本

`versionCode 86 → 87`，`versionName 1.10.45 → 1.10.46`。

## 变更清单

**新增**：`app/src/main/java/com/hashmm/app/ui/workbench/NativeTestEngine.kt` · 本文件

**修改**：`app/src/main/java/com/hashmm/app/ui/workbench/SelfTestScreen.kt`（改跑原生引擎 + 详细日志）·
`ui/chat/ChatFormat.kt`（前天/年月日/完整时间戳）· `ui/chat/ChatMessageOps.kt`（convSortKey）·
`ui/chat/ChatListViewModel.kt`（最新在顶排序）· `ui/chat/ChatBubbles.kt`（动画只给新消息）·
`ui/chat/ChatDetailScreen.kt`（loadedOnce）· 测试：`ChatFormatTest`（前天/年月日）+
`ChatMessageOpsTest`（convSortKey）· `app/build.gradle.kts`（版本号）

## 验证记录（本机真跑，kotlinc 2.0.21）

- **NativeTestEngine 端到端真跑 11 PASS / 1 SKIP**（压力项全过：2万行畸形md 330ms、5万去重 67ms、
  2万排序 1458ms、10万格式化 389ms）——用 stdlib startCoroutine 驱动真实 run()，非模拟。
- `ChatFormat` 日期逻辑真跑 **7/7**（今天/昨天/前天/星期/年月日/跨年/完整时间戳）。
- `ChatMessageOps + ChatFormat + 两个测试类` kotlinc 编译 **0 错误**（含 convSortKey 5 例、前天/年月日断言）。
- SelfTestScreen 括号平衡、无残留 viewModel/hiltViewModel、关键调用点齐全（结构自检通过）。
