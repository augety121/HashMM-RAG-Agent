# App V203 变更记录 —— 澄清气泡「待回答」视觉标记（Siri 式收尾）

## 变化
上一版澄清已是对话内气泡，本版给追问气泡加轻量视觉标记，让"Agent 在等你"一眼可辨：
- **左侧品牌色细条**：追问气泡左缘一道 3dp 品牌红竖条（用 IntrinsicSize 撑满气泡高度），
  与普通助手消息区分——扫一眼就知道"这条要我回答"。
- **「等你回答」小胶囊**：当追问是最后一条消息且未在生成时，标题旁出现淡红胶囊；
  用户答完、对话继续后自动消失（不是最后一条就不显示）。
- 标记走既有的 ⟦⟧ 标记体系（`⟦CLARIFY⟧`），与 CONFIRM/TASKS/MEM 同一套解析/剥离逻辑；
  网页端 `render.ts` 同步剥离该标记（跨端同步来的消息不裸奔）。

## 实现
- `ChatBubbles.kt`：`awaiting` 参数三层穿透（MessageBubble→Content→AssistantMessage），
  `isClarify` 检测 + 剥离，左侧色条（Row + IntrinsicSize.Min）+ 头部胶囊。
- `ChatDetailViewModel.kt`：澄清问题持久化/本地兜底两条路都追加 `⟦CLARIFY⟧` 标记。
- `ChatDetailScreen.kt`：调用点把"最后一条且未生成"作为 awaiting 传入。
- 依赖客户端 V210 的 render.ts 剥离（网页端一致）。

## 变更文件
- ui/chat/ChatBubbles.kt / ChatDetailScreen.kt / ChatDetailViewModel.kt
