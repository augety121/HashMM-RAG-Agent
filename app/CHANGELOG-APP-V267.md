# App V267 —— 新增「测试中枢」原生屏（对标桌面端）

## 为什么
桌面端有测试中枢（SelfTestView：分组勾选 → 一键自测），App 一直**没有**这个入口——
这是双端对标的真实缺口。本版补齐：手机上也能勾选要测的功能、一键跑全项目自测。

## 做了什么（5 处接线，全部照抄项目既有模式）
1. **数据层** `AdminToolsRepository`：新增 `selftestSuites()`（GET /api/selftest/suites，
   套件清单）与 `selftestRun(ids)`（POST /api/selftest/run，只跑勾选项）。HTTP/鉴权/错误话术
   全部复用本文件既有的 getJson/POST/errFor 模式；解析容错、失败不造数。
2. **ViewModel** `AdminToolsViewModel`：两个透传（与 quality()/audit() 同款）。
3. **新屏** `SelfTestScreen.kt`：ModuleHeader + 下拉刷新 + HmmStateView 三态（加载/错误/空），
   按**分组**展示套件、行内 Checkbox 勾选、组头一键全组/取消全组，默认勾选全部快套件；
   「运行选中的 N 项」按钮 → 逐项显示 通过(耗时)/失败/跳过 + 失败详情；顶部 通过/失败/跳过 三格统计。
   慢套件标注「慢·管理员」（后端限管理员执行，普通账号显示跳过原因，不造数）。
4. **工作台入口**：`WorkbenchHubScreen` 质量看板旁新增「测试中枢」卡（NetworkCheck 图标——
   该屏未占用、语义贴合；延续 V266 的"每卡图标唯一"原则）。
5. **导航**：Routes.SELFTEST + MainScaffold 透传 + HashMMApp composable，三处接线与
   QUALITY 完全同构。

## 自查口径（无法在本环境编译 APK，以下为已做的静态核验）
- 7 个涉改 Kotlin 文件括号（含方括号）**逐字符扫描配平**（跳过注释与字符串）全过；
- 新屏引用的每个组件（ModuleHeader/StatTriple/KitGroup/KitInsetDivider/MetricRow/KitTone/
  HmmStateView/HmmStateKind/CircularProgressIndicator/PullToRefreshBox）逐一核对**在项目中真实
  存在且签名匹配**；Checkbox 为 Material3 标准组件；
- 与后端契约核对：/suites 返回 {suites:[{id,name,group,slow}]}、/run 返回
  {results:[{id,name,group,ok,skip,detail,ms}], summary:{pass,fail,skip}}，字段一一对齐。

版本 81/1.10.40 → **82/1.10.41**。请用本包重新构建 APK。
搭配后端 V278（测试中枢 30 套件）使用；老后端会提示"后端过旧，unzip -o 新包并重启"。
