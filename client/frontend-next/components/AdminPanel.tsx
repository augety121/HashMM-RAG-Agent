"use client";
/** components/AdminPanel.tsx — 管理后台（V103.35 重做：纵向分组导航 + 内容区，对标大厂 settings）。 */
import { useStore } from "@/lib/store";
import { X, Users, Cpu, Database, FileText, Star, Shield, HardDrive, Network, Zap, Wrench, Award, Search, Loader2, MessageSquare, Clock } from "lucide-react";
import dynamic from "next/dynamic";
import { ErrorBoundary } from "./ErrorBoundary";

// 管理后台各 tab 加载占位
const tabLoading = () => (
  <div className="flex items-center justify-center py-16" style={{ color: "var(--text-tertiary)" }}>
    <Loader2 className="animate-spin" size={20} />
  </div>
);
// 代码分割：管理后台各 tab 按需加载（之前 11 个 tab 全量静态导入，进后台就全下载）
const UsersTab = dynamic(() => import("./admin/UsersTab").then(m => ({ default: m.UsersTab })), { ssr: false, loading: tabLoading });
const ModelsTab = dynamic(() => import("./admin/ModelsTab").then(m => ({ default: m.ModelsTab })), { ssr: false, loading: tabLoading });
const KBsTab = dynamic(() => import("./admin/KBsTab").then(m => ({ default: m.KBsTab })), { ssr: false, loading: tabLoading });
const LogsTab = dynamic(() => import("./admin/LogsTab").then(m => ({ default: m.LogsTab })), { ssr: false, loading: tabLoading });
const DocsTab = dynamic(() => import("./admin/DocsTab").then(m => ({ default: m.DocsTab })), { ssr: false, loading: tabLoading });
const ValidityTab = dynamic(() => import("./admin/ValidityTab").then(m => ({ default: m.ValidityTab })), { ssr: false, loading: tabLoading });
const SkillsTab = dynamic(() => import("./admin/SkillsTab").then(m => ({ default: m.SkillsTab })), { ssr: false, loading: tabLoading });
const KGTab = dynamic(() => import("./admin/KGTab").then(m => ({ default: m.KGTab })), { ssr: false, loading: tabLoading });
const TemplatesTab = dynamic(() => import("./admin/TemplatesTab").then(m => ({ default: m.TemplatesTab })), { ssr: false, loading: tabLoading });
const ToolsTab = dynamic(() => import("./admin/ToolsTab").then(m => ({ default: m.ToolsTab })), { ssr: false, loading: tabLoading });
const SettingsTab = dynamic(() => import("./admin/SettingsTab").then(m => ({ default: m.SettingsTab })), { ssr: false, loading: tabLoading });
const ChannelsTab = dynamic(() => import("./admin/ChannelsTab").then(m => ({ default: m.ChannelsTab })), { ssr: false, loading: tabLoading });
const EvalPanel = dynamic(() => import("./admin/EvalPanel").then(m => ({ default: m.EvalPanel })), { ssr: false, loading: tabLoading });
import type { LucideIcon } from "lucide-react";

const NAV: { group: string; items: { tab: string; icon: LucideIcon; label: string }[] }[] = [
  { group: "运营", items: [
    { tab: "models", icon: Cpu, label: "模型管理" },
    { tab: "users", icon: Users, label: "用户管理" },
    { tab: "logs", icon: FileText, label: "日志" },
  ] },
  { group: "知识", items: [
    { tab: "kbs", icon: Database, label: "知识库" },
    { tab: "docs", icon: HardDrive, label: "文档管理" },
    { tab: "validity", icon: Clock, label: "失效区" },
    { tab: "kg", icon: Network, label: "知识图谱" },
    { tab: "skills", icon: Star, label: "技能" },
    { tab: "templates", icon: Zap, label: "模板" },
  ] },
  { group: "配置", items: [
    { tab: "tools", icon: Wrench, label: "工具" },
    { tab: "settings", icon: Search, label: "搜索配置" },
    { tab: "channels", icon: MessageSquare, label: "IM 渠道" },
    { tab: "eval", icon: Award, label: "质量评测" },
  ] },
];

const TITLES: Record<string, string> = {
  models: "模型管理", users: "用户管理", logs: "日志", kbs: "知识库", docs: "文档管理",
  kg: "知识图谱", skills: "技能", templates: "模板", tools: "工具", settings: "搜索配置", channels: "IM 渠道（飞书/微信）", eval: "质量评测",
  validity: "失效区 · 文档时效",
};

export function AdminPanel() {
  const { adminTab } = useStore();
  const set = useStore(s => s.set);
  const cur = adminTab || "models";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}
      onClick={e => { if (e.target === e.currentTarget) set({ adminOpen: false }); }}>
      <div className="w-[1040px] max-w-[96vw] h-[88vh] flex flex-col rounded-2xl overflow-hidden anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3.5 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: "var(--accent-light)" }}>
              <Shield size={17} style={{ color: "var(--accent)" }} />
            </div>
            <div>
              <h3 className="text-[15px] font-semibold leading-tight" style={{ color: "var(--text-primary)" }}>管理后台</h3>
              <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>运营 · 知识 · 配置</div>
            </div>
          </div>
          <button onClick={() => set({ adminOpen: false })} aria-label="关闭管理后台" className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]">
            <X size={18} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        {/* Body: 纵向导航 + 内容 */}
        <div className="flex-1 flex min-h-0">
          <div className="w-[190px] flex-shrink-0 overflow-y-auto py-3 px-2.5" style={{ borderRight: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
            {NAV.map(g => (
              <div key={g.group} className="mb-3">
                <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>{g.group}</div>
                {g.items.map(it => {
                  const active = cur === it.tab;
                  const Icon = it.icon;
                  return (
                    <button key={it.tab} onClick={() => set({ adminTab: it.tab as never })}
                      className={`relative w-full flex items-center gap-2.5 pl-3 pr-2.5 py-2 rounded-lg text-[12.5px] transition-all text-left mb-0.5 ${active ? "" : "hover:bg-[var(--bg-tertiary)]"}`}
                      style={active ? { background: "var(--accent-light)" } : undefined}>
                      {active && <span className="absolute left-[3px] top-1/2 -translate-y-1/2 w-[3px] h-[15px] rounded-full" style={{ background: "var(--accent)" }} />}
                      <span style={{ color: active ? "var(--accent)" : "var(--text-tertiary)", display: "inline-flex" }}><Icon size={15} /></span>
                      <span className="flex-1" style={{ color: active ? "var(--accent)" : "var(--text-secondary)", fontWeight: active ? 600 : 400 }}>{it.label}</span>
                    </button>
                  );
                })}
              </div>
            ))}
          </div>

          <div className="flex-1 flex flex-col min-w-0">
            <div className="px-6 py-3 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
              <div className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>{TITLES[cur] || "管理"}</div>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-5 min-w-0">
              <ErrorBoundary key={cur}>
                {cur === "users" && <UsersTab />}
                {cur === "models" && <ModelsTab />}
                {cur === "docs" && <DocsTab />}
                {cur === "validity" && <ValidityTab />}
                {cur === "skills" && <SkillsTab />}
                {cur === "kbs" && <KBsTab />}
                {cur === "kg" && <KGTab />}
                {cur === "templates" && <TemplatesTab />}
                {cur === "tools" && <ToolsTab />}
                {cur === "settings" && <SettingsTab />}
                {cur === "channels" && <ChannelsTab />}
                {cur === "eval" && <EvalPanel />}
                {cur === "logs" && <LogsTab />}
              </ErrorBoundary>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
