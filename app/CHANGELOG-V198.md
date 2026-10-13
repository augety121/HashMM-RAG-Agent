# HashMM App V198 变更说明

基线：V197（该版本存在 11 个编译错误，无法出包）。本轮为构建修复版，两处根因 + 两道防复发门禁。

## 1. 构建报错修复（对应构建输出里的 11 个 Unresolved reference）

- **HashMMApp.kt 的 10 个 `Unresolved reference`（CLIENT_CONN / PERSONAL_INFO / ABOUT / HELP / DATA_LIST，各出现两处）**
  根因：上一轮给 `ui/Routes.kt` 追加五个路由常量的脚本替换因缩进锚点不匹配**静默失效**——文件里只有 `VALIDITY`，五个新常量根本没写进去，而 `HashMMApp.kt` 第 70–74 与 114–128 行已经在引用它们。
  修复：Routes.kt 补齐五个常量（客户端连接 / 个人信息 / 关于 / 帮助 / 数据列表），与 HashMMApp.kt 的路由注册和跳转完全对齐。

- **WorkbenchHubScreen.kt 的 `Unresolved reference 'Description'`（:125）**
  根因：新增"取电脑里的文件"ComputerAction 用了 `Icons.Outlined.Description`，但没有加对应 import。
  修复：补 `import androidx.compose.material.icons.outlined.Description`。

## 2. 防复发门禁（本次打包前已跑，双零）

- **图标导入闭环扫描**：全项目所有 `Icons.Outlined/Filled/AutoMirrored.*` 用法逐一核对 import，missing = 0。
- **路由引用闭环扫描**：全项目所有 `Routes.X` 引用逐一核对 Routes.kt 中的常量/函数定义，missing = 0。

## 版本说明

V197 的功能变更（远程操控虚拟鼠标、拖选状态机、键盘映射、接力回执等）全部保留，本版仅修复编译问题。请用本包（V198）替换手里的 V197 源码包。
