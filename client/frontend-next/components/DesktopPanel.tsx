"use client";
/** DesktopPanel — 桌面端面板壳（V98 模块化）。
 *
 * V64 起这是 frontend-next 的一等组件：桌面与 web 同一套代码、同一套 CSS 变量，
 * 仅当 preload 桥存在（桌面端）才会被 Sidebar 入口打开；渲染模式与 AdminPanel
 * 相同（全屏覆盖层）。
 *
 * V98 拆分：原 429 行单文件按视图拆到 components/desktop/——
 *   FileBrowser   可复用浏览列（FilesView 与工作台共用，支持变更点亮）
 *   FilesView     全屏文件浏览 + 预览
 *   TerminalView  内嵌真终端（与工作台共享同一 PTY，复用 + 回放）
 *   WorkbenchView FanBox 式文件+终端同屏联动（V98 新增）
 *   UsageView     Agent 用量
 *   BackendView   后端连接 + 本地语义增强卡（SemanticCard，V98 新增）
 * 本文件只剩装配：标题映射 + 顶栏 + 视图路由。
 */
import { useStore } from "@/lib/store";
import { X, ArrowLeft, Loader2 } from "lucide-react";
import dynamic from "next/dynamic";
import { ErrorBoundary } from "./ErrorBoundary";

// 面板加载占位（懒加载分块下载时短暂显示）
const panelLoading = () => (
  <div className="flex-1 flex items-center justify-center" style={{ color: "var(--text-tertiary)" }}>
    <Loader2 className="animate-spin" size={20} />
  </div>
);

// 代码分割：每个面板按需加载，只有真正打开时才下载对应 JS 分块
// （替代之前的全量静态导入——首屏不再为没打开的面板买单）
const WorkbenchView = dynamic(() => import("./desktop/WorkbenchView").then(m => ({ default: m.WorkbenchView })), { ssr: false, loading: panelLoading });
const TerminalView = dynamic(() => import("./desktop/TerminalView").then(m => ({ default: m.TerminalView })), { ssr: false, loading: panelLoading });
const UsageView = dynamic(() => import("./desktop/UsageView").then(m => ({ default: m.UsageView })), { ssr: false, loading: panelLoading });
const BackendView = dynamic(() => import("./desktop/BackendView").then(m => ({ default: m.BackendView })), { ssr: false, loading: panelLoading });
const RemoteView = dynamic(() => import("./desktop/RemoteView").then(m => ({ default: m.RemoteView })), { ssr: false, loading: panelLoading });
const MemoryView = dynamic(() => import("./desktop/MemoryView").then(m => ({ default: m.MemoryView })), { ssr: false, loading: panelLoading });
const EvolutionView = dynamic(() => import("./desktop/EvolutionView").then(m => ({ default: m.EvolutionView })), { ssr: false, loading: panelLoading });
const QualityView = dynamic(() => import("./desktop/QualityView").then(m => ({ default: m.QualityView })), { ssr: false, loading: panelLoading });
const AuditView = dynamic(() => import("./desktop/AuditView").then(m => ({ default: m.AuditView })), { ssr: false, loading: panelLoading });
const ScheduledView = dynamic(() => import("./desktop/ScheduledView").then(m => ({ default: m.ScheduledView })), { ssr: false, loading: panelLoading });
const ModelRoutingView = dynamic(() => import("./desktop/ModelRoutingView").then(m => ({ default: m.ModelRoutingView })), { ssr: false, loading: panelLoading });
const RunsView = dynamic(() => import("./desktop/RunsView").then(m => ({ default: m.RunsView })), { ssr: false, loading: panelLoading });
const DiscoveryView = dynamic(() => import("./desktop/DiscoveryView").then(m => ({ default: m.DiscoveryView })), { ssr: false, loading: panelLoading });

const TITLES: Record<string, string> = {
  workbench: "工作台", files: "工作台", terminal: "终端", usage: "Agent 用量", backend: "后端连接", remote: "远程桌面", memory: "记忆中心", evolution: "自我进化", quality: "质量看板", audit: "权限审计", scheduled: "定时任务", routing: "模型路由", runs: "运行轨迹", discovery: "主动发现",
};

export function DesktopPanel() {
  const view = useStore(s => (s as any).desktopView) as string | null;
  if (!view) return null;
  return (
    <div className="fixed inset-0 z-50 flex flex-col" style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <div className="flex items-center gap-3 px-4 py-2.5 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <button onClick={() => useStore.getState().set({ desktopView: null } as any)}
          className="flex items-center gap-1 px-2 py-1 -ml-1 rounded-lg text-[12.5px] transition-colors hover:bg-[var(--bg-tertiary)]"
          style={{ color: "var(--text-secondary)" }}>
          <ArrowLeft size={14} /> 返回
        </button>
        <span className="text-[14px] font-bold">{TITLES[view] || view}</span>
        <span className="text-[10.5px] px-2 py-0.5 rounded-full" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>桌面端</span>
        <button onClick={() => useStore.getState().set({ desktopView: null } as any)} aria-label="关闭面板"
          className="ml-auto p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]">
          <X size={16} style={{ color: "var(--text-tertiary)" }} />
        </button>
      </div>
      <ErrorBoundary key={view}>
        {(view === "workbench" || view === "files") && <WorkbenchView />}
        {view === "terminal" && <TerminalView />}
        {view === "usage" && <UsageView />}
        {view === "backend" && <BackendView />}
        {view === "remote" && <RemoteView />}
        {view === "memory" && <MemoryView />}
        {view === "evolution" && <EvolutionView />}
        {view === "quality" && <QualityView />}
        {view === "audit" && <AuditView />}
        {view === "scheduled" && <ScheduledView />}
        {view === "routing" && <ModelRoutingView />}
        {view === "runs" && <RunsView />}
        {view === "discovery" && <DiscoveryView />}
      </ErrorBoundary>
    </div>
  );
}
