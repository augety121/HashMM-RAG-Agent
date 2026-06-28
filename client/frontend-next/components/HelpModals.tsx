"use client";
import { useState } from "react";
import { useStore } from "@/lib/store";
import {
  X, MessageSquare, Code2, FileText, Database, Zap, Search,
  Upload, FolderOpen, Keyboard, ChevronRight, ExternalLink, CheckCircle2,
} from "lucide-react";

const HELP_SECTIONS = [
  {
    title: "开始使用",
    items: [
      { icon: MessageSquare, title: "发送消息", desc: "在底部输入框输入问题，按 Enter 发送。支持多轮对话，AI 会记住上下文。" },
      { icon: Upload, title: "上传文件", desc: "点击输入框下方的回形针按钮或直接拖拽文件到聊天区域。支持 PDF、Word、Excel、图片、代码文件等。" },
      { icon: Search, title: "知识库检索", desc: "AI 会自动搜索已索引的知识库文档来回答问题。你也可以点击输入栏上方的「知识库」标签强制使用知识库。" },
    ],
  },
  {
    title: "核心功能",
    items: [
      { icon: Code2, title: "代码生成与执行", desc: "要求 AI 写代码时，它会自动创建文件并可以在沙箱中运行 Python 代码。支持自动安装依赖包。" },
      { icon: FileText, title: "文档生成", desc: "支持生成 PPT（6种主题）、Word 文档（带封面目录）、Excel 表格。生成后可直接下载。" },
      { icon: Database, title: "数据分析", desc: "上传 CSV/Excel 文件后，AI 可以分析数据、生成统计图表、输出分析报告。" },
      { icon: FolderOpen, title: "文件管理", desc: "每个对话有独立的工作区，AI 创建的文件都在这里。点击消息中的文件卡片可以预览和下载。" },
    ],
  },
  {
    title: "快捷操作",
    items: [
      { icon: Keyboard, title: "快捷键", desc: "Ctrl+N 新建对话 · Ctrl+K 命令面板 · Ctrl+V 粘贴文件 · Escape 停止生成" },
      { icon: Zap, title: "模板", desc: "欢迎页提供了常用模板（代码生成、PPT制作、数据分析等），点击即可快速开始。" },
    ],
  },
];

export function HelpModal() {
  const set = useStore((s) => s.set);
  const [expandedSection, setExpandedSection] = useState<number>(0);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) set({ helpOpen: false }); }}
    >
      <div
        className="w-[560px] max-h-[80vh] flex flex-col rounded-2xl anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
      >
        <div className="flex items-center justify-between px-6 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>帮助中心</h3>
          <button onClick={() => set({ helpOpen: false })} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-5">
          {HELP_SECTIONS.map((section, si) => (
            <div key={section.title}>
              <button
                onClick={() => setExpandedSection(expandedSection === si ? -1 : si)}
                className="flex items-center gap-2 w-full text-left mb-2"
              >
                <ChevronRight
                  size={14}
                  className={`transition-transform ${expandedSection === si ? "rotate-90" : ""}`}
                  style={{ color: "var(--text-tertiary)" }}
                />
                <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
                  {section.title}
                </span>
              </button>
              {expandedSection === si && (
                <div className="space-y-2.5 ml-5 anim-fade-up">
                  {section.items.map((item) => (
                    <div key={item.title} className="flex gap-3 p-3 rounded-xl" style={{ background: "var(--bg-secondary)" }}>
                      <item.icon size={18} className="flex-shrink-0 mt-0.5" style={{ color: "var(--accent)" }} />
                      <div>
                        <div className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>{item.title}</div>
                        <div className="text-[12px] leading-relaxed mt-0.5" style={{ color: "var(--text-tertiary)" }}>{item.desc}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="px-6 py-3 text-center flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
          <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
            HashMM-RAG v29 · 有问题？在对话中直接问 AI 即可
          </p>
        </div>
      </div>
    </div>
  );
}

export function ReleaseNotesModal() {
  const set = useStore((s) => s.set);

  const releases = [
    {
      version: "v29.0", date: "2025-05",
      changes: [
        "全新设置页面（9个tab，对标 ChatGPT）",
        "个人资料独立弹窗（头像颜色选择）",
        "升级套餐页面",
        "服务条款 + 隐私政策页面",
        "Agent 自动错误恢复（NameError/ModuleNotFound 自动修复）",
        "Agent 任务完成后质量反思",
        "代码块增强（行号、运行按钮、折叠）",
        "思考过程折叠面板",
        "Diff 展示组件",
        "DSML/XML 标签过滤",
        "TypeScript 零 any",
        "server.py 路由提取（-1067行）",
        "AdminPanel 拆分为 7 个独立组件",
        "数据库连接池",
        "LLM streaming function calling 支持",
      ],
    },
    {
      version: "v28.0", date: "2025-04",
      changes: [
        "SmartAgent 统一引擎",
        "多 Agent 代码审查",
        "PPT 6 主题 11 布局",
        "工具调用卡片展示",
        "暗色模式",
        "知识库 FAISS+BGE-M3 检索",
      ],
    },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) set({ releaseNotesOpen: false }); }}
    >
      <div
        className="w-[520px] max-h-[80vh] flex flex-col rounded-2xl anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
      >
        <div className="flex items-center justify-between px-6 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>发行说明</h3>
          <button onClick={() => set({ releaseNotesOpen: false })} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-6">
          {releases.map((r) => (
            <div key={r.version}>
              <div className="flex items-center gap-3 mb-3">
                <span className="text-[14px] font-bold" style={{ color: "var(--text-primary)" }}>{r.version}</span>
                <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{r.date}</span>
              </div>
              <ul className="space-y-1.5 ml-1">
                {r.changes.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-[12.5px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                    <span className="mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: "var(--accent)" }} />
                    {c}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function BugReportModal() {
  const set = useStore((s) => s.set);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [submitted, setSubmitted] = useState(false);

  function submit() {
    if (!title.trim()) return;
    // Save to localStorage as a simple bug report log
    const reports = JSON.parse(localStorage.getItem("hmm_bug_reports") || "[]");
    reports.push({ title, desc, ts: Date.now() });
    localStorage.setItem("hmm_bug_reports", JSON.stringify(reports));
    setSubmitted(true);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) set({ bugReportOpen: false }); }}
    >
      <div
        className="w-[480px] rounded-2xl overflow-hidden anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
      >
        <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
          <h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>报告错误</h3>
          <button onClick={() => set({ bugReportOpen: false })} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        {submitted ? (
          <div className="px-6 py-10 text-center">
            <div className="mb-3 flex justify-center"><CheckCircle2 size={36} style={{ color: "#16a34a" }} /></div>
            <div className="text-[14px] font-medium" style={{ color: "var(--text-primary)" }}>感谢反馈！</div>
            <div className="text-[12px] mt-1" style={{ color: "var(--text-tertiary)" }}>我们会尽快处理</div>
            <button onClick={() => set({ bugReportOpen: false })}
              className="mt-4 px-5 py-2 rounded-xl text-[13px] font-medium text-white" style={{ background: "var(--accent)" }}>
              关闭
            </button>
          </div>
        ) : (
          <div className="px-6 py-4 space-y-3">
            <div>
              <label className="block text-[12px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>问题标题 *</label>
              <input value={title} onChange={(e) => setTitle(e.target.value)}
                placeholder="简要描述你遇到的问题"
                className="w-full h-10 px-3 rounded-xl text-[13px] outline-none"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
            <div>
              <label className="block text-[12px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>详细描述</label>
              <textarea value={desc} onChange={(e) => setDesc(e.target.value)}
                placeholder="复现步骤、截图说明、期望行为..."
                rows={5} className="w-full px-3 py-2.5 rounded-xl text-[13px] outline-none resize-none leading-relaxed"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => set({ bugReportOpen: false })}
                className="px-4 py-2 rounded-xl text-[13px]" style={{ color: "var(--text-secondary)" }}>取消</button>
              <button onClick={submit} disabled={!title.trim()}
                className="px-5 py-2 rounded-xl text-[13px] font-medium text-white disabled:opacity-50"
                style={{ background: "var(--accent)" }}>提交</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
