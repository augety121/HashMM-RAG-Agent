# HashMM App V249 —— 直连模型（Marvis 式离线独立 agent）· 配套桌面 V251

## ⭐ 直连兜底：后端不在线，手机照样是个 agent
- **触发链**：发消息 → 后端流式失败 → 非流兜底失败 → **自动切直连**（若已配置）——
  同一个回答气泡无缝续上流式输出，对话顶部出现米色横幅
  「直连模式 · 后端离线…后端恢复后自动切回」；后端一恢复，下一条自动回主链路。
- **直连仓库** `DirectLlmRepository`：OpenAI 兼容 /chat/completions（stream=true），
  DeepSeek / Moonshot / 通义 / OpenAI 任何兼容端点皆可；带最近 12 条上下文；
  401/404 给人话错误提示。Key 只存本机 DataStore、只发给你自己填的端点。
- **设置屏**「我的 · 直连模型」：米色说明卡 + 三项配置（Base URL / 模型名 / API Key，
  Key 密文显示）+ 墨黑「保存」+「测试连接」（真发一条问 OK）+ 触发时机说明。
- 未配置时的发送失败文案改为可行动：「可在 我的·直连模型 配置离线兜底」。
- 诚实边界：直连轮次不入后端/云端记录（横幅明示），无知识库与工具——它是兜底，
  不是替代后端。

## 配套桌面 V251（孤岛修复）
- App 派 **多智能体 / 多步规划** 不再是黑洞：后端自建会话落画布 →
  动态页「最新产物」即见控制室/任务树 → 点开用 App 原生画布屏看四色直播。

## 改动文件
SettingsStore（direct 三项）· DirectLlmRepository（新）· ChatDetailViewModel（兜底接管）·
ChatDetailScreen（直连横幅）· DirectLlmScreen（新）· ProfileScreen/MainScaffold/
HashMMApp/Routes（入口与导航）。9 文件静态自检全过。
