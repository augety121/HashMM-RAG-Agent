# App V202 变更记录 —— 澄清从 Toast 升级为对话内气泡（Siri 式）

## 体验变化
上一版语音澄清用 Toast 承载：问题一闪而过、原话塞回输入框，像个系统提示。
本版把澄清做成**对话的一部分**：
- 你说「帮我整理一下」→ 你的原话作为用户气泡出现，追问「具体针对哪个文件夹？」作为助手气泡出现；
- 两条都是**真实消息**（走新端点持久化）：跨端同步（桌面端历史也完整）、翻历史不丢；
- 直接说答案（语音或打字都行）——答案自动接上问题，组合成完整意图再决策；
- 落定后派给桌面端的任务文本是「原话（补充：答案）」，任务卡上意图完整透明；
- 最多追问 2 轮，问不清就交给 Agent 自己处理，绝不把用户困在澄清循环里；
- 后端不可达时本地气泡兜底，体验不断。

## 实现
- `ChatLiveRepository`：`postUserMessage` / `postClarifyExchange`（走 user-message / assistant-message 端点）。
- `ChatDetailViewModel`：`send` 变路由（有待回答澄清时打字答案也接编排），`sendOrchestrated` 统一
  处理 dispatch（组合意图 + 持久化用户气泡）/ clarify（持久化交换 + 2 轮上限）/ local；原 Toast 流
  （`_voiceClarify`）与 ChatInput 的 clarify 参数、LaunchedEffect 全部移除。
- 依赖客户端 V209 的 user-message 端点与 intent 修正。

## 变更文件
- ui/chat/ChatDetailViewModel.kt / ChatDetailScreen.kt
- data/remote/ChatLiveRepository.kt
