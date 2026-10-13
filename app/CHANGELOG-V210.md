# App V210 变更记录 —— V300 大版本第一期：App 设计系统落地

对标方案 V300 第一期。此前 App 47 个 UI 文件仅 2 个共享组件（20:1 失衡），每页各写原始布局，
视觉不统一、迭代慢。本期建起一套 Compose 设计系统，把 App 从"功能堆叠"改造成"组合组件"。

## 1. 设计令牌（ui/theme/DesignTokens.kt）
- AppSpacing：4 倍数间距梯度（xs/sm/md/lg/xl/xxl/page），全 App 页面横向边距统一。
- AppRadius / AppShape：圆角分级（chip 50 / small 10 / card 16 / large 20 / iconBox 11 / bubble）。
- AppMotion：统一动效时长（fast 150 / normal 220 / slow 320）+ 缓动曲线（近 iOS 自然感）。
- AppSize：组件尺寸常量（图标容器 38 / 触控下限 48 / 头像三档）。
- 收敛原来散落各页的魔法数字——页面只引用令牌，不再写原始数值。

## 2. 核心组件库（ui/components/DesignSystem.kt）—— 对齐桌面端 PanelKit
- HmmCard / HmmCardHeader：标准卡片 + 卡片头（图标 + 标题 + 副标题 + 右操作）。
- HmmButton：四态按钮（Primary/Secondary/Ghost/Danger），含 busy 转圈 + 按下缩放微交互。
- HmmStatCard：指标卡（label + 24sp 大数值 + 单位 + 图标 + 色调）。
- HmmSectionTitle：区块标题（左标题 + 右操作）。
- HmmBadge：五色徽章（neutral/accent/success/warning/error）。
- HmmIconBadge：淡染图标方块容器（列表项左侧统一视觉，全 App 一致）。
- HmmListItem：列表项（图标容器 + 标题 + 副标题 + 右侧内容，含禁用态）。
- HmmChip：胶囊标签（可点、可选中态）。
- HmmDivider：分隔线。
- HmmStateView：三态占位（loading 转圈 / empty 插画引导 / error 带重试按钮）——大厂 App 每屏标配。

## 3. 移动端专属（大厂 App 每屏都有，此前 App 缺）
- Skeleton.kt：骨架屏 / Shimmer 微光占位（HmmSkeletonBox / ListItem / Card / List）——
  加载时显示"内容形状的骨架 + 微光扫过"，替代生硬的转圈，感知更快。
- PageScaffold.kt：
  - HmmPageScaffold：页面统一外壳（页眉 + 内容区 + 统一背景/边距）。
  - HmmPullRefresh：下拉刷新（Material3 官方 PullToRefreshBox），每个可刷新列表都能用。
  - HmmScrollContent：可滚动内容列（统一边距 + 令牌间距）。

## 4. 参考实现：用量页重构（ui/usage/UsageScreen.kt）
- 重构前：手写 Surface/Box 卡片、原始 chip、CircularProgressIndicator 转圈、手拼错误态，充斥魔法数字。
- 重构后：只组合设计系统组件——HmmChip 时间窗、HmmSkeletonCard 加载骨架、HmmStatCard 指标、
  HmmStateView 错误态带重试。零原始布局、零魔法数字，视觉自动与全 App 一致。
- 这是其余 App 页面迁移设计系统的模板——后续页面照此逐个迁移。

## 后续（V300 第一期剩余）
- 其余页面逐个迁移到设计系统（对话/工作台/我的/记忆/知识库/远程…）。
- 导航架构（底部 Tab + 栈式）与动效系统的进一步统一。

## 变更文件
- 新增：ui/theme/DesignTokens.kt, ui/components/DesignSystem.kt, ui/components/Skeleton.kt, ui/components/PageScaffold.kt
- 重构：ui/usage/UsageScreen.kt
