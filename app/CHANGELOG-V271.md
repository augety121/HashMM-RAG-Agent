# CHANGELOG V271 (App 1.10.45) — App 自己的功能测试（不是搬桌面端的）

> 你说得对："app 里面的测试应该是 app 自己的测试，和桌面端不一样。" 之前那些跨端契约压测是在
> **后端/桌面端**测的。这版给 App 补上**测 App 自身 Kotlin 逻辑**的单元测试：聊天渲染、消息列表
> 重构、时间显示、后台流状态机——全是 App 端独有的东西，`./gradlew test` 直接跑。

---

## App 自己的测试（30 用例，4 个测试类）

| 测试类 | 测的 App 功能 | 用例 |
| --- | --- | --- |
| `MarkdownParseTest` | **聊天气泡的 Markdown 渲染器** `parseMarkdown`：标题多级 / 项目符号+缩进 / 有序列表 / 代码块(内容+语言+**块内不解析 md**) / 表格头+行 / 引用 / 分隔线 / 软换行合并成段 / 混合文档顺序 / 空输入 | 10 |
| `ChatMessageOpsTest` | **消息列表重构** `ChatMessageOps`（App 特有的"乐观占位→规范覆盖"两段式流）：相邻同角色同内容去重、不同角色不去重、空占位保留、非相邻不去重、`hasStreaming` 判据、`reconcile` **保留服务端还没落库的流式尾巴不闪没** | 9 |
| `ChatFormatTest` | **会话列表时间显示**：今天/昨天/周几/同年 MM-dd/跨年 yyyy-MM-dd/空/非法回退 | 7 |
| `LiveChatManagerTest` | **后台流状态机**：内容单调累积、后台 Deferred 不随调用方取消而取消、空流语义 | 4 |

## 为可测性做的最小重构

- 新增 `ui/chat/ChatMessageOps.kt`（纯函数对象）：把原来埋在 `ChatDetailViewModel` 里的 private
  `dedupeAdjacent` 抽出来，并补 `hasStreaming` / `reconcile`；ViewModel 改为委托调用。逻辑单一
  来源，既复用又可单测。**行为完全不变**。
- `MarkdownParseTest` 直接测既有的 `parseMarkdown`（未改动渲染器一行代码）。

## 版本

`versionCode 85 → 86`，`versionName 1.10.44 → 1.10.45`。

## 变更清单

**新增**：`app/src/main/java/com/hashmm/app/ui/chat/ChatMessageOps.kt` ·
`app/src/test/java/com/hashmm/app/ui/chat/ChatMessageOpsTest.kt` ·
`app/src/test/java/com/hashmm/app/ui/chat/MarkdownParseTest.kt` · 本文件

**修改**：`app/src/main/java/com/hashmm/app/ui/chat/ChatDetailViewModel.kt`（dedupeAdjacent 委托 ops）·
`app/build.gradle.kts`（版本号）

## 验证记录（本机真跑）

- `ChatMessageOps.kt + ChatMessageOpsTest.kt` 经 kotlinc 2.0.21 + JUnit/ChatMessage stub 编译
  **0 错误**；`parseMarkdown` 抽出独立编译并**跑真验证 9/9 通过**（标题/列表/代码块块内不解析/
  表格/引用/软换行/空输入等实际输出核对）。
- 全部 4 个测试类共 **30 个 @Test**，均只依赖 JUnit4 +（后台流用例）coroutines-core，
  `./gradlew test` 可直接运行（junit 依赖已在 V270 配好）。
