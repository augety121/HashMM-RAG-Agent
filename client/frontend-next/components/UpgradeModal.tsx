"use client";
import { useStore } from "@/lib/store";
import { X, Sparkles, Zap, Check, Database, Code2, FileText, Brain } from "lucide-react";

const FREE_FEATURES = [
  { icon: Sparkles, text: "核心模型" },
  { icon: Zap, text: "有限额度的消息发送和文件上传" },
  { icon: Code2, text: "有限的代码执行功能" },
  { icon: Database, text: "有限的知识库检索" },
];

const PRO_FEATURES = [
  { icon: Zap, text: "无限制消息发送" },
  { icon: Sparkles, text: "优先使用最新模型" },
  { icon: Code2, text: "无限制代码执行" },
  { icon: Database, text: "完整知识库访问" },
  { icon: FileText, text: "无限制文档生成（PPT/Word/Excel）" },
  { icon: Brain, text: "完整的 Agent 规划能力" },
];

export function UpgradeModal() {
  const set = useStore((s) => s.set);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) set({ upgradeOpen: false }); }}
    >
      <div
        className="w-[720px] max-h-[90vh] overflow-y-auto rounded-2xl anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-8 pt-6">
          <div />
          <h2 className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>升级套餐</h2>
          <button onClick={() => set({ upgradeOpen: false })} className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        {/* Plans */}
        <div className="grid grid-cols-2 gap-5 px-8 py-6">
          {/* Free Plan */}
          <div className="rounded-2xl p-6" style={{ border: "1px solid var(--border)" }}>
            <h3 className="text-base font-semibold mb-1" style={{ color: "var(--text-primary)" }}>免费版</h3>
            <div className="flex items-baseline gap-1 mt-4 mb-2">
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>¥</span>
              <span className="text-4xl font-bold" style={{ color: "var(--text-primary)" }}>0</span>
              <span className="text-[13px]" style={{ color: "var(--text-tertiary)" }}>/月</span>
            </div>
            <p className="text-[13px] mb-5" style={{ color: "var(--text-secondary)" }}>了解 AI 的功能</p>
            <div
              className="w-full py-2.5 rounded-xl text-center text-[13px] font-medium mb-6"
              style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}
            >
              你当前的套餐
            </div>
            <div className="space-y-3">
              {FREE_FEATURES.map((f) => (
                <div key={f.text} className="flex items-center gap-2.5">
                  <f.icon size={16} style={{ color: "var(--text-tertiary)" }} />
                  <span className="text-[13px]" style={{ color: "var(--text-secondary)" }}>{f.text}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Pro Plan */}
          <div
            className="rounded-2xl p-6 relative"
            style={{ border: "2px solid var(--accent)", background: "var(--bg-primary)" }}
          >
            <span
              className="absolute top-4 right-4 text-[10px] font-semibold px-2 py-0.5 rounded-full text-white"
              style={{ background: "var(--accent)" }}
            >
              推荐
            </span>
            <h3 className="text-base font-semibold mb-0.5" style={{ color: "var(--text-primary)" }}>Pro</h3>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>发烧友</p>
            <div className="flex items-baseline gap-1 mt-4 mb-2">
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>¥</span>
              <span className="text-4xl font-bold" style={{ color: "var(--text-primary)" }}>99</span>
              <span className="text-[13px]" style={{ color: "var(--text-tertiary)" }}>/月</span>
            </div>
            <p className="text-[13px] font-medium mb-5" style={{ color: "var(--accent)" }}>有效提升效率</p>
            <button
              className="w-full py-2.5 rounded-xl text-center text-[13px] font-medium text-white mb-6"
              style={{ background: "var(--accent)" }}
              onClick={() => {
                alert("Pro 版本暂未开放购买，敬请期待！");
              }}
            >
              升级至 Pro
            </button>
            <p className="text-[11px] font-medium mb-3" style={{ color: "var(--text-primary)" }}>
              除免费版所有功能外，还包括：
            </p>
            <div className="space-y-3">
              {PRO_FEATURES.map((f) => (
                <div key={f.text} className="flex items-center gap-2.5">
                  <f.icon size={16} style={{ color: "var(--accent)" }} />
                  <span className="text-[13px]" style={{ color: "var(--text-secondary)" }}>{f.text}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-8 pb-6 text-center">
          <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
            自部署版本 · 数据完全自控 ·{" "}
            <a href="/terms" className="underline">服务条款</a>
            {" · "}
            <a href="/privacy" className="underline">隐私政策</a>
          </p>
        </div>
      </div>
    </div>
  );
}
