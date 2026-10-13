# App V268 —— 新增「上下文透视」原生屏（对标桌面端）

## 是什么
对标桌面端 contextInspect / OpenClaw 的 `/context list`：手机上一眼看清**这轮对话的
system 上下文由哪些块组成**——引导文件各级 / 长期记忆(四类) / 用户画像 / 会话补丁 / 动态块——
每块**字符数 + 内容预览 + 精简建议**。排查"模型为何知道/不知道某事"、控上下文预算全靠它。

## 做了什么（5 处接线，照 V267 测试中枢的同款模式）
1. 数据层 `AdminToolsRepository`：`contextInspect()`（GET /api/context/inspect），
   CtxBlock/CtxInspect 数据类，解析容错、失败给人话不造数。
2. `AdminToolsViewModel` 透传。
3. 新屏 `ContextScreen.kt`：三格统计（组成块/注入总量/在场块，总量超 6 万字符转警示色）+
   建议卡（"健康"绿、超量黄）+ 分组白卡逐块展示（名称 + 预览或说明 + 「N 字 / 未注入」徽记），
   下拉刷新 + 三态（加载/错误/空）。
4. 工作台入口：测试中枢卡下方新增「上下文透视」卡（CenterFocusStrong 图标——项目已在
   4 个文件使用、可解析；该屏图标保持唯一）。
5. 导航：Routes.CONTEXT + MainScaffold 透传 + HashMMApp composable，与 SELFTEST 同构。

## 本轮一次真实失误与修复（如实记录）
给 Repository 追加方法时，一次替换误吞了 selftestRun 的 `catch` 与函数闭括号——
括号配平核验当场抓出，已补回并复验（这正是每步核验存在的意义）。

## 自查口径（无法编译 APK，静态核验如下）
7 个涉改 Kotlin 文件逐字符括号（含方括号）配平全过；新屏引用的每个组件核对真实存在；
与后端 /api/context/inspect 字段契约一一对齐（blocks[].id/name/present/chars/preview/note、
total_chars、tips）。

版本 82/1.10.41 → **83/1.10.42**。请重新构建 APK；搭配后端 V280。
