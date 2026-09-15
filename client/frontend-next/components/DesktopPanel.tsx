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
import { desktopParentView } from "@/lib/desktopNavigation";
import { ArrowLeft, ChevronRight, Loader2, Radio } from "lucide-react";
import dynamic from "next/dynamic";
import { ErrorBoundary } from "./ErrorBoundary";
import { WorkspaceHubView } from "./desktop/WorkspaceHubView";

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
const CollabView = dynamic(() => import("./desktop/CollabView").then(m => ({ default: m.CollabView })), { ssr: false, loading: panelLoading });
const ScheduledView = dynamic(() => import("./desktop/ScheduledView").then(m => ({ default: m.ScheduledView })), { ssr: false, loading: panelLoading });
const ModelRoutingView = dynamic(() => import("./desktop/ModelRoutingView").then(m => ({ default: m.ModelRoutingView })), { ssr: false, loading: panelLoading });
const RunsView = dynamic(() => import("./desktop/RunsView").then(m => ({ default: m.RunsView })), { ssr: false, loading: panelLoading });
const DiscoveryView = dynamic(() => import("./desktop/DiscoveryView").then(m => ({ default: m.DiscoveryView })), { ssr: false, loading: panelLoading });
const AdvancedView = dynamic(() => import("./desktop/AdvancedView").then(m => ({ default: m.AdvancedView })), { ssr: false, loading: panelLoading });
const AgentsStudioView = dynamic(() => import("./desktop/AgentsStudioView").then(m => ({ default: m.AgentsStudioView })), { ssr: false, loading: panelLoading });
const DocStudioView = dynamic(() => import("./desktop/DocStudioView").then(m => ({ default: m.DocStudioView })), { ssr: false, loading: panelLoading });
const SelfTestView = dynamic(() => import("./desktop/SelfTestView").then(m => ({ default: m.SelfTestView })), { ssr: false, loading: panelLoading });
const BrowserView = dynamic(() => import("./desktop/BrowserView").then(m => ({ default: m.BrowserView })), { ssr: false, loading: panelLoading });
const WorkCanvasView = dynamic(() => import("./desktop/WorkCanvasView").then(m => ({ default: m.WorkCanvasView })), { ssr: false, loading: panelLoading });
const WorkspaceShellView = dynamic(() => import("./desktop/WorkspaceShellView").then(m => ({ default: m.WorkspaceShellView })), { ssr: false, loading: panelLoading });
const PluginCenterView = dynamic(() => import("./desktop/PluginCenterView").then(m => ({ default: m.PluginCenterView })), { ssr: false, loading: panelLoading });
const PersonalHomeView = dynamic(() => import("./desktop/PersonalHomeView").then(m => ({ default: m.PersonalHomeView })), { ssr: false, loading: panelLoading });
const CanvasHomeView = dynamic(() => import("./desktop/CanvasHomeView").then(m => ({ default: m.CanvasHomeView })), { ssr: false, loading: panelLoading });

const TITLES: Record<string, string> = {
  personal: "我的", "work-active": "今天", "work-results": "成果", "work-detail": "工作画布",
  "hub-knowledge": "资料库", "hub-agents": "复杂任务", "hub-operations": "任务与进度", "hub-device": "电脑与连接",
  gworkspace: "项目", "canvas-home": "画布", plugins: "插件", agents: "智能体", docstudio: "文档工坊", workbench: "电脑工作台", files: "电脑工作台", terminal: "终端", browser: "共享浏览器", usage: "Agent 用量", backend: "后端连接", remote: "设备接力", memory: "记忆中心", evolution: "自我进化", quality: "质量看板", audit: "权限审计", scheduled: "自动任务", routing: "模型路由", runs: "运行轨迹", discovery: "主动发现", advanced: "高级能力", selftest: "测试中枢",
};

// 每个桌面页都有明确的父级。这样进入“高级能力/协作/运行轨迹”等页面后，
// 顶部返回只退回所属工作区，不会直接把用户送回 Chat。
export function DesktopPanel() {
  const view = useStore(s => (s as any).desktopView) as string | null;
  const workDetailParent = useStore(s => s.workDetailParent);
  const taskRunning = useStore(s => Boolean(s.streamingConvId || s.loading));
  if (!view) return null;
  const parent = view === "work-detail" ? workDetailParent : desktopParentView(view);
  const goBack = () => useStore.getState().set({ desktopView: (parent || null) as any });
  return (
    <div className="flex-1 min-w-0 min-h-0 h-full flex flex-col" style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      <div className="h-[52px] flex items-center gap-2 px-5 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-primary)" }}>
        <button onClick={goBack}
          className="flex items-center gap-1.5 px-2 py-1.5 -ml-2 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]"
          style={{ color: "var(--text-secondary)" }} title={parent ? `返回${TITLES[parent] || parent}` : "返回对话"}>
          <ArrowLeft size={14} /> {parent ? (TITLES[parent] || parent) : "对话"}
        </button>
        <ChevronRight size={14} style={{ color: "var(--text-tertiary)" }} />
        <span className="text-[13px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{TITLES[view] || view}</span>
        {taskRunning && (
          <span className="ml-auto inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-[10.5px]"
            style={{ color: "var(--accent)", background: "var(--accent-light)" }} title="当前 Chat 仍有任务运行">
            <Radio size={11} className="animate-pulse" /> Chat 任务运行中
          </span>
        )}
      </div>
      <ErrorBoundary key={view}>
        {view === "personal" && <PersonalHomeView />}
        {view === "work-active" && <WorkspaceShellView section="today" />}
        {view === "work-results" && <WorkspaceShellView section="results" />}
        {view === "work-detail" && <WorkCanvasView />}
        {view === "hub-knowledge" && <WorkspaceShellView section="library" />}
        {view === "hub-agents" && <WorkspaceHubView hub="hub-agents" />}
        {view === "hub-operations" && <WorkspaceHubView hub="hub-operations" />}
        {view === "hub-device" && <WorkspaceHubView hub="hub-device" />}
        {(view === "workbench" || view === "files") && <WorkbenchView />}
        {view === "terminal" && <TerminalView />}
        {view === "browser" && <BrowserView />}
        {view === "usage" && <UsageView />}
        {view === "backend" && <BackendView />}
        {view === "remote" && <RemoteView />}
        {view === "memory" && <MemoryView />}
        {view === "evolution" && <EvolutionView />}
        {view === "quality" && <QualityView />}
        {view === "audit" && <AuditView />}
        {view === "collab" && <CollabView />}
        {view === "scheduled" && <ScheduledView />}
        {view === "routing" && <ModelRoutingView />}
        {view === "runs" && <RunsView />}
        {view === "discovery" && <DiscoveryView />}
        {view === "advanced" && <AdvancedView />}
        {view === "gworkspace" && <WorkspaceShellView section="projects" />}
        {view === "canvas-home" && <CanvasHomeView />}
        {view === "plugins" && <PluginCenterView />}
        {view === "agents" && <AgentsStudioView />}
        {view === "docstudio" && <DocStudioView />}
        {view === "selftest" && <SelfTestView />}
      </ErrorBoundary>
    </div>
  );
}
