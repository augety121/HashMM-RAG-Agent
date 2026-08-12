# App V211 变更记录 —— V300 第一期续：批量页面迁移设计系统

承接 V210 建立的设计系统，本轮把一批页面从"手写原始布局"迁移到"组合设计系统组件"。
迁移是套模板的机械活，但每页都做了编译级校验（括号平衡 / 图标闭环 / 组件引用存在 / ViewModel 字段匹配）。

## 本轮迁移页面（5 个，含 V210 的用量页）
1. **记忆中心**（MemoryScreen）—— 你特别点名"丑死了"的页面。
   - 手写 Surface 卡 → HmmCard；转圈 → HmmSkeletonList 骨架；空态 ExtraBold 大字 → HmmStateView 三态占位；
   - 加了下拉刷新（HmmPullRefresh）；置信度条作为记忆卡专属细节保留（用令牌尺寸）。
2. **客户端动态**（ActivityScreen）
   - 标题 24sp ExtraBold → AppType.screenTitle；转圈 → HmmSkeletonList；空态吉祥物大字 → HmmStateView；
   - 实时进度卡/任务卡是功能特化组件，保留（进度条是功能必需）。
3. **失效区·文档时效**（ValidityScreen）
   - 转圈 → HmmSkeletonList；空态 → HmmStateView（带图标+引导）。
4. **知识库**（KnowledgeScreen）
   - 转圈 → HmmSkeletonCard + HmmSkeletonList；EmptyKnowledge 手写空态 → HmmStateView 带重试；
   - **修掉一处 34sp 桌面级巨字** → 收到移动端尺度上限 24sp（此前违反字号规范）。
5. **用量**（UsageScreen，V210 已迁移）—— 参考实现。

## 迁移带来的一致性
- 所有加载态统一为骨架屏（不再是生硬转圈）；
- 所有空/错误态统一为 HmmStateView（图标 + 标题 + 说明 + 重试/引导），不再各写各的;
- 巨字号清理：34sp → 24sp，符合移动端字号梯度；
- 清理迁移后产生的未用 import（HashMascot / TextAlign），保持代码整洁。

## 后续（第一期剩余）
- 继续迁移：对话首页 / 工作台 / 我的 / 知识图谱 / 模型配置 / 接力 等页面；
- 远程控制页（1449 行，功能密度高）单独评估，只迁头部与状态、不动功能密集区。

## 变更文件
- ui/memory/MemoryScreen.kt（整页重构）
- ui/activity/ActivityScreen.kt（头部+状态迁移）
- ui/validity/ValidityScreen.kt（状态迁移）
- ui/knowledge/KnowledgeScreen.kt（状态迁移 + 巨字修正）
