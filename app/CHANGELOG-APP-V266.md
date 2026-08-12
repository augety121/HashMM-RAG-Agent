# App V266 —— 远程控制回退到 V261 · 工作台图标去重

## ① 远程控制：按要求整段回退到 V261
你明确要求「不要再改，把远程回退到 V261」。本轮把 `RemoteControlScreen.kt`
**整文件用 V261 版本覆盖**（字节一致），V262~V265 那几轮对远程手势的改写全部撤销。
- 回退安全性已核验：远程目录另外三个文件（ViewModel / TextureVideoRenderer / WebRtcHelpers）
  在 V261 与 V265 之间**字节完全相同**，所以覆盖单个 Screen 文件不会有接口不匹配；
- V261 该文件引用的 26 个 `viewModel.*` 函数、`HashMascot` 组件在 V265 代码树中**全部存在**；
- V261 该文件**没有任何 V265 缺失的 import**；上一轮为改写引入的 4 个 import
  （OpenWith / Arrangement / rememberCoroutineScope / launch）随整文件覆盖一并移除，无悬空引用；
- 括号（含方括号）逐字符配平通过。
- 回来的东西：虚拟小鼠标 + 左上箭头 + 左右键按压动画（pressL/pressR）+ `left_click_drag`。

## ② 工作台图标去重（WorkbenchHubScreen）
你反馈工作台功能卡图标重复。审计发现该屏有 8 组重复（Psychology 重复 4 次，另 7 个各 2 次）。
**每组保留最贴切的一个，其余改成语义匹配的新图标**，且新图标全部取自本项目**已验证可解析**的
图标池（零编译风险）：
- 取电脑里的文件 → FileDownload ｜ 总结最近对话 → ChatBubbleOutline ｜ 提炼知识库要点 → FormatQuote
- 运行轨迹 → History ｜ 权限审计 → FactCheck ｜ 质量看板 → MonitorHeart ｜ 高级能力 → AutoAwesome
- 记忆中心 → Memory ｜ 知识图谱 → Share ｜ 失效区 → Archive
改完该屏**每个功能卡图标唯一**；新增 10 个 import 全部齐全、无重复；括号配平通过。

版本 80/1.10.39 → **81/1.10.40**。请用本包重新构建 APK（远程回退与图标改动都在编译进 APK 的代码里）。
