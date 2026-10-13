"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ElementType } from "react";
import {
  ArrowRight, Blocks, BookOpen, Brain, Check, CheckCircle2, ChevronDown,
  CalendarClock, CircleAlert, Clock3, Database, FileOutput, FolderKanban, Globe2, Image,
  LayoutDashboard, LayoutTemplate, Loader2, Monitor, Newspaper, RefreshCw,
  Search, ShieldCheck, Users, Wrench, XCircle, Upload, FileArchive, Copy,
} from "lucide-react";
import {
  activateManifestPlugin, deactivateManifestPlugin, getRuntimePluginDiagnostics, installRuntimePlugin, listRuntimePlugins, loadRuntimePlugin,
  quarantineRuntimePlugin, revokeRuntimePlugin, runtimeCapabilities, trustRuntimePlugin,
  type RuntimeCapability, type RuntimePlugin, type RuntimePluginDiagnostics,
} from "@/lib/api";
import {
  capabilityTruthPresentation, isCapabilityVisible, type CapabilityTone,
} from "@/lib/runtimeCapabilities";
import {
  onSelectedPluginIdsChanged, readSelectedPluginIds, writeSelectedPluginIds,
} from "@/lib/pluginSelection";
import { isDesktop } from "@/lib/desktop";
import { useStore } from "@/lib/store";
import { UserSkillsSettings } from "@/components/settings/UserSkillsSettings";
import { SearchIntegrationSettings } from "@/components/settings/SearchIntegrationSettings";

type CenterTab = "services" | "skills" | "search";
type CapabilityFilter = "all" | CapabilityMeta["category"];
type CapabilityMeta = {
  icon: ElementType;
  category: "knowledge" | "creation" | "action" | "continuity";
  outcome: string;
  examples: string[];
  actionLabel: string;
  prompt?: string;
  runMode?: "deep" | "browser" | "computer" | "team" | "canvas";
  view?: "work-active" | "hub-knowledge" | "docstudio" | "memory" | "scheduled";
};
type ServiceLauncher = {
  id: string;
  title: string;
  description: string;
  note: string;
  icon: ElementType;
  view?: "workbench" | "gworkspace" | "hub-knowledge" | "work-active" | "docstudio" | "remote" | "memory" | "scheduled";
  runMode?: "browser" | "computer" | "team" | "canvas" | "deep";
  prompt?: string;
  desktopOnly?: boolean;
};

const CAPABILITY_META: Record<string, CapabilityMeta> = {
  rag: {
    icon: Database, category: "knowledge", view: "hub-knowledge", actionLabel: "选择资料",
    outcome: "从你有权访问的资料中检索，并把依据带回当前对话。",
    examples: ["总结一组资料", "比较多个文档", "基于证据回答问题"],
  },
  web_research: {
    icon: Globe2, category: "knowledge", runMode: "deep", actionLabel: "开始调研",
    outcome: "检索网页、交叉核对来源，并在同一个 Chat 中形成可追溯结论。",
    examples: ["研究一个行业", "核对公开事实", "整理带来源的报告"],
  },
  image_search: {
    icon: Image, category: "knowledge", actionLabel: "查找图片",
    outcome: "在当前账号可见的图片资料中查找内容，并说明实际命中的文件。",
    examples: ["查找产品截图", "定位图表素材", "按内容筛选图片"],
    prompt: "请在我当前账号可见的图片资料中查找相关内容，并说明实际命中的文件与依据：",
  },
  artifacts: {
    icon: FileOutput, category: "creation", view: "docstudio", actionLabel: "制作成果",
    outcome: "把对话中的内容继续整理成文档、表格、演示稿或可下载成果。",
    examples: ["整理成正式文档", "生成演示稿", "导出结构化结果"],
  },
  canvas: {
    icon: LayoutTemplate, category: "creation", runMode: "canvas", actionLabel: "打开画布",
    outcome: "在右侧工作区生成并持续编辑可视化成果，修改会回到原任务。",
    examples: ["制作方案对比", "搭建数据看板", "多人协作评审"],
  },
  browser_use: {
    icon: Globe2, category: "action", runMode: "browser", actionLabel: "使用浏览器",
    outcome: "让 Chat 在右侧受控浏览器中打开页面、阅读内容并完成可审计操作。",
    examples: ["打开并阅读网页", "填写非敏感表单", "截图并提取证据"],
  },
  computer_use: {
    icon: Monitor, category: "action", runMode: "computer", actionLabel: "使用电脑",
    outcome: "在逐步可见、需要时确认的边界内操作本机文件和应用。",
    examples: ["整理本地文件", "操作桌面应用", "完成重复步骤"],
  },
  multi_agent: {
    icon: Users, category: "action", runMode: "team", actionLabel: "组织协作",
    outcome: "按目标分工并行推进，由主 Agent 汇总、查冲突并按验收标准交付。",
    examples: ["并行研究多个方向", "分别分析后交叉评审", "拆分大型交付任务"],
  },
  memory: {
    icon: Brain, category: "continuity", view: "memory", actionLabel: "管理记忆",
    outcome: "查看、修正或删除跨对话保留的偏好与长期信息。",
    examples: ["记住输出偏好", "查看已保存信息", "纠正过期记忆"],
  },
  long_tasks: {
    icon: Clock3, category: "continuity", view: "work-active", actionLabel: "继续任务",
    outcome: "离开界面后仍保留任务状态，回来继续处理待确认事项和成果。",
    examples: ["继续后台任务", "处理待确认事项", "验收最近成果"],
  },
  automations: {
    icon: CalendarClock, category: "continuity", view: "scheduled", actionLabel: "安排任务",
    outcome: "按你的时区重复执行只读工作，把结果送到任务记录或绑定的原对话。",
    examples: ["每天生成工作简报", "定期整理资料变化", "检查知识可用状态"],
  },
  publisher_studio: {
    icon: Newspaper, category: "creation", runMode: "deep", actionLabel: "开始创作",
    outcome: "把热点候选、原始来源核验、编辑结构与公众号 Markdown/HTML 交付留在同一个 Chat。",
    examples: ["生成今日 AI 热点简报", "把资料写成公众号专题", "核验选题并生成公众号 HTML"],
    prompt: "请使用“AI 公众号简报工作室”制作过去 24 小时 AI 行业简报。先联网发现候选，再打开原始来源核验标题、发布时间与关键事实；本轮没有附件或已选资料时，不要扫描整个私有资料库。交付 Markdown 和公众号兼容 HTML 草稿，逐条标注来源、时间与不确定项；发布前必须再次让我确认。",
  },
};

const SERVICE_LAUNCHERS: ServiceLauncher[] = [
  {
    id: "desktop-workbench", title: "电脑工作台", icon: LayoutDashboard, view: "workbench", desktopOnly: true,
    description: "在一个界面查看文件、预览代码和复用终端。",
    note: "适合需要在本机持续处理文件或代码的工作",
  },
  {
    id: "project-work", title: "项目工作", icon: FolderKanban,
    description: "在当前 Chat 中建立或继续项目，让资料、对话和成果保持在一起。",
    note: "项目不是另一套应用；创建后仍在当前对话继续",
    prompt: "请帮我把当前工作整理为一个项目：先确认项目名称、目标、交付物和验收标准，再在当前对话继续推进。",
  },
  {
    id: "publisher-work", title: "AI 公众号简报", icon: Newspaper, runMode: "deep",
    description: "核验热点来源，整理成公众号 Markdown 与兼容 HTML。",
    note: "可与 AI 热点自动任务配合；发送与发布始终需要你确认",
    prompt: "请使用“AI 公众号简报工作室”制作过去 24 小时 AI 行业简报。先联网发现候选，再打开原始来源核验标题、发布时间与关键事实；本轮没有附件或已选资料时，不要扫描整个私有资料库。交付 Markdown 和公众号兼容 HTML 草稿，逐条标注来源、时间与不确定项；发布前必须再次让我确认。",
  },
  {
    id: "knowledge-work", title: "资料工作区", icon: BookOpen, view: "hub-knowledge",
    description: "导入、阅读并把已有资料用于 Chat 和交付。",
    note: "适合围绕一组文件提炼、比较和问答",
  },
  {
    id: "delivery-work", title: "文档与成果", icon: FileOutput, view: "docstudio",
    description: "继续处理上传文件，并把结果交付为可下载内容。",
    note: "适合需要正式文件而不只是文字回答的工作",
  },
  {
    id: "browser-chat", title: "受控浏览器", icon: Globe2, runMode: "browser",
    description: "在右侧打开网页，把页面内容和证据带回当前 Chat。",
    note: "适合公开信息、网页阅读与可审计操作",
  },
  {
    id: "computer-chat", title: "电脑操作", icon: Monitor, runMode: "computer", desktopOnly: true,
    description: "在你确认的边界内处理本机文件和应用。",
    note: "适合重复步骤、文件整理和桌面任务",
  },
  {
    id: "canvas-chat", title: "工作画布", icon: LayoutTemplate, runMode: "canvas",
    description: "把 Chat 结果变成可继续编辑的可视化成果。",
    note: "适合方案、看板、网页和交付物",
  },
  {
    id: "team-chat", title: "协作分工", icon: Users, runMode: "team",
    description: "按目标拆分角色、并行推进，再由主 Chat 汇总和验收。",
    note: "适合资料多、需要交叉评审的复杂任务",
  },
  {
    id: "device-handoff", title: "设备接力", icon: Monitor, view: "remote",
    description: "把当前工作接回 App，或连接自己的另一台电脑。",
    note: "连接状态、权限和实际链路都会如实显示",
  },
  {
    id: "automation-work", title: "自动任务", icon: CalendarClock, view: "scheduled",
    description: "安排重复工作，离开后仍保留执行、核验和结果记录。",
    note: "按本地时区运行；只读动作可无人值守，敏感操作仍需确认",
  },
];

const CATEGORY_COPY: Array<{
  id: CapabilityMeta["category"];
  title: string;
  description: string;
}> = [
  { id: "knowledge", title: "研究与资料", description: "查资料、核来源，把证据带回 Chat" },
  { id: "creation", title: "创作与成果", description: "从对话继续编辑和交付可用内容" },
  { id: "action", title: "浏览器、电脑与协作", description: "在可见、可控的边界内完成真实操作" },
  { id: "continuity", title: "持续工作", description: "跨轮次保留目标，让长任务可以继续" },
];

const TONE_STYLE: Record<CapabilityTone, { color: string; background: string }> = {
  success: { color: "#15803d", background: "rgba(21,128,61,.09)" },
  warning: { color: "#b45309", background: "rgba(180,83,9,.09)" },
  error: { color: "#b42318", background: "rgba(180,35,24,.08)" },
  neutral: { color: "var(--text-tertiary)", background: "var(--bg-secondary)" },
};

function pluginStatusText(plugin: RuntimePlugin): string {
  if (plugin.active) return "已可用";
  if (plugin.status === "changed") return "需要重新确认";
  if (plugin.status === "invalid") return "清单无效，可查看诊断或移入隔离区";
  if (plugin.status === "manifest_changed") return "声明已变化，需要重新核对配置";
  if (plugin.trusted) return "正在准备";
  if (plugin.runtime === "manifest") return "声明式集成，待配置启用（不执行代码）";
  return "等待管理员确认";
}

function matchesCapability(capability: RuntimeCapability, query: string): boolean {
  if (!query) return true;
  const meta = CAPABILITY_META[capability.id];
  return [
    capability.title, capability.description, meta?.outcome || "",
    ...(meta?.examples || []),
  ].join(" ").toLowerCase().includes(query);
}

function CapabilityRow({
  capability, selected, onSelect, onUse,
}: {
  capability: RuntimeCapability;
  selected: boolean;
  onSelect: () => void;
  onUse: () => void;
}) {
  const meta = CAPABILITY_META[capability.id];
  const Icon = meta?.icon || Blocks;
  const status = capabilityTruthPresentation(capability);
  const usable = capability.availability !== "unavailable" && capability.state !== "disabled";
  return (
    <article className="plugin-capability-row" style={{
      background: selected ? "var(--accent-light)" : "var(--bg-primary)",
      border: `1px solid ${selected ? "color-mix(in srgb, var(--accent) 30%, var(--border))" : "var(--border)"}`,
    }}>
      <button onClick={onSelect} className="min-w-0 flex-1 flex items-center gap-3 text-left">
        <span className="plugin-capability-icon" style={{ color: usable ? "var(--accent)" : "var(--text-tertiary)" }}>
          <Icon size={17} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="text-[12.5px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{capability.title}</span>
            <span className="text-[9px] px-1.5 py-0.5 rounded-full flex-shrink-0" style={TONE_STYLE[status.tone]}>{status.label}</span>
          </span>
          <span className="block text-[10.5px] mt-1 truncate" style={{ color: "var(--text-tertiary)" }}>{meta?.outcome || capability.description}</span>
        </span>
      </button>
      <button onClick={onUse} disabled={!usable} className="plugin-use-button disabled:opacity-40 disabled:cursor-not-allowed">
        {meta?.actionLabel || "使用"}
      </button>
    </article>
  );
}

function CapabilityDetail({ capability, onUse }: { capability: RuntimeCapability; onUse: () => void }) {
  const meta = CAPABILITY_META[capability.id];
  const Icon = meta?.icon || Blocks;
  const status = capabilityTruthPresentation(capability);
  const usable = capability.availability !== "unavailable" && capability.state !== "disabled";
  return (
    <section className="plugin-detail-card">
      <div className="flex items-start gap-3">
        <span className="plugin-capability-icon" style={{ color: "var(--accent)" }}><Icon size={18} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-[16px] font-semibold" style={{ color: "var(--text-primary)" }}>{capability.title}</h2>
            <span className="text-[9.5px] px-2 py-0.5 rounded-full" style={TONE_STYLE[status.tone]}>{status.label}</span>
            <span className="text-[9.5px] px-2 py-0.5 rounded-full" style={{ color: "var(--text-tertiary)", background: "var(--bg-secondary)" }}>无需安装</span>
          </div>
          <p className="text-[11px] mt-1 leading-5" style={{ color: "var(--text-secondary)" }}>{meta?.outcome || capability.description}</p>
        </div>
        <button onClick={onUse} disabled={!usable} className="plugin-primary-action disabled:opacity-40">
          {usable ? (meta?.actionLabel || "开始使用") : "当前不可用"} <ArrowRight size={12} />
        </button>
      </div>
      <div className="mt-4">
        <div className="text-[10px] font-medium" style={{ color: "var(--text-tertiary)" }}>你可以这样用</div>
        <div className="mt-2 flex flex-wrap gap-2">
          {(meta?.examples || []).map(example => (
            <button key={example} onClick={onUse} disabled={!usable}
              className="rounded-full px-3 py-1.5 text-[10.5px] disabled:opacity-40"
              style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              {example}
            </button>
          ))}
        </div>
      </div>
      {!usable && (
        <div className="mt-3 rounded-xl px-3 py-2 text-[10.5px] leading-5"
          style={{ color: "#b42318", background: "rgba(180,35,24,.06)", border: "1px solid rgba(180,35,24,.16)" }}>
          这项能力暂时没有接通。请稍后重试，或请管理员检查服务状态。
        </div>
      )}
    </section>
  );
}

export function PluginCenterView() {
  const user = useStore(state => state.user);
  const set = useStore(state => state.set);
  const [tab, setTab] = useState<CenterTab>("services");
  const [plugins, setPlugins] = useState<RuntimePlugin[]>([]);
  const [capabilities, setCapabilities] = useState<RuntimeCapability[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [desktopMode, setDesktopMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [pluginError, setPluginError] = useState("");
  const [capabilityError, setCapabilityError] = useState("");
  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<CapabilityFilter>("all");
  const [selectedCapabilityId, setSelectedCapabilityId] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const [diagnostics, setDiagnostics] = useState<RuntimePluginDiagnostics | null>(null);
  const [installNotice, setInstallNotice] = useState("");
  const uploadRef = useRef<HTMLInputElement>(null);
  const [chatSelection, setChatSelection] = useState<string[] | undefined>(() => readSelectedPluginIds());

  const refresh = useCallback(async () => {
    setLoading(true);
    setPluginError("");
    setCapabilityError("");
    const [pluginResult, capabilityResult] = await Promise.allSettled([
      listRuntimePlugins(), runtimeCapabilities(),
    ]);
    if (pluginResult.status === "fulfilled") {
      setPlugins(pluginResult.value.plugins || []);
      setCanManage(Boolean(pluginResult.value.can_manage));
    } else {
      setPlugins([]);
      setPluginError("外部插件暂时无法更新，内置能力仍可继续使用。");
    }
    if (capabilityResult.status === "fulfilled") {
      const visible = (capabilityResult.value.capabilities || [])
        .filter(item => isCapabilityVisible(item, user?.role))
        .filter(item => item.visibility === "user" && Boolean(CAPABILITY_META[item.id]));
      setCapabilities(visible);
      setSelectedCapabilityId(current => visible.some(item => item.id === current) ? current : (visible[0]?.id || ""));
    } else {
      setCapabilities([]);
      setCapabilityError("工作能力状态暂时无法更新，请检查服务连接后重试。");
    }
    setLoading(false);
  }, [user?.role]);

  useEffect(() => { setDesktopMode(isDesktop()); }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => onSelectedPluginIdsChanged(setChatSelection), []);

  const activePlugins = useMemo(() => plugins.filter(item => item.active), [plugins]);
  const reviewPlugins = useMemo(() => plugins.filter(item => !item.active), [plugins]);
  const activePluginNames = useMemo(() => activePlugins.filter(item => item.runtime === "python").map(item => item.name), [activePlugins]);
  const effectiveSelection = chatSelection === undefined ? activePluginNames : chatSelection;
  const normalizedQuery = query.trim().toLowerCase();
  const visibleCapabilities = useMemo(
    () => capabilities.filter(item =>
      matchesCapability(item, normalizedQuery)
      && (categoryFilter === "all" || CAPABILITY_META[item.id]?.category === categoryFilter)
    ),
    [capabilities, normalizedQuery, categoryFilter],
  );
  const visibleServices = useMemo(() => SERVICE_LAUNCHERS.filter(item => {
    if (item.desktopOnly && !desktopMode) return false;
    if (!normalizedQuery) return true;
    return `${item.title} ${item.description} ${item.note}`.toLowerCase().includes(normalizedQuery);
  }), [desktopMode, normalizedQuery]);
  const visibleActivePlugins = useMemo(() => activePlugins.filter(item => {
    if (!normalizedQuery) return true;
    return [item.name, item.description, ...item.capabilities].join(" ").toLowerCase().includes(normalizedQuery);
  }), [activePlugins, normalizedQuery]);
  const selectedCapability = capabilities.find(item => item.id === selectedCapabilityId);
  const readyCapabilities = capabilities.filter(item => item.availability !== "unavailable" && item.state !== "disabled");

  const useCapability = (capability: RuntimeCapability) => {
    const meta = CAPABILITY_META[capability.id];
    if (!meta) return;
    set({
      desktopView: meta.view || null,
      adminOpen: false,
      setOpen: false,
      pendingRunMode: meta.runMode || "",
      pendingPrompt: meta.prompt || "",
    });
  };

  const openService = (service: ServiceLauncher) => {
    set({
      desktopView: service.view || null,
      adminOpen: false,
      setOpen: false,
      pendingRunMode: service.runMode || "",
      pendingPrompt: service.prompt || "",
    });
  };

  const toggleForChat = (name: string) => {
    const next = effectiveSelection.includes(name)
      ? effectiveSelection.filter(item => item !== name)
      : [...effectiveSelection, name];
    writeSelectedPluginIds(next);
    setChatSelection(next);
  };

  const manage = async (action: "trust" | "load" | "revoke" | "activate" | "deactivate" | "quarantine", plugin: RuntimePlugin) => {
    setBusy(`${action}:${plugin.name}`);
    setPluginError("");
    try {
      if (action === "trust") await trustRuntimePlugin(plugin.name, plugin.sha256);
      if (action === "load") await loadRuntimePlugin(plugin.name);
      if (action === "revoke") await revokeRuntimePlugin(plugin.name);
      if (action === "activate") await activateManifestPlugin(plugin.name, plugin.sha256);
      if (action === "deactivate") await deactivateManifestPlugin(plugin.name, plugin.sha256);
      if (action === "quarantine") {
        if (!window.confirm(`将 ${plugin.name} 移入 ProjectVault 隔离区？文件不会永久删除，可重新导入修复后的版本。`)) return;
        await quarantineRuntimePlugin(plugin.name);
      }
      await refresh();
    } catch {
      setPluginError("插件状态没有更新，请检查清单与服务日志后重试。");
    } finally {
      setBusy("");
    }
  };

  const inspectPlugin = async (plugin: RuntimePlugin) => {
    setBusy(`inspect:${plugin.name}`); setPluginError("");
    try { setDiagnostics(await getRuntimePluginDiagnostics(plugin.name)); }
    catch (error) { setPluginError((error as Error)?.message || "无法读取插件诊断。" ); }
    finally { setBusy(""); }
  };

  const importPlugin = async (file: File | null | undefined) => {
    if (!file) return;
    setBusy("install"); setPluginError(""); setInstallNotice("");
    try {
      const existing = plugins.some(item => item.name === file.name.replace(/\.zip$/i, ""));
      let replace = existing && window.confirm("检测到可能的同名插件。是否执行受控升级？旧版本会进入隔离区，新版本仍需重新审核后才能执行。");
      let result;
      try { result = await installRuntimePlugin(file, replace); }
      catch (error) {
        if (!replace && (error as Error)?.message?.includes("同名插件已存在")
          && window.confirm("插件清单中的 id 已存在。是否将旧版本移入隔离区并导入新版本？新版本不会自动执行。")) {
          replace = true;
          result = await installRuntimePlugin(file, true);
        } else throw error;
      }
      setInstallNotice(
        `${result.plugin.name} 已${result.action === "upgraded" ? "升级并隔离旧版本" : "安全导入"}；`
        + `已验证 ${result.file_count} 个文件，包摘要 ${result.package_sha256.slice(0, 16)}…。当前未执行任何插件代码；`
        + (result.plugin.runtime === "python" ? "Python 插件仍需审核摘要后加载。" : "声明式集成只需核对权限与配置，不进入代码信任流程。"),
      );
      await refresh();
      setReviewOpen(true);
      setDiagnostics(await getRuntimePluginDiagnostics(result.plugin.name));
    } catch (error) {
      setPluginError((error as Error)?.message || "插件导入失败；没有安装或执行任何代码。" );
    } finally {
      setBusy("");
      if (uploadRef.current) uploadRef.current.value = "";
    }
  };

  const trySearchInChat = () => {
    set({
      desktopView: null,
      adminOpen: false,
      setOpen: false,
      pendingRunMode: "deep",
      pendingPrompt: "请联网检索并交叉核对来源，明确区分已确认事实、来源分歧与仍未知的信息：",
    });
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto workbench-vnext plugins-workbench" style={{ background: "var(--bg-secondary)" }}>
      <div className="max-w-[1040px] mx-auto px-7 py-7 plugin-center-shell">
        <div className="flex items-center justify-between gap-4 mb-7">
          <div className="inline-flex items-center gap-1 p-1 rounded-xl" style={{ background: "var(--bg-tertiary)" }}>
            <button onClick={() => setTab("services")} className="plugin-center-tab" data-active={tab === "services"}><Blocks size={13} /> 能力与插件</button>
            <button onClick={() => setTab("skills")} className="plugin-center-tab" data-active={tab === "skills"}><BookOpen size={13} /> 技能</button>
            <button onClick={() => setTab("search")} className="plugin-center-tab" data-active={tab === "search"}><Search size={13} /> 联网服务</button>
          </div>
          <button onClick={() => void refresh()} disabled={loading} className="p-2 rounded-lg hover:bg-[var(--bg-tertiary)] disabled:opacity-50" title="刷新可用状态">
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} style={{ color: "var(--text-secondary)" }} />
          </button>
        </div>

        {tab === "skills" ? (
          <>
            <div className="mb-6">
              <h1 className="text-[24px] font-semibold tracking-[-0.025em]" style={{ color: "var(--text-primary)" }}>技能</h1>
              <p className="text-[12px] mt-1" style={{ color: "var(--text-tertiary)" }}>把常用流程教给 HashMM。支持压缩包、网址，以及从 Codex、Claude Code 导入，之后可以直接在对话中使用。</p>
            </div>
            <UserSkillsSettings />
          </>
        ) : tab === "search" ? (
          <>
            <header className="mb-6">
              <h1 className="text-[24px] font-semibold tracking-[-0.025em]" style={{ color: "var(--text-primary)" }}>联网服务</h1>
              <p className="text-[12px] mt-1 leading-5 max-w-[760px]" style={{ color: "var(--text-tertiary)" }}>
                这里与管理后台的“检索设置”共用同一份账号配置。保存一次即可在桌面端和 App 的深度检索中使用。
              </p>
            </header>
            <SearchIntegrationSettings showChatAction onTryInChat={trySearchInChat} />
          </>
        ) : (
          <>
            <header className="mb-7">
              <h1 className="text-[26px] font-semibold tracking-[-0.025em]" style={{ color: "var(--text-primary)" }}>能力与插件</h1>
              <p className="text-[12.5px] mt-1.5 leading-5" style={{ color: "var(--text-tertiary)" }}>
                选择一种工作方式后会回到同一个 Chat；过程、资料、确认和成果不会被拆成互不相干的页面。
              </p>
              <label className="mt-5 h-10 px-3.5 rounded-xl flex items-center gap-2 max-w-[760px]" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <Search size={14} style={{ color: "var(--text-tertiary)" }} />
                <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索想完成的工作，例如调研、做文档或操作电脑"
                  className="bg-transparent outline-none min-w-0 flex-1 text-[12px]" style={{ color: "var(--text-primary)" }} />
              </label>
              <div className="mt-3 flex flex-wrap gap-2">
                {([
                  ["all", "全部"],
                  ["knowledge", "研究与资料"],
                  ["creation", "创作与成果"],
                  ["action", "操作与协作"],
                  ["continuity", "持续工作"],
                ] as Array<[CapabilityFilter, string]>).map(([value, label]) => (
                  <button key={value} onClick={() => setCategoryFilter(value)} className="rounded-full px-3 py-1.5 text-[10.5px]" style={{ color: categoryFilter === value ? "var(--accent)" : "var(--text-tertiary)", background: categoryFilter === value ? "var(--accent-light)" : "var(--bg-primary)", border: "1px solid var(--border)" }}>{label}</button>
                ))}
              </div>
            </header>

            {loading ? (
              <div className="h-[360px] flex items-center justify-center"><Loader2 size={20} className="animate-spin" style={{ color: "var(--accent)" }} /></div>
            ) : (
              <>
                {visibleServices.length > 0 && (
                  <section className="mb-8">
                    <div className="mb-3">
                      <h2 className="text-[13.5px] font-semibold" style={{ color: "var(--text-primary)" }}>常用工作区</h2>
                      <p className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>直接进入真实工作空间，不需要先理解 Agent、工具或运行参数</p>
                    </div>
                    <div className="grid grid-cols-2 gap-3 plugin-service-grid">
                      {visibleServices.map(service => {
                        const Icon = service.icon;
                        return (
                          <button key={service.id} onClick={() => openService(service)}
                            className="plugin-service-card group">
                            <span className="plugin-capability-icon" style={{ color: "var(--accent)" }}><Icon size={18} /></span>
                            <span className="min-w-0 flex-1">
                              <span className="block text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{service.title}</span>
                              <span className="block mt-1 text-[10.8px] leading-5" style={{ color: "var(--text-secondary)" }}>{service.description}</span>
                              <span className="block mt-1 text-[9.8px]" style={{ color: "var(--text-tertiary)" }}>{service.note}</span>
                            </span>
                            <ArrowRight size={14} className="opacity-35 transition-all group-hover:opacity-100 group-hover:translate-x-0.5" />
                          </button>
                        );
                      })}
                    </div>
                  </section>
                )}

                <section className="mb-7">
                  <div className="flex items-end justify-between mb-3">
                    <div>
                      <h2 className="text-[13.5px] font-semibold" style={{ color: "var(--text-primary)" }}>可以直接使用</h2>
                      <p className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{readyCapabilities.length} 项已经准备好；点击后从当前对话开始</p>
                    </div>
                    <button onClick={() => setTab("skills")} className="text-[10.5px] inline-flex items-center gap-1" style={{ color: "var(--accent)" }}>
                      管理我的工作方法 <ArrowRight size={12} />
                    </button>
                  </div>
                </section>

                {CATEGORY_COPY.map(category => {
                  const items = visibleCapabilities.filter(item => CAPABILITY_META[item.id]?.category === category.id);
                  if (!items.length) return null;
                  return (
                    <section key={category.id} className="mb-7">
                      <div className="mb-2.5">
                        <h2 className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{category.title}</h2>
                        <p className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{category.description}</p>
                      </div>
                      <div className="grid grid-cols-2 gap-2.5 plugin-capability-grid">
                        {items.map(capability => (
                          <CapabilityRow key={capability.id} capability={capability} selected={selectedCapabilityId === capability.id}
                            onSelect={() => setSelectedCapabilityId(capability.id)}
                            onUse={() => useCapability(capability)} />
                        ))}
                      </div>
                    </section>
                  );
                })}

                {selectedCapability && matchesCapability(selectedCapability, normalizedQuery) && (
                  <CapabilityDetail capability={selectedCapability} onUse={() => useCapability(selectedCapability)} />
                )}

                <section className="mt-8">
                  <div className="flex items-end justify-between mb-2.5">
                    <div>
                      <h2 className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>已接通的外部插件</h2>
                      <p className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>只展示已经审核并能在 Chat 中实际使用的插件</p>
                    </div>
                  </div>
                  <div className="rounded-2xl overflow-hidden" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                    {visibleActivePlugins.length === 0 ? (
                      <div className="px-5 py-8 text-center">
                        <Blocks size={20} className="mx-auto mb-2" style={{ color: "var(--text-tertiary)" }} />
                        <div className="text-[11.5px]" style={{ color: "var(--text-primary)" }}>还没有接通外部插件</div>
                        <div className="text-[10px] mt-1" style={{ color: "var(--text-tertiary)" }}>浏览器、资料、画布、电脑操作和长期任务等内置能力仍可直接使用。</div>
                      </div>
                    ) : visibleActivePlugins.map(plugin => {
                      const attached = effectiveSelection.includes(plugin.name);
                      return (
                        <div key={plugin.name} className="flex items-center gap-3 px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
                          <span className="plugin-capability-icon" style={{ color: "var(--accent)" }}><Check size={16} /></span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-[12px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{plugin.name}</span>
                            <span className="block text-[10px] mt-0.5 truncate" style={{ color: "var(--text-tertiary)" }}>{plugin.description || "可为当前对话提供额外能力"}</span>
                          </span>
                          {plugin.runtime === "python" ? <button onClick={() => toggleForChat(plugin.name)} className="plugin-use-button">
                            {attached ? "已用于 Chat" : "用于 Chat"}
                          </button> : <span className="text-[9.5px]" style={{ color: "#15803d" }}>声明式集成已启用</span>}
                          {canManage && <button onClick={() => void inspectPlugin(plugin)} disabled={Boolean(busy)} className="plugin-admin-button"><Search size={11} /> 检查</button>}
                          {canManage && plugin.runtime === "manifest" && <button onClick={() => void manage("deactivate", plugin)} disabled={Boolean(busy)} className="plugin-admin-button"><XCircle size={11} /> 停用</button>}
                        </div>
                      );
                    })}
                  </div>
                </section>

                {canManage && (
                  <section className="mt-5 rounded-2xl p-4" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                    <div className="flex items-start gap-3">
                      <span className="plugin-capability-icon" style={{ color: "var(--accent)" }}><FileArchive size={16} /></span>
                      <div className="min-w-0 flex-1">
                        <div className="text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}>安全导入外部插件</div>
                        <p className="mt-1 text-[9.8px] leading-5" style={{ color: "var(--text-tertiary)" }}>
                          ZIP 必须包含单一插件目录和 plugin.json。系统校验路径、大小、文件数、权限声明与 SHA-256；导入后不会自动信任或执行。
                        </p>
                        {installNotice && <div className="mt-2 rounded-lg px-3 py-2 text-[10px] leading-5" style={{ color: "#15803d", background: "rgba(21,128,61,.07)" }}>{installNotice}</div>}
                      </div>
                      <input ref={uploadRef} type="file" accept=".zip,application/zip" className="hidden" onChange={event => void importPlugin(event.target.files?.[0])} />
                      <button onClick={() => uploadRef.current?.click()} disabled={Boolean(busy)} className="plugin-admin-button"><Upload size={11} /> {busy === "install" ? "校验中…" : "导入 ZIP"}</button>
                    </div>
                  </section>
                )}

                {canManage && reviewPlugins.length > 0 && (
                  <section className="mt-5 rounded-2xl overflow-hidden" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                    <button onClick={() => setReviewOpen(value => !value)}
                      className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[var(--bg-secondary)]">
                      <ShieldCheck size={15} style={{ color: "var(--text-tertiary)" }} />
                      <span className="min-w-0 flex-1">
                        <span className="block text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>管理员插件审核</span>
                        <span className="block mt-0.5 text-[9.8px]" style={{ color: "var(--text-tertiary)" }}>{reviewPlugins.length} 个插件需要确认，不影响用户使用已接通能力</span>
                      </span>
                      <ChevronDown size={14} className={reviewOpen ? "rotate-180" : ""} style={{ color: "var(--text-tertiary)" }} />
                    </button>
                    {reviewOpen && reviewPlugins.map(plugin => (
                      <div key={plugin.name} className="px-4 py-3 flex items-center gap-3" style={{ borderTop: "1px solid var(--border)" }}>
                        <Wrench size={14} style={{ color: "var(--text-tertiary)" }} />
                        <span className="min-w-0 flex-1">
                          <span className="block text-[11px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{plugin.name}</span>
                          <span className="block text-[9.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{pluginStatusText(plugin)} · {plugin.runtime} · {plugin.sha256.slice(0, 12)}…</span>
                        </span>
                        <button onClick={() => void inspectPlugin(plugin)} disabled={Boolean(busy)} className="plugin-admin-button"><Search size={11} /> 检查</button>
                        {plugin.runtime === "python" && !plugin.trusted && plugin.status !== "invalid" && (
                          <button onClick={() => void manage("trust", plugin)} disabled={Boolean(busy)} className="plugin-admin-button"><ShieldCheck size={11} /> 确认版本</button>
                        )}
                        {plugin.runtime === "python" && plugin.trusted && (
                          <button onClick={() => void manage("load", plugin)} disabled={Boolean(busy)} className="plugin-admin-button"><CheckCircle2 size={11} /> 接通</button>
                        )}
                        {plugin.runtime === "manifest" && plugin.status !== "invalid" && (
                          <button onClick={() => void manage("activate", plugin)} disabled={Boolean(busy)} className="plugin-admin-button"><CheckCircle2 size={11} /> 配置并启用</button>
                        )}
                        {plugin.status === "invalid" && (
                          <button onClick={() => void manage("quarantine", plugin)} disabled={Boolean(busy)} className="plugin-admin-button"><XCircle size={11} /> 移入隔离区</button>
                        )}
                        {plugin.trusted && (
                          <button onClick={() => void manage("revoke", plugin)} disabled={Boolean(busy)} className="plugin-admin-button"><XCircle size={11} /> 撤销</button>
                        )}
                      </div>
                    ))}
                  </section>
                )}

                {canManage && diagnostics && (
                  <section className="mt-5 rounded-2xl p-4" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2"><ShieldCheck size={14} style={{ color: diagnostics.trust.trusted ? "#15803d" : "#b45309" }} /><h3 className="text-[12px] font-semibold">{diagnostics.name} 安全诊断</h3></div>
                        <div className="mt-1 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>版本 {diagnostics.version || "未声明"} · {diagnostics.file_count} 个文件 · {diagnostics.execution_boundary}</div>
                      </div>
                      <button onClick={() => setDiagnostics(null)} className="p-1"><XCircle size={14} /></button>
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-[10px]">
                      <div className="rounded-xl p-3" style={{ background: "var(--bg-secondary)" }}><div style={{ color: "var(--text-tertiary)" }}>文件系统</div><div className="mt-1 font-medium">{diagnostics.permissions.filesystem || "none"}</div></div>
                      <div className="rounded-xl p-3" style={{ background: "var(--bg-secondary)" }}><div style={{ color: "var(--text-tertiary)" }}>网络</div><div className="mt-1 font-medium">{diagnostics.permissions.network || "none"}</div></div>
                      <div className="rounded-xl p-3" style={{ background: "var(--bg-secondary)" }}><div style={{ color: "var(--text-tertiary)" }}>副作用</div><div className="mt-1 font-medium">{diagnostics.permissions.side_effects ? "已声明" : "无"}</div></div>
                    </div>
                    <div className="mt-3 rounded-xl p-3" style={{ background: "var(--bg-secondary)" }}>
                      <div className="flex items-center gap-2 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}><span className="flex-1 font-mono break-all">SHA-256 {diagnostics.sha256}</span><button title="复制摘要" onClick={() => navigator.clipboard?.writeText(diagnostics.sha256)}><Copy size={12} /></button></div>
                      <div className="mt-2 space-y-1.5">{diagnostics.tools.map(tool => <div key={tool.name} className="flex flex-wrap items-center gap-1.5 text-[9.5px]"><span className="font-medium">{tool.name}</span>{Object.entries(tool.annotations).filter(([, value]) => typeof value === "boolean" && value).map(([key]) => <span key={key} className="rounded-full px-1.5 py-0.5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>{key}</span>)}</div>)}</div>
                    </div>
                    {diagnostics.runtime === "python" && !diagnostics.trust.trusted && <div className="mt-3 text-[10px]" style={{ color: "#b45309" }}>当前 Python 摘要尚未获得管理员信任；插件工具不会进入 Chat。</div>}
                    {diagnostics.runtime === "manifest" && <div className="mt-3 text-[10px]" style={{ color: "var(--text-tertiary)" }}>这是声明式集成，不导入或执行插件代码；管理员核对权限和调度配置后即可启用。</div>}
                    {diagnostics.error && <div className="mt-3 text-[10px]" style={{ color: "#b42318" }}>{diagnostics.error}</div>}
                  </section>
                )}

                {(capabilityError || pluginError) && (
                  <div className="mt-4 rounded-xl px-3 py-2 text-[10.5px] flex items-center gap-2"
                    style={{ color: "#b42318", background: "rgba(180,35,24,.06)", border: "1px solid rgba(180,35,24,.16)" }}>
                    <CircleAlert size={12} /> {[capabilityError, pluginError].filter(Boolean).join(" ")}
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
