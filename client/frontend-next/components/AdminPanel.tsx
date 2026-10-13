"use client";
/** 团队与服务设置：纵向分组导航 + 内容区。 */
import { useStore } from "@/lib/store";
import { useEffect, useState } from "react";
import { getMe } from "@/lib/api";
import {
  Activity, ArrowLeft, Award, Bot, Brain, CalendarClock, CircleDollarSign,
  Clock, Cpu, Database, FileText, FlaskConical, Gauge, HardDrive, Loader2,
  MessageSquare, Network, Radar, Route, Search, Server, Shield, ShieldCheck,
  Star, Users, Workflow, Wrench, Zap,
} from "lucide-react";
import dynamic from "next/dynamic";
import { ErrorBoundary } from "./ErrorBoundary";
import { myModels, addMyModel, delMyModel, preferMyModel, myAudit } from "@/lib/api";
import type { MyModel } from "@/lib/api";
import type { AuditLog } from "@/lib/types";
import { resolveAdminSurface, type IdentityProbe } from "@/lib/adminAccess";
import HashMascot from "./HashMascot";
import { Cpu as IcMyModels, ScrollText as IcMyLogs } from "lucide-react";

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
const OverviewTab = dynamic(() => import("./admin/OverviewTab").then(m => ({ default: m.OverviewTab })), { ssr: false, loading: tabLoading });
// 管理员运行控制面直接复用已经接通后端的真实页面。普通用户导航不加载这些分块，
// 既不暴露内部术语，也不把功能复制成一套容易失真的“后台演示页”。
const AgentsStudioView = dynamic(() => import("./desktop/AgentsStudioView").then(m => ({ default: m.AgentsStudioView })), { ssr: false, loading: tabLoading });
const ModelRoutingView = dynamic(() => import("./desktop/ModelRoutingView").then(m => ({ default: m.ModelRoutingView })), { ssr: false, loading: tabLoading });
const AdvancedView = dynamic(() => import("./desktop/AdvancedView").then(m => ({ default: m.AdvancedView })), { ssr: false, loading: tabLoading });
const ScheduledView = dynamic(() => import("./desktop/ScheduledView").then(m => ({ default: m.ScheduledView })), { ssr: false, loading: tabLoading });
const RunsView = dynamic(() => import("./desktop/RunsView").then(m => ({ default: m.RunsView })), { ssr: false, loading: tabLoading });
const QualityView = dynamic(() => import("./desktop/QualityView").then(m => ({ default: m.QualityView })), { ssr: false, loading: tabLoading });
const AuditView = dynamic(() => import("./desktop/AuditView").then(m => ({ default: m.AuditView })), { ssr: false, loading: tabLoading });
const SelfTestView = dynamic(() => import("./desktop/SelfTestView").then(m => ({ default: m.SelfTestView })), { ssr: false, loading: tabLoading });
const BackendView = dynamic(() => import("./desktop/BackendView").then(m => ({ default: m.BackendView })), { ssr: false, loading: tabLoading });
const UsageView = dynamic(() => import("./desktop/UsageView").then(m => ({ default: m.UsageView })), { ssr: false, loading: tabLoading });
const MemoryView = dynamic(() => import("./desktop/MemoryView").then(m => ({ default: m.MemoryView })), { ssr: false, loading: tabLoading });
const EvolutionView = dynamic(() => import("./desktop/EvolutionView").then(m => ({ default: m.EvolutionView })), { ssr: false, loading: tabLoading });
const DiscoveryView = dynamic(() => import("./desktop/DiscoveryView").then(m => ({ default: m.DiscoveryView })), { ssr: false, loading: tabLoading });
const CollabView = dynamic(() => import("./desktop/CollabView").then(m => ({ default: m.CollabView })), { ssr: false, loading: tabLoading });
import type { LucideIcon } from "lucide-react";
const ProviderFabricPanel = dynamic(() => import("./ProviderFabricPanel"), { ssr: false, loading: tabLoading });

const NAV: { group: string; items: { tab: string; icon: LucideIcon; label: string }[] }[] = [
  { group: "控制面", items: [
    { tab: "overview", icon: Gauge, label: "平台总览" },
  ] },
  { group: "团队与服务", items: [
    { tab: "models", icon: Cpu, label: "AI 服务" },
    { tab: "users", icon: Users, label: "成员与角色" },
    { tab: "logs", icon: FileText, label: "安全记录" },
  ] },
  { group: "资料与知识", items: [
    { tab: "kbs", icon: Database, label: "知识库" },
    { tab: "docs", icon: HardDrive, label: "文档管理" },
    { tab: "validity", icon: Clock, label: "过期资料" },
    { tab: "kg", icon: Network, label: "图工程" },
    { tab: "skills", icon: Star, label: "技能" },
    { tab: "templates", icon: Zap, label: "模板" },
  ] },
  { group: "能力设置", items: [
    { tab: "tools", icon: Wrench, label: "可用能力" },
    { tab: "settings", icon: Search, label: "检索设置" },
    { tab: "channels", icon: MessageSquare, label: "消息渠道" },
    { tab: "eval", icon: Award, label: "回答质量" },
  ] },
  { group: "Agent 与自动化", items: [
    { tab: "agents", icon: Bot, label: "Agent 编排" },
    { tab: "routing", icon: Route, label: "模型路由" },
    { tab: "advanced", icon: Workflow, label: "能力与 Hooks" },
    { tab: "evolution", icon: Zap, label: "技能进化" },
    { tab: "discovery", icon: Radar, label: "主动发现" },
    { tab: "collab", icon: Network, label: "跨 Agent 协作" },
  ] },
  { group: "运行与治理", items: [
    { tab: "runs", icon: Activity, label: "运行轨迹" },
    { tab: "scheduled", icon: CalendarClock, label: "计划任务" },
    { tab: "quality", icon: Gauge, label: "质量看板" },
    { tab: "audit", icon: ShieldCheck, label: "权限审计" },
    { tab: "selftest", icon: FlaskConical, label: "测试中枢" },
  ] },
  { group: "系统", items: [
    { tab: "backend", icon: Server, label: "服务连接" },
    { tab: "usage", icon: CircleDollarSign, label: "用量与成本" },
    { tab: "memory", icon: Brain, label: "记忆治理" },
  ] },
];

type ControlDomain = {
  id: string;
  label: string;
  icon: LucideIcon;
  description: string;
  tabs: string[];
};

// Ten stable control domains replace the previous 28 first-level entries.
// Existing panels remain real second-level capabilities; no feature is hidden
// or reimplemented as a decorative dashboard.
const CONTROL_DOMAINS: ControlDomain[] = [
  { id: "control", label: "控制中心", icon: Gauge, description: "版本、服务健康、告警与待处理事项", tabs: ["overview"] },
  { id: "identity", label: "身份与权限", icon: Users, description: "成员、角色与项目身份边界", tabs: ["users"] },
  { id: "models", label: "模型与路由", icon: Cpu, description: "Provider、模型健康、路由与回退", tabs: ["models", "routing"] },
  { id: "agent-runtime", label: "Agent 运行时", icon: Bot, description: "Agent、任务状态、运行轨迹与恢复", tabs: ["agents", "runs"] },
  { id: "tools", label: "工具与集成", icon: Wrench, description: "工具、技能、模板、Hooks 与消息渠道", tabs: ["tools", "skills", "templates", "advanced", "channels"] },
  { id: "knowledge", label: "知识与 RAG", icon: Database, description: "知识库、文档、时效、KG 与记忆", tabs: ["kbs", "docs", "validity", "kg", "memory"] },
  { id: "work", label: "工作与自动化", icon: Workflow, description: "计划任务、主动发现和跨 Agent 协作", tabs: ["scheduled", "discovery", "collab"] },
  { id: "quality", label: "质量与演进", icon: FlaskConical, description: "评测、质量门禁、自测与技能演进", tabs: ["eval", "quality", "selftest", "evolution"] },
  { id: "usage", label: "用量与成本", icon: CircleDollarSign, description: "Token、工具、存储、预算和异常增长", tabs: ["usage"] },
  { id: "security", label: "安全与系统", icon: ShieldCheck, description: "安全记录、权限审计与服务连接", tabs: ["logs", "audit", "backend"] },
];

const LEAF_NAV = new Map(
  NAV.flatMap(group => group.items).map(item => [item.tab, item] as const),
);

const TITLES: Record<string, string> = {
  overview: "平台总览",
  models: "AI 服务", users: "成员与角色", logs: "安全记录", kbs: "知识库", docs: "文档管理",
  kg: "图工程", skills: "技能", templates: "模板", tools: "工具", settings: "搜索配置", channels: "IM 渠道（飞书/微信）", eval: "质量评测",
  validity: "过期资料",
  agents: "Agent 编排", routing: "模型路由", advanced: "能力、凭据与 Hooks", evolution: "技能进化", discovery: "主动发现", collab: "跨 Agent 协作",
  runs: "运行轨迹", scheduled: "计划任务", quality: "质量看板", audit: "权限审计", selftest: "测试中枢",
  backend: "服务连接", usage: "用量与成本", memory: "记忆治理",
};

const ADMIN_DESCRIPTIONS: Record<string, string> = {
  overview: "核对服务、成员、模型、知识与任务的真实运行状态",
  models: "选择 HashMM 使用的模型并检查服务状态", users: "邀请成员、分配角色并查看最近登录", logs: "查看重要操作、授权与执行结果",
  kbs: "维护 RAG 知识库与索引入口", docs: "查看文档解析、切分与入库状态", validity: "处理过期文档与时效策略",
  kg: "维护知识关系，并把查询时关联证据接回 Chat", skills: "管理可复用的技能与提示模板", templates: "管理交付与任务模板",
  tools: "选择 HashMM 可以为用户使用的能力", settings: "调整查找资料和引用来源的方式", channels: "连接飞书、微信等消息入口", eval: "检查回答是否可靠、有依据并值得采用",
  agents: "配置多 Agent 分工、执行方式和协作状态", routing: "配置不同任务的模型选择、回退与成本边界",
  advanced: "管理能力模块、加密凭据、派活、自动触发与执行 Hooks", evolution: "审查技能候选、版本与安装权限",
  discovery: "管理主动发现规则、建议来源和采用边界", collab: "管理组织、跨机协作与 Agent 通信安全",
  runs: "查看长任务的真实事件、工具调用和终止原因", scheduled: "管理后台与定时执行任务",
  quality: "查看延迟、成本、证据和回答质量指标", audit: "核对授权、敏感调用和拒绝记录",
  selftest: "运行真实功能测试并保留可下载报告", backend: "检查当前服务、运行时与连接状态",
  usage: "查看模型和本地工具的实际用量", memory: "审查、纠正和删除进入 Agent 的长期记忆",
};

const OPERATION_TABS = new Set([
  "agents", "routing", "advanced", "evolution", "discovery", "collab",
  "runs", "scheduled", "quality", "audit", "selftest", "backend", "usage", "memory",
]);

export function AdminPanel() {
  const { adminTab } = useStore();
  const set = useStore(s => s.set);
  const cur = adminTab || "overview";
  // V222: 打开即自证身份——role!=admin 时给可行动的横幅（此前 403 会被误当"要重新登录"死循环）
  const [who, setWho] = useState<IdentityProbe>(null);
  // V271: 离线（后端未启动）≠ 身份失效——分开对待。此前统一置 "err"，横幅提示
  // "请退出后重新登录"，正是"后端没启动还让我重新登录"的最后一处文案来源。
  useEffect(() => { getMe().then(setWho).catch((e: unknown) => setWho((e as { offline?: boolean })?.offline ? "offline" : "invalid")); }, []);
  // V270 双形态：普通用户 → 个人控制台（我的模型 / 我的日志 / IM 渠道）；管理员 → 完整后台。
  // V271: 后端离线时用**缓存身份**判定形态（登录时存过 hmm_user），管理员离线也能看到
  // 完整后台骨架（数据接口恢复后自动可用），不再降级/弹横幅要求重新登录。
  const cachedRole = useStore(s => s.user?.role);
  const surface = resolveAdminSurface(who, cachedRole);
  const isUser = surface === "user";
  const isOperational = OPERATION_TABS.has(cur);
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const activeDomain = CONTROL_DOMAINS.find(domain => domain.tabs.includes(cur)) || CONTROL_DOMAINS[0];
  const visibleDomains = CONTROL_DOMAINS.filter(domain => {
    if (!q) return true;
    const leafText = domain.tabs.map(tab => {
      const leaf = LEAF_NAV.get(tab);
      return `${leaf?.label || TITLES[tab] || tab} ${ADMIN_DESCRIPTIONS[tab] || ""}`;
    }).join(" ");
    return `${domain.label} ${domain.description} ${leafText}`.toLowerCase().includes(q);
  });

  return (
    <div className="flex-1 min-w-0 min-h-0 flex flex-col" style={{ background: "var(--bg-primary)" }}>
        {/* Header */}
        <div className="h-16 flex items-center justify-between px-6 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2">
            <button onClick={() => set({ adminOpen: false })} className="p-1.5 mr-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" title="返回对话" aria-label="返回对话">
              <ArrowLeft size={15} style={{ color: "var(--text-secondary)" }} />
            </button>
            <HashMascot size={32} />
            <div>
              <h3 className="text-[16px] font-semibold leading-tight" style={{ color: "var(--text-primary)" }}>
                {isUser ? "我的设置" : surface === "admin" ? "管理后台" : "身份校验"}
              </h3>
              <div className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                {isUser ? "模型、记录与消息渠道" : surface === "admin" ? "团队、知识、Agent 与运行治理" : "正在确认当前账号权限"}
              </div>
            </div>
          </div>
        </div>

        {who === "offline" && (
          <div className="px-6 py-2.5 text-[12px] flex-shrink-0" style={{ background: "#FEF3C7", borderBottom: "1px solid #FDE68A", color: "#92400E" }}>
            后端未连接——以缓存身份（{cachedRole === "admin" ? "管理员" : "用户"}）展示{cachedRole === "admin" ? "完整后台" : "控制台"}；数据接口将在后端恢复后自动可用。<b>这不是登录过期</b>，无需重新登录。
          </div>
        )}
        {who === "invalid" && (
          <div className="px-6 py-2.5 text-[12px] flex-shrink-0" style={{ background: "color-mix(in srgb, var(--error) 10%, transparent)", borderBottom: "1px solid var(--border)", color: "var(--text-primary)" }}>
            当前令牌未通过服务端校验。管理功能不会使用本地缓存角色解锁；返回对话后可从账号菜单重新登录。
          </div>
        )}
        {/* Body: 双形态——普通用户个人控制台 / 管理员完整后台 */}
        {surface === "loading" ? <IdentityState loading /> : surface === "invalid" ? <IdentityState /> : isUser ? <UserConsole /> : (
        <div className="flex-1 flex min-h-0">
          <aside className="w-[236px] flex-shrink-0 flex flex-col min-h-0" style={{ borderRight: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
            <div className="px-3 pt-3 pb-2">
              <label className="h-9 px-2.5 flex items-center gap-2 rounded-lg" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <Search size={14} style={{ color: "var(--text-tertiary)" }} />
                <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索管理功能..."
                  className="flex-1 min-w-0 bg-transparent outline-none text-[12px]" style={{ color: "var(--text-primary)" }} />
              </label>
            </div>
            <nav className="flex-1 min-h-0 overflow-y-auto py-1 px-2.5">
             {visibleDomains.map(domain => {
               const active = activeDomain.id === domain.id;
               const Icon = domain.icon;
               return (
                 <button key={domain.id} onClick={() => set({ adminTab: domain.tabs[0] as never })}
                   className={`relative mb-0.5 w-full rounded-lg py-2 pl-3 pr-2.5 text-left transition-all ${active ? "" : "hover:bg-[var(--bg-tertiary)]"}`}
                   style={active ? { background: "var(--accent-light)" } : undefined}>
                   {active && <span className="absolute left-[3px] top-1/2 h-[18px] w-[3px] -translate-y-1/2 rounded-full" style={{ background: "var(--accent)" }} />}
                   <div className="flex items-center gap-2.5">
                     <Icon size={15} style={{ color: active ? "var(--accent)" : "var(--text-tertiary)" }} />
                     <span className="text-[12.5px] font-medium" style={{ color: active ? "var(--accent)" : "var(--text-secondary)" }}>{domain.label}</span>
                   </div>
                   <div className="mt-0.5 line-clamp-2 pl-[25px] text-[10px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{domain.description}</div>
                 </button>
               );
             })}
            </nav>
          </aside>

          <div className="flex-1 flex flex-col min-w-0 min-h-0">
            <div className="flex-shrink-0 px-6 pt-5 pb-3" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-primary)" }}>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-[18px] font-semibold" style={{ color: "var(--text-primary)" }}>{activeDomain.label}</div>
                  <div className="mt-0.5 text-[11px]" style={{ color: "var(--text-tertiary)" }}>{activeDomain.description}</div>
                </div>
                <div className="rounded-full px-2 py-1 text-[10px]" style={{ background: "var(--bg-secondary)", color: "var(--text-tertiary)", border: "1px solid var(--border)" }}>
                  控制域 · {activeDomain.tabs.length} 项能力
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {activeDomain.tabs.map(tab => {
                  const leaf = LEAF_NAV.get(tab);
                  const active = cur === tab;
                  return <button key={tab} onClick={() => set({ adminTab: tab as never })}
                    className="rounded-lg px-2.5 py-1.5 text-[11.5px] transition-colors"
                    style={{ background: active ? "var(--accent-light)" : "var(--bg-secondary)", color: active ? "var(--accent)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>
                    {leaf?.label || TITLES[tab] || tab}
                  </button>;
                })}
              </div>
            </div>
            {isOperational ? (
              <ErrorBoundary key={cur}><AdminOperation tab={cur} /></ErrorBoundary>
            ) : (
              <>
                <div className="px-8 pt-8 pb-5 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
                  <div className="text-[24px] font-semibold tracking-[-0.02em]" style={{ color: "var(--text-primary)" }}>{TITLES[cur] || "管理"}</div>
                  <div className="text-[12px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>{ADMIN_DESCRIPTIONS[cur] || "成员、资料与 AI 能力"}</div>
                </div>
                <div className="flex-1 overflow-y-auto px-8 py-7 min-w-0">
                  <ErrorBoundary key={cur}>
                    {cur === "users" && <UsersTab />}
                    {cur === "overview" && <OverviewTab />}
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
              </>
            )}
          </div>
        </div>
        )}
    </div>
  );
}

function IdentityState({ loading = false }: { loading?: boolean }) {
  const set = useStore(s => s.set);
  return (
    <div className="flex-1 flex items-center justify-center px-6">
      <div className="max-w-[420px] text-center">
        <div className="w-10 h-10 mx-auto mb-3 rounded-xl flex items-center justify-center" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
          {loading ? <Loader2 size={19} className="animate-spin" /> : <Shield size={19} />}
        </div>
        <div className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>{loading ? "正在确认账号权限" : "无法打开管理功能"}</div>
        <p className="mt-1.5 text-[12px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
          {loading ? "验证完成后只展示当前账号有权使用的控制面。" : "服务端没有接受当前登录状态，缓存中的管理员标记不会被用于授权。"}
        </p>
        {!loading && <button onClick={() => set({ adminOpen: false })} className="mt-4 px-3 py-2 rounded-lg text-[12px]" style={{ background: "var(--accent)", color: "#fff" }}>返回对话</button>}
      </div>
    </div>
  );
}

function AdminOperation({ tab }: { tab: string }) {
  return (
    <div className="flex-1 min-h-0 flex">
      {tab === "agents" && <AgentsStudioView />}
      {tab === "routing" && <ModelRoutingView />}
      {tab === "advanced" && <AdvancedView />}
      {tab === "scheduled" && <ScheduledView />}
      {tab === "runs" && <RunsView />}
      {tab === "quality" && <QualityView />}
      {tab === "audit" && <AuditView />}
      {tab === "selftest" && <SelfTestView />}
      {tab === "backend" && <BackendView />}
      {tab === "usage" && <UsageView />}
      {tab === "memory" && <MemoryView />}
      {tab === "evolution" && <EvolutionView />}
      {tab === "discovery" && <DiscoveryView />}
      {tab === "collab" && <CollabView />}
    </div>
  );
}

/* ── V270 个人控制台（普通用户形态）──────────────────────────────────────
   诉求来源：用户点名“桌面端里用户要能看到模型管理、（自己的）日志、IM 渠道”。
   划分原则（与管理员端解耦）：
   · 我的模型  → /api/models/mine 全套（V261 已有用户级 CRUD/设默认，界面此前只藏在设置深处）
   · 我的日志  → /api/admin/audit/mine（V270 新增，query_audit_logs 按 uid 过滤，越权面为零）
   · IM 渠道   → 直接复用 ChannelsTab：其读写走 /api/channels/config 等用户级端点，
                管理动作后端本就单独 require_admin，普通用户看不到也调不动。 */
function UserConsole() {
  const [tab, setTab] = useState<"models" | "providers" | "logs">("models");
  const ITEMS = [
    { k: "providers" as const, label: "Provider Fabric", icon: Network, desc: "官方 API、Sub2API 与兼容网关" },
    { k: "models" as const, label: "我的模型", icon: IcMyModels, desc: "自己的 API 模型 · 新增/设默认" },
    { k: "logs" as const, label: "我的日志", icon: IcMyLogs, desc: "自己的操作记录" },
  ];
  return (
    <div className="flex-1 flex min-h-0">
      <div className="w-[236px] flex-shrink-0 overflow-y-auto py-4 px-3" style={{ borderRight: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <div className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>我的工作区</div>
        {ITEMS.map(it => {
          const active = tab === it.k; const Icon = it.icon;
          return (
            <button key={it.k} onClick={() => setTab(it.k)}
              className={`relative w-full flex items-center gap-2.5 pl-3 pr-2.5 py-2.5 rounded-lg text-[12.5px] transition-all text-left mb-0.5 ${active ? "" : "hover:bg-[var(--bg-tertiary)]"}`}
              style={active ? { background: "var(--accent-light)" } : undefined}>
              {active && <span className="absolute left-[3px] top-1/2 -translate-y-1/2 w-[3px] h-[15px] rounded-full" style={{ background: "var(--accent)" }} />}
              <span style={{ color: active ? "var(--accent)" : "var(--text-tertiary)", display: "inline-flex" }}><Icon size={15} /></span>
              <span className="flex-1" style={{ color: active ? "var(--accent)" : "var(--text-secondary)", fontWeight: active ? 600 : 400 }}>{it.label}</span>
            </button>
          );
        })}
        <div className="px-2 pt-3 text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
          用量与配额在「桌面 · 用量」页；知识库与系统配置为管理员职能。
        </div>
      </div>
      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-8 pt-8 pb-5 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="text-[24px] font-semibold tracking-[-0.02em]" style={{ color: "var(--text-primary)" }}>{ITEMS.find(i => i.k === tab)?.label}</div>
          <div className="text-[12px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>{ITEMS.find(i => i.k === tab)?.desc}</div>
        </div>
        <div className="flex-1 overflow-y-auto px-8 py-7 min-w-0">
          <ErrorBoundary key={tab}>
            {tab === "models" && <MyModelsPanel />}
            {tab === "providers" && <ProviderFabricPanel />}
            {tab === "logs" && <MyLogsPanel />}
          </ErrorBoundary>
        </div>
      </div>
    </div>
  );
}

function MyModelsPanel() {
  const [list, setList] = useState<MyModel[]>([]);
  const [preferred, setPreferred] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [f, setF] = useState({ name: "", base_url: "", model_name: "", api_key: "" });
  const load = () => {
    myModels().then(r => { setList(r.models || []); setPreferred(r.preferred || ""); setErr(""); })
      .catch(e => setErr((e as Error).message || "加载失败"));
  };
  useEffect(load, []);
  const inCls = "px-3 py-2 rounded-lg text-[12px] outline-none";
  const inStyle = { background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" } as const;
  const submit = async () => {
    if (!f.name.trim() || !f.base_url.trim() || !f.model_name.trim()) { setErr("名称 / Base URL / 模型名为必填"); return; }
    setBusy(true); setErr("");
    try { await addMyModel(f); setF({ name: "", base_url: "", model_name: "", api_key: "" }); load(); }
    catch (e) { setErr((e as Error).message || "添加失败"); }
    finally { setBusy(false); }
  };
  return (
    <div className="max-w-[760px]">
      <div className="text-[12px] mb-4 leading-relaxed" style={{ color: "var(--text-secondary)" }}>
        这里的模型是<b>你自己的</b>（密钥只属于你的账号）：聊天工具条可一键在「系统默认 → 我的模型」间切换；设为默认后你的回答优先用它。
      </div>
      {err && <div className="text-[12px] mb-3" style={{ color: "#b42318" }}>{err}</div>}
      <div className="rounded-xl overflow-hidden mb-5" style={{ border: "1px solid var(--border)" }}>
        {list.length === 0 && <div className="px-4 py-6 text-[12px]" style={{ color: "var(--text-tertiary)" }}>还没有添加模型——用下方表单加第一个（OpenAI 兼容端点即可）。</div>}
        {list.map(m => (
          <div key={m.id} className="flex items-center gap-3 px-4 py-3" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-primary)" }}>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-medium truncate" style={{ color: "var(--text-primary)" }}>
                {m.name} {(preferred === m.id || m.is_preferred) && <span className="ml-1.5 px-1.5 py-0.5 rounded text-[10px]" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>默认</span>}
              </div>
              <div className="text-[11px] truncate" style={{ color: "var(--text-tertiary)" }}>{m.model_name} · {m.base_url}</div>
            </div>
            {!(preferred === m.id || m.is_preferred) && (
              <button onClick={() => preferMyModel(m.id).then(load).catch(e => setErr((e as Error).message))}
                className="px-2.5 py-1 rounded-lg text-[11px] hover:opacity-80" style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>设为默认</button>
            )}
            <button onClick={() => { if (confirm(`删除模型「${m.name}」？`)) delMyModel(m.id).then(load).catch(e => setErr((e as Error).message)); }}
              className="px-2.5 py-1 rounded-lg text-[11px] hover:opacity-80" style={{ background: "color-mix(in srgb, #b42318 10%, transparent)", color: "#b42318" }}>删除</button>
          </div>
        ))}
      </div>
      <div className="text-[12px] font-semibold mb-2" style={{ color: "var(--text-primary)" }}>新增模型（OpenAI 兼容）</div>
      <div className="grid grid-cols-2 gap-2.5 mb-2.5">
        <input className={inCls} style={inStyle} placeholder="名称（如：我的 DeepSeek）" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} />
        <input className={inCls} style={inStyle} placeholder="模型名（如 deepseek-chat）" value={f.model_name} onChange={e => setF({ ...f, model_name: e.target.value })} />
        <input className={inCls} style={inStyle} placeholder="Base URL（如 https://api.deepseek.com/v1）" value={f.base_url} onChange={e => setF({ ...f, base_url: e.target.value })} />
        <input className={inCls} style={inStyle} placeholder="API Key" type="password" value={f.api_key} onChange={e => setF({ ...f, api_key: e.target.value })} />
      </div>
      <button onClick={submit} disabled={busy}
        className="px-4 py-2 rounded-lg text-[12px] font-semibold text-white disabled:opacity-60 hover:opacity-90"
        style={{ background: "var(--accent)" }}>{busy ? "添加中…" : "添加模型"}</button>
    </div>
  );
}

function MyLogsPanel() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    myAudit(200).then(r => { setLogs(r.logs || []); setErr(""); })
      .catch(e => setErr((e as Error).message || "加载失败（后端需 V270+）"))
      .finally(() => setLoading(false));
  }, []);
  const fmt = (ts: number) => { const ms = ts > 1e12 ? ts : ts * 1000; try { return new Date(ms).toLocaleString(); } catch { return String(ts); } };
  return (
    <div className="max-w-[860px]">
      <div className="text-[12px] mb-3" style={{ color: "var(--text-secondary)" }}>只显示<b>你自己</b>的操作记录（登录、对话、文件、配置变更等），与管理员全局日志同一口径。</div>
      {err && <div className="text-[12px] mb-3" style={{ color: "#b42318" }}>{err}</div>}
      {loading && <div className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>加载中…</div>}
      {!loading && logs.length === 0 && !err && <div className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>暂无记录。</div>}
      <div className="rounded-xl overflow-hidden" style={{ border: logs.length ? "1px solid var(--border)" : "none" }}>
        {logs.map((l, i) => (
          <div key={l.id ?? i} className="flex items-start gap-3 px-4 py-2.5 text-[12px]" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-primary)" }}>
            <span className="flex-shrink-0 w-[150px]" style={{ color: "var(--text-tertiary)" }}>{fmt(l.ts)}</span>
            <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[10.5px]" style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>{l.action}</span>
            <span className="flex-1 min-w-0 truncate" style={{ color: "var(--text-primary)" }} title={l.detail}>{l.detail || "—"}</span>
            <span className="flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>{l.ip || ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
