# App V212 变更记录 —— 修复构建错误 + 续迁移页面到设计系统

## 修复构建报错（真机 Gradle 编译）
- **DesignSystem.kt / Skeleton.kt：State 委托缺 getValue import** —— 
  `val x by animateFloatAsState(...)` / `by collectIsPressedAsState()` / `by transition.animateFloat(...)`
  用 `by` 委托需要 `import androidx.compose.runtime.getValue`。这两个新文件用了显式 import 却漏了 getValue，
  导致 "Type 'State<Boolean/Float>' has no method 'getValue', so it cannot serve as a delegate"。已补齐。
- 根因：新文件用显式 import（漏 getValue），既有文件用通配 `runtime.*`（含 getValue）所以不报。
- 已加静态检查脚本防此类错误复发，全 App 82 个文件 State 委托 import 全部核对通过。

## 续迁移页面到设计系统（本轮 4 个）
- **我的（ProfileScreen）**：5 个重复的 `Card(shape/colors/elevation/border)` → HmmCard，
  删除不再用的 Card/CardDefaults import。视觉统一、代码收敛。
- **模型配置（ModelConfigScreen）**：空态 → HmmStateView（图标+标题+说明）。
- **知识图谱（KGScreen）**：空态 → HmmStateView，图谱 Canvas 渲染保持不动。
- **接力（RelayScreen）**：加载 → HmmSkeletonList 骨架；空态 → HmmStateView。

## 迁移进度
- 已迁移：用量 / 记忆中心 / 客户端动态 / 失效区 / 知识库 / 我的 / 模型配置 / 知识图谱 / 接力（9 个）
- 剩余：对话首页、对话详情、工作台、工作台Hub（功能密集，迁头部与状态为主）；
  远程控制页（1449 行）单独评估，只迁外壳不碰远程操作功能。

## 变更文件
- 修复：ui/components/DesignSystem.kt、ui/components/Skeleton.kt（getValue import）
- 迁移：ui/profile/ProfileScreen.kt、ui/models/ModelConfigScreen.kt、ui/kg/KGScreen.kt、ui/relay/RelayScreen.kt
