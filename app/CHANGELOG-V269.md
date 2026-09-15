# CHANGELOG V269 (App 1.10.43) — 后台聊天不断流 · 历史日期修复 · 首批单元测试

> 本版直击你点名的两个 App 大问题：① "进聊天发送后切走，回来先空白、过会儿才出现"；
> ② "历史全像今天，看不出哪天的"。并给 App 建立第一批可 `./gradlew test` 跑的 JVM 单元测试。
> 与客户端 V295/V296 的后端改动（生成期 partial 每 1.5s 落库、status 往返、App 后端契约压测）配套。

---

## 1️⃣ 后台聊天不断流（根治"点进去先空白"）

**病根**：聊天流式跑在 `viewModelScope` 里——离开会话页 ViewModel 被清、协程随之取消，SSE 连接
关闭、后端因客户端断连而**中止生成**；回来是全新 ViewModel、本地 streaming 占位已丢，只能等下次
拉取才看到（先空白）。

**解法**（新增 `data/remote/LiveChatManager.kt`，Hilt `@Singleton`）：
- 真正的流式请求放到**应用级作用域**（`SupervisorJob + Dispatchers.IO`，与工程内
  `SupabaseRemoteSignaling` 同款模式）——**不随任何页面销毁而取消**；
- 实时内容按 convId 挂在单例的 `StateFlow<Map<convId, LiveChat>>` 上；
- `ChatDetailViewModel.streamResponse` 改经 `liveManager.stream(...)` 拿**累计内容**回调（原有
  气泡替换逻辑零改动，`acc` 语义兼容保留）；
- 调用方被清时底层 Deferred 照常跑完（CancellationException 只断转发不断流），配合后端"生成期
  partial 每 1.5s 落库"，回到会话页时 `start()` 里既有的 streaming 重连轮询立刻续看到增长中的内容；
- 同会话重复起流自动附着到既有任务（不重发）。

## 2️⃣ 历史日期修复（几天内显示星期，超 7 天显示完整日期）

重写 `ui/chat/ChatFormat.formatChatTime`（会话列表时间列）：
- 今天 → `今天 HH:mm`；昨天 → `昨天 HH:mm`；7 天内 → `周三 HH:mm`；
- 同年更早 → `MM-dd HH:mm`；**跨年 → `yyyy-MM-dd`**。
- 之前无论多早一律 `MM-dd HH:mm`，"看不出是哪天/像都在今天"。解析失败安全回退不变。
- 与客户端 V295 的后端修复（云端回填保留真实 created_at）配套——数据对、显示也对。

## 3️⃣ 首批单元测试（`app/src/test/`，之前 App 没有任何测试）

- `ChatFormatTest`（7 用例）：今天/昨天/周几/同年 MM-dd/跨年 yyyy-MM-dd/空串/非法输入安全回退。
  纯 JVM，`./gradlew test` 直接跑。
- `LiveChatManagerTest`（4 用例）：内容单调累积、CompletableDeferred 不随调用方取消而取消（后台
  存活的核心机制）等行为契约。注：正式跑需 mockk 或把 `ChatLiveRepository` 抽接口（文件头有说明）。

## 4️⃣ 版本

`versionCode 83 → 84`，`versionName 1.10.42 → 1.10.43`。

## 变更清单

**新增**：`app/src/main/java/com/hashmm/app/data/remote/LiveChatManager.kt` ·
`app/src/test/java/com/hashmm/app/ui/chat/ChatFormatTest.kt` ·
`app/src/test/java/com/hashmm/app/data/remote/LiveChatManagerTest.kt` · 本文件

**修改**：`app/src/main/java/com/hashmm/app/ui/chat/ChatFormat.kt`（日期规则重写）·
`app/src/main/java/com/hashmm/app/ui/chat/ChatDetailViewModel.kt`（注入 LiveChatManager +
streamResponse 改经其后台流）· `app/build.gradle.kts`（版本号）

## 验证记录（本机真跑）

- `ChatFormat.kt` 经 kotlinc 2.0.21 独立编译通过；**日期逻辑跑真验证 7/7 通过**（今天/星期/
  06-30 14:30/2023-03-05 等实际输出核对）。
- `LiveChatManager.kt` 经 kotlinc + 签名对齐 stub **完整类型检查通过**（.class 已生成）；其用到的
  每个 coroutines API 均核对存在于工程内已编译代码（`SupervisorJob/async/Deferred/await/
  StateFlow/ConcurrentHashMap/CancellationException` 等逐一 grep 证实）。
- `ChatDetailViewModel` 的调用点与 `LiveChatManager.stream` 签名逐字段核对一致；`acc.isEmpty()`
  兼容语义保留。
