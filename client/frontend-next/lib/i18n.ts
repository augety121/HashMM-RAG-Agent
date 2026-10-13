// lib/i18n.ts — 界面多语言引擎（V103.90，frontend-next 真 UI）。
//
// 全应用原本写死中文。retrofit 策略：用「中文原文」本身作 key，词典做 中→英 映射。
//   - locale="zh"：t(原文) 直接返回原文（零成本）
//   - locale="en"：t(原文) 查 EN_DICT，命中返回译文，缺失回退原文（不崩、便于补）
// 这样只需在渲染处把字面量包一层 t(...)，无须重构数据结构；词典逐步补全即可。
//
// t / interpolate 是纯函数（可 tsc 编译后单测）；useT 是绑定 store 当前语言的 React hook。

export type Locale = "zh" | "en";
export const LOCALES: { code: Locale; name: string }[] = [
  { code: "zh", name: "简体中文" },
  { code: "en", name: "English" },
];

// 中文原文 → 英文。只列已翻译的；未列的英文环境回退中文原文。
export const EN_DICT: Record<string, string> = {
  // 侧边栏导航
  "新对话": "New Chat", "工作台": "Workbench", "终端": "Terminal", "本机命令行": "Local shell",
  "文件 · 预览 · 终端": "Files · Preview · Terminal",
  "知识库": "Knowledge Base", "语料 · 迁移": "Corpus · Migration",
  "技能与模板": "Skills & Templates", "提示词资产": "Prompt assets",
  "用量": "Usage", "Token · 成本": "Tokens · Cost",
  "后端连接": "Backend", "本地 / 远程": "Local / Remote",
  "远程": "Remote", "远程桌面 · 配对码": "Remote desktop · Pairing",
  "记忆中心": "Memory", "跨会话偏好": "Cross-session prefs",
  "自我进化": "Self-evolution", "技能 · 经验": "Skills · Experience",
  "质量看板": "Quality", "延迟 · 成本 · 质量": "Latency · Cost · Quality",
  "权限审计": "Audit", "工具 · 治理 · 合规": "Tools · Governance · Compliance",
  "定时任务": "Scheduled", "主动服务 · 计划": "Proactive · Plans",
  "模型路由": "Model Routing", "角色 · 本地/云端": "Roles · Local/Cloud",
  "运行轨迹": "Runs", "遥测 · 回放 · 排障": "Telemetry · Replay · Debug",
  "主动发现": "Discovery", "自找活 · 提前想": "Proactive · Foresight",
  "知识": "Knowledge", "系统": "System", "已固定": "Pinned", "管理员": "Admin",
  "暂无对话记录": "No conversations yet", "搜索对话…": "Search chats…",
  // 聊天区
  "HashMM-RAG": "HashMM-RAG",
  "企业级 RAG + 自我进化 Agent · 对标 Claude + Hermes": "Enterprise RAG + Self-evolving Agent",
  "有什么想问的?": "Ask anything…",
  "在对话中查找…": "Find in conversation…",
  "导出 Markdown": "Export Markdown", "在对话中查找": "Find in conversation",
  "知识检索": "Knowledge", "图谱推理": "Graph", "代码执行": "Code", "文档生成": "Docs", "URL读取": "URL", "自我进化 ": "Self-evolution",
  "支持拖放/粘贴文件 · 自动创建可下载文件 · 支持多轮对话": "Drag & drop / paste files · auto-download · multi-turn",
  "拖放文件到这里上传": "Drop files here to upload",
  "关闭标签": "Close tab", "关闭": "Close",
  // 设置
  "界面语言": "Language", "语言": "Language", "外观": "Appearance", "主题": "Theme", "字号": "Font size",
  "深色": "Dark", "浅色": "Light", "跟随系统": "System",
  "重点色": "Accent", "字体大小": "Font size", "小": "Small", "默认": "Default", "大": "Large",
  "设置": "Settings", "通用": "General", "通知": "Notifications", "账户": "Account", "关于": "About",
};

/** 插值：{name} → params.name；未提供的占位符原样保留。 */
export function interpolate(str: string, params?: Record<string, string | number>): string {
  if (!params) return str;
  return String(str).replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
}

/** 翻译：locale=zh 返回原文；locale=en 查词典命中返回译文、缺失回退原文。 */
export function translate(text: string, locale: Locale, params?: Record<string, string | number>): string {
  const base = locale === "en" ? (EN_DICT[text] ?? text) : text;
  return interpolate(base, params);
}

export function isLocale(x: unknown): x is Locale {
  return x === "zh" || x === "en";
}
