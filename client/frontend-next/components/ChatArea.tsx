"use client";
import { useRef, useEffect, useState, useCallback, useMemo, type SetStateAction } from "react";
import { NotificationBell } from "./NotificationBell";
import { useStore, persistSessions } from "@/lib/store";
import { searchMessages, stepHit } from "@/lib/messageSearch";
import { isDesktop, getCU, getBrowser, saveFile } from "@/lib/desktop";
import { showToast } from "@/lib/toast";
import { documentNamesFromContexts } from "@/lib/chatContext";
import { runComputerUse, cuSave, type CuStep } from "@/lib/cu";
import { CuReplayPanel } from "./CuReplayPanel";
import { openArtifact, isPanelPreviewable } from "@/lib/artifact";
import { TodoCard, type TodoItem } from "./TodoCard";
import { chatStream, chatStreamV10, generateTitle, createConversation, saveStreamingMessage, quickListModels, setDefaultModel, stats as apiStats, withToken, saveConvFile, sessionPatch, rewindConversation, getDispatch, loadConversation, getActiveTurn, steerActiveTurn, interruptActiveTurn, uploadConversationFileWithProgress, assignConversationProject, getConversationPrompt, saveConversationPrompt, createChatContinuation, getIncomingChatContinuation, transitionChatContinuation, type ActiveTurn, type ChatContinuation, type ConversationResourceSummary } from "@/lib/api";
import { canvasTemplateHtml } from "@/lib/canvasTemplate";
import CanvasMenuPortal from "./CanvasMenuPortal";
import { listCanvasTemplates, getCanvasTemplate, importCanvasTemplates, promoteCanvasTemplate, marketTemplates, marketInstall, createDispatch } from "@/lib/api";
import { renderMsg, sanitizeLLMOutput } from "@/lib/render";
import type { TraceStep, Source, TaskContract, Message } from "@/lib/types";
import { MsgBubble } from "./MsgBubble";
import HashMascotHero from "./HashMascotHero";
import { loopGoal, loopInterval, myModels, preferMyModel, runtimeCapabilities, type MyModel } from "@/lib/api";
import HashMascot from "./HashMascot";
import { Paperclip, ArrowUp, X, Zap, BookOpen, GitCompare, HelpCircle, PanelLeft, Square, Cpu,
         FileText, Image, FileCode, FileSpreadsheet, File as FileIcon, ChevronDown, Check,
         Download, Loader2, Upload, Eye, Search as SearchIcon, Network, Code2, Globe, Sparkles, Database, Bot, Camera, Monitor, ScrollText, ChevronUp, LayoutTemplate, Users, Link2, PanelRight, Blocks, FolderKanban, Forward } from "lucide-react";
import { CommandPalette } from "./CommandPalette";
import { TemplateMarket } from "./TemplateMarket";
import { AgentLog } from "./AgentLog";
import { SubAgentPanel } from "./SubAgentPanel";
import { TeamPanel } from "./TeamPanel";
import {
  CAPABILITY_PREFS_EVENT,
  readCapabilityPrefs,
  type ChatCapability,
} from "@/lib/capabilityPrefs";
import { QualityBadgesRow } from "./QualityBadges";
import { notifyTaskComplete, requestNotificationPermission } from "@/lib/notifications";
import { DocFilterChips } from "./DocFilterChips";
import { featureContextLabel, featureContextsForLocalTool } from "@/lib/chatContext";
import { buildToolHistory } from "@/lib/toolHistory";
import { openBrowserInInspector } from "@/lib/browserInspector";
import { COMPOSER_MAX_HEIGHT, COMPOSER_MIN_HEIGHT, composerTextareaHeight } from "@/lib/workspaceLayout";
import { useWorkMethod } from "./chat/useWorkMethod";
import { onSelectedPluginIdsChanged, readSelectedPluginIds } from "@/lib/pluginSelection";
import { readAccountProjects, readActiveProject, writeActiveProject } from "@/lib/accountWorkspaceCache";
import { toPublicAgentTimeline } from "@/lib/publicAgentTimeline";
import {
  flattenRelativeAttachmentName,
  normalizeTodoManifestVersion,
  shouldAcceptTodoManifest,
  type TodoManifestVersion,
} from "@/lib/chatDelivery";

type AttachmentState = "selected" | "uploading" | "uploaded" | "failed";
interface AttachmentReceipt {
  ok: boolean;
  filename: string;
  size: number;
  sha256: string;
  download_url: string;
  resource?: ConversationResourceSummary;
}
interface UFile {
  id: string;
  name: string;
  size: string;
  ext: string;
  text: string;
  relativePath?: string;
  dataUrl?: string;
  sourceFile?: globalThis.File;
  state: AttachmentState;
  uploadedBytes: number;
  totalBytes: number;
  error?: string;
  receipt?: AttachmentReceipt;
}

interface DroppedEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (success: (file: globalThis.File) => void, failure?: (error: DOMException) => void) => void;
  createReader?: () => { readEntries: (success: (entries: DroppedEntry[]) => void, failure?: (error: DOMException) => void) => void };
}

async function readDroppedEntry(entry: DroppedEntry, prefix = ""): Promise<Array<{ file: globalThis.File; relativePath: string }>> {
  const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile && entry.file) {
    const file = await new Promise<globalThis.File>((resolve, reject) => entry.file!(resolve, reject));
    return [{ file, relativePath }];
  }
  if (!entry.isDirectory || !entry.createReader) return [];
  const reader = entry.createReader();
  const children: DroppedEntry[] = [];
  while (true) {
    const batch = await new Promise<DroppedEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    children.push(...batch);
  }
  const nested = await Promise.all(children.map(child => readDroppedEntry(child, relativePath)));
  return nested.flat();
}

function fileForConversationUpload(item: UFile): globalThis.File {
  if (!item.sourceFile) {
    throw new Error(`附件「${item.name}」需要重新添加后才能发送`);
  }
  const relative = String(item.relativePath || "").trim();
  if (!relative || relative === item.sourceFile.name) return item.sourceFile;
  // The conversation file endpoint stores a flat, owner-scoped file list.
  // Preserve folder identity without smuggling path separators to the server.
  const flatName = flattenRelativeAttachmentName(relative, item.sourceFile.name);
  return new File([item.sourceFile], flatName, {
    type: item.sourceFile.type,
    lastModified: item.sourceFile.lastModified,
  });
}

type ComposerDrafts = Record<string, string>;
const NEW_CHAT_DRAFT = "__new__";

function composerDraftStorageKey(userId: string): string {
  return `hmm_composer_drafts:${userId || "anonymous"}`;
}

function readComposerDrafts(userId: string): ComposerDrafts {
  if (typeof window === "undefined") return {};
  try {
    const value = JSON.parse(localStorage.getItem(composerDraftStorageKey(userId)) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

function writeComposerDrafts(userId: string, drafts: ComposerDrafts): void {
  if (typeof window === "undefined") return;
  try {
    // 保留最近编辑过且非空的草稿；防止长期使用后无界增长。
    const entries = Object.entries(drafts).filter(([, value]) => value.length > 0).slice(-100);
    localStorage.setItem(composerDraftStorageKey(userId), JSON.stringify(Object.fromEntries(entries)));
  } catch { /* 配额不足不影响当前输入 */ }
}

// V308 修 P0-4：流式渲染的**唯一**内容边界——始终走完整 sanitize + render。
// 删除了原先的"增量快路径"：当 delta<300 时它做
//     _srHtml + text.slice(_srLen).replace(/\n/g,"<br>")
// 把【未经 sanitizeLLMOutput 清理】的原始增量直接拼进已渲染 HTML。逐 token 到达时，
// 一个恶意标签可能被拆成多个 token（如 "<scr"+"ipt>"、"<img sr"+"c=x onerror=..."），
// 单 token/单增量的正则清理【结构上】拦不住跨 token 拼接 —— 这正是审计 P0-4 的要点。
// 现在每次都对【完整累积文本】做 renderMsg（内部先 stripDangerousHtml），无论标签被
// 怎样切分，看到的都是完整字符串，清理器才能生效。缓存仅用于"内容未变则复用结果"，
// 不再承担"跳过清理"的职责。逐 token 重渲的性能由 ChatArea 的 rAF 缓冲（下方）解决。
let _srHtml = "", _srKey = "", _srSrc = "";
function safeRenderStream(text: string, cacheKey = ""): string {
  if (cacheKey !== _srKey) { _srKey = cacheKey; _srHtml = ""; _srSrc = ""; }  // 会话切换即重置
  if (text === _srSrc && _srHtml) return _srHtml;   // 纯缓存命中（内容一字未变）
  // Strip markdown headers → bold (Claude-style) before rendering
  let t = text.replace(/^(#{1,4})\s+(.+)$/gm, (_, _h, title) => `**${title.trim()}**\n`);
  // Close unclosed code/math blocks（流式期间标记可能尚未闭合）
  const cc = (t.match(/```/g) || []).length;
  if (cc % 2 !== 0) t += "\n```";
  const mc = (t.match(/\$\$/g) || []).length;
  if (mc % 2 !== 0) t += "$$";
  try {
    _srHtml = renderMsg(t);   // 完整 sanitize + render，是流式内容唯一进入 DOM 的路径
    _srSrc = text;
    return _srHtml;
  } catch (_e) {
    // 兜底也必须先清理，绝不回退到原始拼接
    _srHtml = sanitizeLLMOutput(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
    _srSrc = text;
    return _srHtml;
  }
}

// ── V272 专家人格（借鉴 agency-agents 的"人格+流程+硬约束交付"与 WorkBuddy/QoderWork
// 的"专家"玩法）：/persona 名称 → 写入会话运行时补丁 system_append，仅本会话生效、
// 下一轮起效、/persona off 一键还原。每个人格都带**可验收的硬约束**，不是空泛口吻。
const PERSONAS: Record<string, { label: string; sys: string }> = {
  "验收员": { label: "严苛验收员", sys: "你现在是严苛验收员：对每个交付默认找出至少 3 处具体问题（引用原文/行号），每条问题给修复建议；无法验证的说法必须标注'未验证'；结论前先列证据。" },
  "数据分析师": { label: "数据分析师", sys: "你现在是数据分析师：所有数字必须注明来源与口径；先给结论卡（指标/变化/置信度），再展开；缺数据时明确列出'还需要哪些数据'而不是估算。" },
  "文档架构师": { label: "文档架构师", sys: "你现在是文档架构师：输出必须有清晰层级（概述→细节→行动项）；每节≤5 要点；术语首次出现给一句话定义；结尾附'下一步'清单。" },
  "产品经理": { label: "产品经理", sys: "你现在是产品经理：一切围绕用户价值与优先级；输出需求时用'用户故事+验收标准'格式；主动指出范围蔓延与依赖风险。" },
  "红队测试员": { label: "红队测试员", sys: "你现在是红队测试员：对给定方案主动构造 3 个失败场景/边界条件/滥用路径，并给出各自的防御措施；不许只说'看起来没问题'。" },
};

const TEMPLATES = [
  { icon: Zap, text: "帮我写一个 Transformer Encoder 的 PyTorch 实现", color: "#d97706", label: "代码生成" },
  { icon: FileText, text: "分析小米2024年营收和利润趋势，做一个PPT", color: "#7c3aed", label: "分析+PPT" },
  { icon: BookOpen, text: "对比腾讯和网易2025年的财务数据", color: "#059669", label: "知识问答" },
  { icon: HelpCircle, text: "帮我拆解这篇论文 https://arxiv.org/abs/2410.21276", color: "#2563eb", label: "论文分析" },
  { icon: GitCompare, text: "帮我写一份数据分析报告，用图表展示关键指标", color: "#dc2626", label: "数据分析" },
  { icon: Zap, text: "帮我写一个 C++ 的红黑树实现", color: "#0891b2", label: "C++ 代码" },
];

function fIcon(ext: string) {
  if (["pdf"].includes(ext)) return { Icon: FileText, color: "#ef4444" };
  if (["png","jpg","jpeg","gif","webp","bmp"].includes(ext)) return { Icon: Image, color: "#8b5cf6" };
  if (["py","js","ts","java","cpp","c","go","rs"].includes(ext)) return { Icon: FileCode, color: "#059669" };
  if (["xlsx","csv","xls"].includes(ext)) return { Icon: FileSpreadsheet, color: "#059669" };
  return { Icon: FileIcon, color: "var(--text-tertiary)" };
}

function FileChip({ f, onRemove, onRetry }: { f: UFile; onRemove: () => void; onRetry: () => void }) {
  const { Icon, color } = fIcon(f.ext);
  // V86: 图片附件（截屏走这里）直接显示缩略图，所见即所发
  if (f.dataUrl) {
    return <div className="relative inline-block rounded-lg overflow-hidden anim-fade-up" style={{ border: "1px solid var(--border)" }}>
      <img src={f.dataUrl} alt={f.name} title={f.name} className="block" style={{ height: 56, maxWidth: 120, objectFit: "cover" }} />
      <div className="absolute bottom-0 left-0 h-0.5 transition-all" style={{ width: `${f.totalBytes ? Math.round(f.uploadedBytes / f.totalBytes * 100) : 0}%`, background: "var(--accent)" }} />
      <button onClick={onRemove} title="移除"
        className="absolute top-0.5 right-0.5 p-0.5 rounded-full"
        style={{ background: "rgba(0,0,0,.55)" }}>
        <X size={10} style={{ color: "#fff" }} />
      </button>
    </div>;
  }
  return <div className="inline-flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-lg text-[11px] max-w-[200px]" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)" }}>
    <Icon size={13} style={{ color }} className="flex-shrink-0" />
    <span className="truncate font-medium" style={{ color: "var(--text-primary)" }}>{f.name}</span>
    <span className="flex-shrink-0" style={{ color: f.state === "failed" ? "#b42318" : "var(--text-tertiary)" }}>
      {f.state === "uploading" ? `${Math.round((f.uploadedBytes / Math.max(1, f.totalBytes)) * 100)}%` : f.state === "uploaded" ? "已上传" : f.state === "failed" ? "失败" : f.size}
    </span>
    {f.state === "failed" && <button onClick={onRetry} title={f.error || "重试"} className="text-[10px] px-1 py-0.5 rounded" style={{ color: "var(--accent)" }}>重试</button>}
    <button onClick={onRemove} className="p-0.5 rounded hover:bg-[var(--bg-secondary)] flex-shrink-0"><X size={11} style={{ color: "var(--text-tertiary)" }} /></button>
  </div>;
}

function ModelSwitcher({ current }: { current: string }) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<Array<{ id: string; name: string; is_default: number }>>([]);
  return <div className="relative min-w-0 max-w-[160px] flex-shrink">
    <button onClick={async () => { if (open) { setOpen(false); return; } setModels(await quickListModels()); setOpen(true); }}
      className="flex min-w-0 max-w-full items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium whitespace-nowrap transition-all hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--accent)" }}>
      <span className="min-w-0 truncate">{current}</span><ChevronDown size={11} className="flex-shrink-0" />
    </button>
    {open && models.length > 0 && <>
      <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
      <div className="absolute top-full left-0 mt-1 z-40 rounded-xl overflow-hidden min-w-[200px] anim-fade-up" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
        {models.map(m => <button key={m.id} onClick={async () => { setOpen(false); await setDefaultModel(m.id); apiStats().then(s => useStore.getState().set({ stats: s })).catch(() => {}); }}
          className="w-full flex items-center gap-2 px-3 py-2 text-[12px] transition-colors hover:bg-[var(--bg-secondary)]" style={{ color: "var(--text-primary)" }}>
          <span className="flex-1 text-left truncate">{m.name}</span>
          {m.is_default ? <Check size={13} style={{ color: "var(--accent)" }} /> : null}
        </button>)}
      </div>
    </>}
  </div>;
}

export function ChatArea() {
  const { sessions, sid, stats, sbOpen, customPrompt, artifactPanel, rightPanelOpen, streamingConvId, user, token } = useStore();
  const set = useStore(s => s.set);
  const desktopView = useStore(s => s.desktopView);
  const addSession = useStore(s => s.addSession);
  const addMsg = useStore(s => s.addMsg);
  const featureContexts = useStore(s => s.featureContexts);
  // V103.90 多标签
  const openTabs = useStore(s => s.openTabs);
  const openTabAction = useStore(s => s.openTab);
  const closeTabAction = useStore(s => s.closeTab);
  const syncTabs = useStore(s => s.syncTabs);
  // Codex 式会话草稿：输入属于具体 Chat，而不是 ChatArea 这个全局组件实例。
  // 切到别的消息时只切换草稿 key；重开桌面端后也恢复当前账号自己的草稿。
  const draftScope = user?.id || user?.username || "anonymous";
  const draftKey = sid || NEW_CHAT_DRAFT;
  const [composerDrafts, setComposerDrafts] = useState<ComposerDrafts>(() => readComposerDrafts(draftScope));
  const text = composerDrafts[draftKey] || "";
  const setText = useCallback((nextValue: SetStateAction<string>) => {
    setComposerDrafts(previous => {
      const current = previous[draftKey] || "";
      const next = typeof nextValue === "function" ? nextValue(current) : nextValue;
      const updated = { ...previous };
      delete updated[draftKey];
      if (next) updated[draftKey] = next;
      writeComposerDrafts(draftScope, updated);
      return updated;
    });
  }, [draftKey, draftScope]);
  useEffect(() => { setComposerDrafts(readComposerDrafts(draftScope)); }, [draftScope]);
  // 复跑：从「运行轨迹」带来的问题，填进输入框（不自动发，用户审一眼再发）
  const pendingPrompt = useStore(s => s.pendingPrompt);
  const pendingRunMode = useStore(s => s.pendingRunMode);
  useEffect(() => {
    if (pendingPrompt) {
      setText(pendingPrompt);
      useStore.getState().set({ pendingPrompt: "" });
      setTimeout(() => textRef.current?.focus(), 50);
    }
  }, [pendingPrompt]);
  const [streaming, setStreaming] = useState(false);
  const [focusMode, setFocusMode] = useState(() => {
    if (typeof window === "undefined") return false;
    try { return localStorage.getItem("hmm_chat_focus_mode") === "1"; } catch { return false; }
  });
  const [isDesktopEnv, setIsDesktopEnv] = useState(false);
  useEffect(() => { setIsDesktopEnv(isDesktop()); }, []);
  const [streamContent, setStreamContent] = useState("");
  const [traceSteps, setTraceSteps] = useState<TraceStep[]>([]);
  const [taskPlan, setTaskPlan] = useState<{ goal: string; steps: { n: number; action: string; acceptance: string }[] } | null>(null);
  const [taskContract, setTaskContract] = useState<TaskContract | null>(null);
  const traceRef = useRef<TraceStep[]>([]);
  const [thinkingContent, setThinkingContent] = useState("");
  const [agentSteps, setAgentSteps] = useState<Array<{ tool: string; status: string; detail: string; args?: Record<string, unknown>; duration_ms?: number; id?: string; hooks?: import("@/lib/types").HookRun[]; receipt?: import("@/lib/types").ExecutionReceipt }>>([]);
  const agentStepsRef = useRef<typeof agentSteps>([]);
  // 统一有序事件流（对标 Claude：思考/工具/正文按真实发生顺序交错，而非思考全堆前面）。
  // 每个元素: {kind: "trace"|"tool", node, detail, tool?, status, elapsed_ms, id}
  const [liveTimeline, setLiveTimeline] = useState<Array<{ kind: string; node: string; detail: string; tool?: string; status: "done" | "running" | "error"; elapsed_ms?: number; id: string; hooks?: import("@/lib/types").HookRun[] }>>([]);
  // V50: 任务清单（update_todo 全量覆盖，原地替换渲染；done 时持久化进消息）
  const [todoItems, setTodoItems] = useState<TodoItem[]>([]);
  const todoRef = useRef<TodoItem[]>([]);
  const todoManifestRef = useRef<TodoManifestVersion | null>(null);
  const clarifyRef = useRef<{ question: string; options: string[] } | null>(null);
  // V54: 真·流式直播缓冲——逐字预览模型正在写的内容；权威事件（narrate/token）
  // 到达时清空让位（同文替换，视觉无缝），旧协议/关开关时恒为空 = 零变化。
  const [liveDelta, setLiveDelta] = useState("");
  const [liveOrch, setLiveOrch] = useState<any>(null);  // V103.30 子 agent 编排实时
  const liveOrchRef = useRef<any>(null);
  // V55: 上下文用量（每轮由 ctx 事件刷新）
  const [ctxInfo, setCtxInfo] = useState<{ chars: number; budget: number } | null>(null);
  const timelineRef = useRef<Array<{ kind: string; node: string; detail: string; status: "done" | "running" | "error"; elapsed_ms?: number; id: string }>>([]);
  const [agentIteration, setAgentIteration] = useState<{ current: number; max: number } | null>(null);
  const [createdFiles, setCreatedFiles] = useState<Array<{ filename: string; download_url: string }>>([]);
  const [cmdPaletteOpen, setCmdPaletteOpen] = useState(false);
  const [customPromptOpen, setCustomPromptOpen] = useState(false);
  const [convPrompt, setConvPrompt] = useState("");
  const [incomingHandoff, setIncomingHandoff] = useState<ChatContinuation | null>(null);
  const [handoffBusy, setHandoffBusy] = useState(false);
  useEffect(() => {
    if (!customPromptOpen || !sid) return;
    let alive = true;
    void getConversationPrompt(sid)
      .then(result => { if (alive) setConvPrompt(result.prompt || ""); })
      .catch(() => { if (alive) setConvPrompt(""); });
    return () => { alive = false; };
  }, [customPromptOpen, sid]);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [docFilter, setDocFilter] = useState<string[]>([]);
  // 工作方式是 Chat 的领域状态，不再由一组互相覆盖的布尔开关散落维护。
  const {
    retrievalMode, cycleRetrieval,
    effortMode, cycleEffort,
    browserMode, setBrowserMode,
    deepMode, setDeepMode,
    cuMode, setCuMode,
  } = useWorkMethod();
  const [streamSources, setStreamSources] = useState<Source[]>([]);

  // v10: Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "n") {
        e.preventDefault();
        writeActiveProject(useStore.getState().user, "");
        useStore.getState().newChat();
      }
      if (e.key === "Escape" && streaming) {
        e.preventDefault();
        void interruptCurrentTurn();
      }
      // v7.0: Ctrl+D toggle dark mode
      if ((e.ctrlKey || e.metaKey) && e.key === "d") {
        e.preventDefault();
        const s = useStore.getState();
        const newDark = !s.dark;
        s.set({ dark: newDark });
        localStorage.setItem("hmm_dark", newDark ? "1" : "");
        localStorage.setItem("hmm_theme_mode", newDark ? "dark" : "light");
      }
      // V103.90 Ctrl/⌘+F 会话内搜索
      if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F")) {
        e.preventDefault();
        setSearchOpen(true);
        setTimeout(() => searchRef.current?.focus(), 30);
      }
      // v7.0: Ctrl+Shift+C copy last assistant answer
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === "C") {
        e.preventDefault();
        const sess = useStore.getState().sessions.find(s => s.id === sid);
        if (sess) {
          const last = [...sess.messages].reverse().find(m => m.role === "assistant");
          if (last) navigator.clipboard.writeText(last.content).catch(() => {});
        }
      }
      // v7.0: ↑ in empty input → edit last user message
      if (e.key === "ArrowUp" && !text && !streaming && document.activeElement === textRef.current) {
        if (session) {
          const userMsgs = session.messages.map((m, i) => ({ ...m, idx: i })).filter(m => m.role === "user");
          if (userMsgs.length > 0) {
            e.preventDefault();
            handleEdit(userMsgs[userMsgs.length - 1].idx);
          }
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [streaming, text, sid]);
  // Attachment drafts follow the same ownership rule as text drafts: changing
  // Chat must never carry selected bytes into another conversation. Files stay
  // in memory, but each Chat owns an independent resumable queue.
  const [attachmentDrafts, setAttachmentDrafts] = useState<Record<string, UFile[]>>({});
  const files = attachmentDrafts[draftKey] || [];
  const setFiles = useCallback((nextValue: SetStateAction<UFile[]>) => {
    setAttachmentDrafts(previous => {
      const current = previous[draftKey] || [];
      const next = typeof nextValue === "function" ? nextValue(current) : nextValue;
      const updated = { ...previous };
      if (next.length) updated[draftKey] = next;
      else delete updated[draftKey];
      return updated;
    });
  }, [draftKey]);
  const uploadControllersRef = useRef(new Map<string, AbortController>());
  const [dragOver, setDragOver] = useState(false);
  const dragDepthRef = useRef(0);
  const stopRef = useRef(false);
  const [activeTurn, setActiveTurn] = useState<ActiveTurn | null>(null);
  const activeTurnRef = useRef<ActiveTurn | null>(null);
  const interruptRequestedRef = useRef<string | null>(null);
  const [steeringBusy, setSteeringBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  // V103.90 回到底部按钮
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const onScrollArea = useCallback(() => {
    const el = scrollAreaRef.current; if (!el) return;
    setShowScrollBtn(el.scrollHeight - el.scrollTop - el.clientHeight > 240);   // 离底部 >240px 才显示
  }, []);
  const scrollToBottom = useCallback(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, []);
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const [attachmentMenu, setAttachmentMenu] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const lastQueryRef = useRef<{ query: string; sid: string } | null>(null);
  const isFirstMsgRef = useRef(false);
  // V103.90 会话内搜索（Ctrl/⌘+F）
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchIdx, setSearchIdx] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);

  const session = sessions.find(s => s.id === sid);
  const msgs = session?.messages || [];
  useEffect(() => {
    let active = true;
    setIncomingHandoff(null);
    if (!sid || !token) return () => { active = false; };
    void getIncomingChatContinuation(sid).then(async result => {
      let handoff = result.handoff;
      if (handoff?.status === "sealed") {
        try {
          handoff = (await transitionChatContinuation(sid, handoff.id, "claim")).handoff;
        } catch { /* the banner still shows the sealed read receipt */ }
      }
      if (active) setIncomingHandoff(handoff);
    }).catch(() => { if (active) setIncomingHandoff(null); });
    return () => { active = false; };
  }, [sid, token]);
  // V103.7 客户端加窗：超长会话默认只渲染最近 MSG_WINDOW 条消息，更早的折叠到按钮后，
  // 把 DOM 体积/渲染开销压下来（治超长对话卡顿）。切会话时重置为加窗。
  const [showAllMsgs, setShowAllMsgs] = useState(false);
  useEffect(() => { setShowAllMsgs(false); }, [sid]);
  useEffect(() => { syncTabs(); }, [sid, syncTabs]);   // V103.90 任何地方设了 sid → 自动开标签

  // V295 后台直播态选择器（提前声明——下面的镜像 effect 依赖它）：该会话在 store 里的实时快照。
  const liveHere = useStore(s => (sid ? s.liveStreams[sid] : undefined));

  // V295 后台直播镜像：当切回/重开一条「别处发起、仍在生成」的会话时，把全局 store 里的实时快照
  // 映射到本地渲染状态——这样重进立刻看到思考/正文/工具进度，而不是空白。发起端自己不镜像（本地
  // state 由 send 的 setState 维护，避免 250ms 快照抖动）。被镜像的会话直播结束（clearLive）时收起
  // 直播块（最终消息已由发起端 onDone 落库，重进走正常历史渲染）。这是"后台执行、重进可见"的核心。
  useEffect(() => {
    if (!sid) return;
    if (myStreamRef.current === sid) { mirroringRef.current = null; return; }  // 自己发起的：别碰本地 state
    if (liveHere && liveHere.streaming) {
      mirroringRef.current = sid;
      setStreaming(true);
      setStreamContent(liveHere.content || ""); streamContentRef.current = liveHere.content || "";
      const publicProgress = liveHere.thinking || "";
      setThinkingContent(publicProgress); thinkingRef.current = publicProgress;
      const tl = (liveHere.timeline || []) as typeof timelineRef.current;
      setLiveTimeline(tl); timelineRef.current = tl;
      setStreamSources((liveHere.sources || []) as unknown as Source[]);
      setTaskPlan((liveHere.plan as typeof taskPlan) || null);
      setTaskContract((liveHere.taskContract as TaskContract | null) || null);
      const td = (liveHere.todo || []) as unknown as TodoItem[];
      setTodoItems(td); todoRef.current = td;
      setLiveOrch(liveHere.orch || null); liveOrchRef.current = liveHere.orch || null;
      setAgentIteration((liveHere.iteration as typeof agentIteration) || null);
      setProgress((liveHere.progress as typeof progress) || null);
      const mirroredTurn = (liveHere.activeTurn || null) as ActiveTurn | null;
      if (mirroredTurn) {
        activeTurnRef.current = mirroredTurn; setActiveTurn(mirroredTurn);
      } else {
        void getActiveTurn(sid).then(result => {
          const turn = result.active ? result.turn : null;
          activeTurnRef.current = turn; setActiveTurn(turn);
        }).catch(() => {});
      }
    } else if (mirroringRef.current === sid) {
      // 之前在镜像这条会话，现在直播结束了 → 收起直播块（最终消息已落库，历史渲染接手）
      mirroringRef.current = null;
      setStreaming(false); setStreamContent(""); streamContentRef.current = "";
      setThinkingContent(""); thinkingRef.current = "";
      setLiveTimeline([]); timelineRef.current = [];
      setStreamSources([]); setTaskPlan(null); setTaskContract(null); setTodoItems([]); todoRef.current = [];
      todoManifestRef.current = null;
      setLiveOrch(null); liveOrchRef.current = null; setAgentIteration(null); setProgress(null);
      activeTurnRef.current = null; setActiveTurn(null);
    }
  }, [sid, liveHere]);

  // V103.90 会话内搜索：结果计算 + 定位导航
  const searchResult = useMemo(() => searchMessages(msgs, searchOpen ? searchQuery : ""), [msgs, searchQuery, searchOpen]);
  const activeHitMsgIdx = searchResult.flat[searchIdx]?.msgIndex ?? -1;
  useEffect(() => { setSearchIdx(0); }, [searchQuery]);
  const gotoHit = useCallback((flatIdx: number) => {
    const hit = searchResult.flat[flatIdx];
    if (!hit) return;
    const MSG_WINDOW = 40;
    if (!showAllMsgs && msgs.length > MSG_WINDOW && hit.msgIndex < msgs.length - MSG_WINDOW) setShowAllMsgs(true);  // 命中在折叠区→先展开
    setSearchIdx(flatIdx);
    setTimeout(() => { document.getElementById(`msg-${hit.msgIndex}`)?.scrollIntoView({ block: "center", behavior: "smooth" }); }, 30);
  }, [searchResult, showAllMsgs, msgs.length]);
  const stepSearch = useCallback((delta: number) => {
    const total = searchResult.flat.length;
    if (!total) return;
    gotoHit(stepHit(searchIdx, delta, total));
  }, [searchResult, searchIdx, gotoHit]);
  const closeSearch = useCallback(() => { setSearchOpen(false); setSearchQuery(""); setSearchIdx(0); }, []);
  // V103.1 / V295: 流式 UI 只在「正在生成的那个会话」里显示。切到别的会话时，原会话仍在后台跑、
  // 结果照常落库；回到该会话再看见它的实时进度。V295 起同时看全局直播态——即使本组件不是发起端
  // （用户切走再切回、或整页刷新后重进），只要该会话在 store 里仍标记 streaming，就照样显示实时进度。
  const streamingHere = (streaming && (streamingConvId === null || sid === streamingConvId)) || !!(liveHere && liveHere.streaming);

  // V308 批次四：滚动优化。原实现每次 streamContent 变化都 scrollIntoView({behavior:"smooth"})，
  // 流式时高频触发 → 多个平滑滚动动画互相覆盖、与重渲叠加造成卡顿；且用户想上滑看历史会被
  // 强行拽回底部。改为：仅当用户【本就停在底部附近】时才自动跟随；流式期间用瞬时滚动
  // （behavior:"auto"），不叠动画；非流式（新消息到达）才用平滑滚动。
  useEffect(() => {
    const sc = scrollAreaRef.current;
    const atBottom = !sc || (sc.scrollHeight - sc.scrollTop - sc.clientHeight < 120);
    if (!atBottom) return;   // 用户在上面看历史，不打断
    endRef.current?.scrollIntoView({ behavior: streaming ? "auto" : "smooth" });
  }, [msgs.length, streaming, streamContent]);
  const adjustHeight = useCallback(() => {
    const ta = textRef.current;
    if (!ta) return;
    ta.style.height = `${COMPOSER_MIN_HEIGHT}px`;
    if (!text) return;
    ta.style.height = "0px";
    ta.style.height = `${composerTextareaHeight(text, ta.scrollHeight)}px`;
  }, [text]);
  useEffect(() => { adjustHeight(); }, [text, adjustHeight]);

  useEffect(() => {
    function handlePaste(e: ClipboardEvent) {
      const items = e.clipboardData?.items; if (!items) return;
      for (let i = 0; i < items.length; i++) { if (items[i].kind === "file") { e.preventDefault(); const f = items[i].getAsFile(); if (f) processFile(f); return; } }
    }
    document.addEventListener("paste", handlePaste);
    return () => document.removeEventListener("paste", handlePaste);
  }, []);

  function handleDragEnter(e: React.DragEvent) {
    if (!Array.from(e.dataTransfer.items || []).some(item => item.kind === "file")) return;
    e.preventDefault();
    dragDepthRef.current += 1;
    setDragOver(true);
  }
  function handleDragOver(e: React.DragEvent) {
    if (!Array.from(e.dataTransfer.items || []).some(item => item.kind === "file")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }
  function handleDragLeave(e: React.DragEvent) {
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current > 0) return;
    setDragOver(false);
  }
  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    dragDepthRef.current = 0;
    setDragOver(false);
    const items = Array.from(e.dataTransfer.items || []);
    const entries = items.map(item => {
      const candidate = item as DataTransferItem & { webkitGetAsEntry?: () => DroppedEntry | null };
      return (candidate.webkitGetAsEntry?.() as unknown as DroppedEntry | null) || null;
    }).filter((entry): entry is DroppedEntry => Boolean(entry));
    try {
      if (entries.length) {
        const dropped = (await Promise.all(entries.map(entry => readDroppedEntry(entry)))).flat();
        for (const item of dropped) await processFile(item.file, { relativePath: item.relativePath });
        return;
      }
      const fl = e.dataTransfer.files;
      for (let i = 0; i < fl.length; i++) {
        await processFile(fl[i], { relativePath: fl[i].webkitRelativePath || fl[i].name });
      }
    } catch (error) {
      showToast((error as Error)?.message || "无法读取拖入的文件或目录", "error");
    }
  }

  async function processFile(
    f: globalThis.File,
    opts?: { skipAnalyze?: boolean; relativePath?: string },
  ) {
    if (!useStore.getState().token) { useStore.getState().set({ loginOpen: true }); return; }
    const relativePath = String(opts?.relativePath || f.webkitRelativePath || f.name).replace(/\\/g, "/");
    if (!relativePath || relativePath.startsWith("/") || relativePath.split("/").includes("..")) {
      showToast(`已拒绝不安全的附件路径：${f.name}`, "error");
      return;
    }
    if (f.size > 256 * 1024 * 1024) {
      showToast(`附件“${relativePath}”超过 256 MB 上限`, "error");
      return;
    }
    const fname = relativePath || f.name;
    // Composer selection is local-only. Exact bytes are uploaded once, to the
    // owner-scoped conversation resource endpoint, when the message is sent.
    // The old eager /api/upload call duplicated bytes into a shared legacy
    // directory and its extracted text was not authoritative for Chat anyway.
    const isImg = ["png","jpg","jpeg","gif","webp","bmp"].includes((fname.split(".").pop() || "").toLowerCase());
    const dataUrl = isImg ? URL.createObjectURL(f) : undefined;
    const ext = f.name.split(".").pop()?.toLowerCase() || "";
    const size = f.size > 1048576 ? `${(f.size / 1048576).toFixed(1)}M` : `${Math.round(f.size / 1024)}K`;
    setFiles(prev => {
      const duplicate = prev.some(item => item.relativePath === relativePath && item.sourceFile?.size === f.size && item.sourceFile?.lastModified === f.lastModified);
      if (duplicate) {
        if (dataUrl) URL.revokeObjectURL(dataUrl);
        showToast(`已忽略重复附件：${relativePath}`, "info");
        return prev;
      }
      if (prev.length >= 50) {
        if (dataUrl) URL.revokeObjectURL(dataUrl);
        showToast("单次消息最多添加 50 个附件", "error");
        return prev;
      }
      return [...prev, {
        id: typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `att-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        name: relativePath || f.name,
        size,
        ext,
        text: "",
        relativePath,
        dataUrl,
        sourceFile: f,
        state: "selected",
        uploadedBytes: 0,
        totalBytes: f.size,
      }];
    });
  }

  function removeFileAt(i: number) {
    setFiles(prev => {
      const f = prev[i];
      if (f) {
        uploadControllersRef.current.get(f.id)?.abort();
        uploadControllersRef.current.delete(f.id);
      }
      if (f?.dataUrl) URL.revokeObjectURL(f.dataUrl);
      return prev.filter((_, j) => j !== i);
    });
  }

  function retryFile(id: string) {
    setFiles(prev => prev.map(item => item.id === id ? {
      ...item, state: "selected", uploadedBytes: 0, error: undefined,
    } : item));
  }

  function patchFile(id: string, patch: Partial<UFile>) {
    setFiles(prev => prev.map(item => item.id === id ? { ...item, ...patch } : item));
  }

  async function uploadAttachmentBatch(convId: string, items: UFile[]): Promise<AttachmentReceipt[]> {
    const receipts: Array<AttachmentReceipt | undefined> = new Array(items.length);
    let cursor = 0;
    const worker = async () => {
      while (true) {
        const index = cursor++;
        if (index >= items.length) return;
        const item = items[index];
        if (item.receipt) { receipts[index] = item.receipt; continue; }
        const file = fileForConversationUpload(item);
        const controller = new AbortController();
        uploadControllersRef.current.get(item.id)?.abort();
        uploadControllersRef.current.set(item.id, controller);
        patchFile(item.id, { state: "uploading", uploadedBytes: 0, totalBytes: file.size, error: undefined });
        try {
          const receipt = await uploadConversationFileWithProgress(
            convId,
            file,
            (uploadedBytes, totalBytes) => patchFile(item.id, { uploadedBytes, totalBytes }),
            controller.signal,
          );
          receipts[index] = receipt;
          patchFile(item.id, {
            state: "uploaded", uploadedBytes: file.size, totalBytes: file.size,
            receipt, error: undefined,
          });
        } catch (error) {
          if ((error as Error)?.name !== "AbortError") {
            patchFile(item.id, { state: "failed", error: (error as Error)?.message || "附件上传失败" });
          }
        } finally {
          if (uploadControllersRef.current.get(item.id) === controller) {
            uploadControllersRef.current.delete(item.id);
          }
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(3, items.length) }, () => worker()));
    const failed = items.filter((_, index) => !receipts[index]);
    if (failed.length) throw new Error(`${failed.length} 个附件上传失败；已成功的附件会保留，修复后可重试`);
    return receipts as AttachmentReceipt[];
  }

  // V86: 问答栏截屏（视觉型 Computer Use 入口）——微信式框选+标注，截完直接成为附件。
  // hideSelf 让主窗在截屏瞬间隐身，不会把 HashMM 自己截进去。
  const [capturing, setCapturing] = useState(false);
  const [shotMenu, setShotMenu] = useState(false);   // V94: 截屏方式选择（微信式）
  // Legacy composer menus remain mounted for old deep links, but their visible
  // controls are retired in V601.  Stable Canvas/Collaboration workspaces now
  // live in the left navigation and return their results to this Chat.
  const [canvasMenu, setCanvasMenu] = useState(false);
  const canvasButtonRef = useRef<HTMLButtonElement>(null);
  const [canvasMenuPos, setCanvasMenuPos] = useState({ left: 0, bottom: 0 });
  const [myTpls, setMyTpls] = useState<{ id: string; name: string }[] | null>(null);
  const [orgTpls, setOrgTpls] = useState<{ id: string; name: string; by: string }[]>([]);
  const [mktTpls, setMktTpls] = useState<{ id: string; name: string; desc: string }[]>([]);
  // 协作是 Chat 的一种工作方式，而不是把用户送进另一套应用。
  // 面板挂在当前会话上，目标、资料和结果都沿用同一个 sid。
  const [teamOpen, setTeamOpen] = useState(false);
  const [selectedPluginIds, setSelectedPluginIds] = useState<string[] | undefined>(() => readSelectedPluginIds());
  const [capabilityPrefs, setCapabilityPrefs] = useState<Record<ChatCapability, boolean>>(() => readCapabilityPrefs());
  const [runtimeReady, setRuntimeReady] = useState<Set<string>>(new Set());
  useEffect(() => {
    let live = true;
    // Bootstrap can render before Supabase restores the access token. Calling
    // an authenticated capability endpoint in that window produced a noisy
    // 401 and cached a false "unavailable" state until the next reload.
    if (!token) {
      setRuntimeReady(new Set());
      return () => { live = false; };
    }
    runtimeCapabilities().then(payload => {
      if (!live) return;
      setRuntimeReady(new Set((payload.capabilities || []).filter(item => item.production_ready).map(item => item.id)));
    }).catch(() => { if (live) setRuntimeReady(new Set()); });
    return () => { live = false; };
  }, [token]);
  useEffect(() => {
    const sync = () => setCapabilityPrefs(readCapabilityPrefs());
    window.addEventListener(CAPABILITY_PREFS_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(CAPABILITY_PREFS_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  useEffect(() => onSelectedPluginIdsChanged(setSelectedPluginIds), []);
  const openCanvasMenu = useCallback((forceOpen = false) => {
    setCanvasMenu(value => forceOpen ? true : !value);
    if (forceOpen || !canvasMenu) {
      listCanvasTemplates().then(r => { setMyTpls(r.items || []); setOrgTpls(r.org_items || []); }).catch(() => { setMyTpls(null); setOrgTpls([]); });
      marketTemplates().then(r => setMktTpls(r.items || [])).catch(() => setMktTpls([]));
    }
  }, [canvasMenu]);
  // V344：右侧证据栏只负责准备复核任务，不越过用户自动发送。模式切换与
  // Chat 的真实 dispatch 状态复用，避免做出一个和 Agent 割裂的装饰按钮。
  useEffect(() => {
    if (!pendingRunMode) return;
    if ((pendingRunMode === "browser" || pendingRunMode === "computer" || pendingRunMode === "canvas" || pendingRunMode === "team")
      && !capabilityPrefs[pendingRunMode]) {
      showToast("这项能力已在设置中关闭", "info");
      useStore.getState().set({ pendingRunMode: "" });
      return;
    }
    if (pendingRunMode === "browser") {
      setBrowserMode(isDesktopEnv);
    } else if (pendingRunMode === "computer") {
      setCuMode(isDesktopEnv);
    } else if (pendingRunMode === "team") {
      setBrowserMode(false);
      setDeepMode(false);
      setCuMode(false);
      setTeamOpen(true);
    } else if (pendingRunMode === "canvas") {
      setBrowserMode(false);
      setDeepMode(false);
      setCuMode(false);
      openCanvasMenu(true);
    } else {
      setDeepMode(true);
    }
    useStore.getState().set({ pendingRunMode: "" });
    setTimeout(() => textRef.current?.focus(), 50);
  }, [pendingRunMode, isDesktopEnv, openCanvasMenu, capabilityPrefs]);
  // V267 模型快速切换（工具条）：加载我的模型列表 + 当前选用；点按钮轮换并当场 prefer。
  const [myModelList, setMyModelList] = useState<MyModel[]>([]);
  const [curModelId, setCurModelId] = useState("");
  useEffect(() => {
    if (!useStore.getState().token) return;
    myModels().then(r => { setMyModelList(r.models || []); setCurModelId(r.preferred || ""); })
      .catch(() => { /* 后端旧/未连：不显示切换器 */ });
  }, []);
  const curModelName = curModelId
    ? (myModelList.find(m => m.id === curModelId)?.name || "我的模型")
    : "系统默认";
  const cycleMyModel = async () => {
    const ids = ["", ...myModelList.map(m => m.id)];
    const next = ids[(ids.indexOf(curModelId) + 1) % ids.length];
    setCurModelId(next);   // 乐观更新
    try { await preferMyModel(next); } catch { setCurModelId(curModelId); }
  };
  const [slashHint, setSlashHint] = useState("");   // V258 /goal /loop 命令回执（几秒自动消失）
  useEffect(() => { if (slashHint) { const t = setTimeout(() => setSlashHint(""), 6000); return () => clearTimeout(t); } }, [slashHint]);
  const [replayOpen, setReplayOpen] = useState(false);   // V99: 操作回放审计面板
  async function captureScreen(hideSelf?: boolean) {
    // V103.15: 默认「直接截屏」（微信式：瞬间冻结当前屏，无隐藏等待、无弹层 gap）。
    // 旧默认是「隐藏窗口截屏」，会先隐藏 HashMM→等合成器重画（远程机更久）→才截，
    // 那段「实时桌面→冻结层」的可见过渡正是「截屏上叠截屏」的别扭感来源。
    // 需要截 HashMM 背后内容时，用户仍可在菜单选「隐藏窗口截屏」，偏好会被记住。
    if (hideSelf === undefined) hideSelf = ((typeof window !== "undefined" && localStorage.getItem("hmm_shot_hide")) ?? "0") === "1";
    const cu = getCU();
    if (!cu || capturing) return;
    setCapturing(true);
    try {
      const res = await cu.capture({ hideSelf });
      if (res?.ok && res.dataUrl) {
        const blob = await (await fetch(res.dataUrl)).blob();
        const t = new Date();
        const pad = (n: number) => String(n).padStart(2, "0");
        const name = `截屏-${pad(t.getHours())}${pad(t.getMinutes())}${pad(t.getSeconds())}.png`;
        await processFile(new File([blob], name, { type: "image/png" }), { skipAnalyze: true });
        textRef.current?.focus();
      }
    } catch (_e) { /* 取消/异常都安静返回 */ }
    setCapturing(false);
  }

  const [progress, setProgress] = useState<{ stage: string; pct: number; msg: string } | null>(null);
  const streamSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const streamTokenCount = useRef(0);
  const streamStartTime = useRef(0);
  const streamContentRef = useRef("");
  // ── V308 批次四：逐 token 重渲染性能优化 ──
  // 原实现每收到一个 token 就 setStreamContent(prev=>prev+t)——每 token 触发一次
  // React 重渲 + Markdown 重解析 + 平滑滚动动画；长对话下 CPU/内存持续上升、界面卡顿。
  // 改为：token 先进 _streamBuf 缓冲区，用 requestAnimationFrame【每帧最多提交一次】
  // setState（约 60fps 上限，与浏览器绘制节奏对齐），把 N 次重渲合并成每帧 1 次。
  const _streamBuf = useRef("");           // 待提交的累积文本
  const _rafId = useRef<number | null>(null);
  const _flushStream = useRef<() => void>(() => {});

  // V295 后台直播镜像所需的 refs（reader loop 每次回调更新，250ms 定时器把快照推进全局 store）：
  const thinkingRef = useRef("");            // 思考累积
  const liveMetaRef = useRef<Record<string, unknown>>({});  // plan/sources/progress/iteration/orch/todo 低频快照
  const myStreamRef = useRef<string | null>(null);          // 本组件正在直播的会话（区分"自己发起" vs "切回别处发起的流"）
  const liveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);  // 直播快照定时器
  const mirroringRef = useRef<string | null>(null);  // 本组件正在"镜像"的别处发起的会话直播（结束时据此收起直播块）

  async function steerCurrentTurn(content?: string) {
    const convId = sid;
    const steerText = (content ?? text).trim();
    if (!convId || (!steerText && files.length === 0) || steeringBusy) return;
    setSteeringBusy(true);
    try {
      let turn = activeTurnRef.current;
      if (!turn || turn.conversation_id !== convId) {
        const current = await getActiveTurn(convId);
        turn = current.active ? current.turn : null;
        activeTurnRef.current = turn; setActiveTurn(turn);
      }
      if (!turn) {
        showToast("当前任务正在建立运行通道，请稍后再发送", "info");
        return;
      }
      if (!turn.steerable) {
        showToast("当前任务已进入收尾，追加要求请作为下一条消息发送", "info");
        return;
      }
      const clientMessageId = typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID() : `steer-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const attachmentReceipts = await uploadAttachmentBatch(convId, files);
      const result = await steerActiveTurn(
        convId,
        turn.turn_id,
        steerText,
        clientMessageId,
        attachmentReceipts.map(item => ({
          filename: item.filename,
          size: item.size,
          sha256: item.sha256,
          download_url: item.download_url,
        })),
      );
      if (result.accepted && !result.duplicate) {
        addMsg(convId, {
          id: result.message_id,
          role: "user",
          content: steerText || "请继续处理这些附件",
          ts: Date.now(),
          ...(attachmentReceipts.length ? {
            files: attachmentReceipts.map(item => ({
              filename: item.filename,
              download_url: item.download_url,
            })),
          } : {}),
        } as never);
      }
      setText("");
      files.forEach(item => { if (item.dataUrl) URL.revokeObjectURL(item.dataUrl); });
      setFiles([]);
      if (textRef.current) textRef.current.style.height = `${COMPOSER_MIN_HEIGHT}px`;
      showToast(
        attachmentReceipts.length
          ? `已把要求和 ${attachmentReceipts.length} 个附件加入当前任务`
          : "已加入当前任务，Agent 将按新要求继续",
        "success",
      );
    } catch (error) {
      showToast((error as Error)?.message || "追加要求失败", "error");
      if (convId) {
        void getActiveTurn(convId).then(result => {
          const turn = result.active ? result.turn : null;
          activeTurnRef.current = turn; setActiveTurn(turn);
        }).catch(() => {});
      }
    } finally {
      setSteeringBusy(false);
    }
  }

  async function interruptCurrentTurn() {
    const convId = sid;
    if (!convId) return;
    if (interruptRequestedRef.current === convId) return;
    interruptRequestedRef.current = convId;
    let turn = activeTurnRef.current;
    try {
      showToast("正在停止并保存当前进度", "info");
      setProgress(prev => ({
        stage: "interrupting",
        pct: prev?.pct || 0,
        msg: "正在停止并保存当前进度",
      }));
      // `turn_started` and the visible stream do not necessarily arrive in the
      // same browser task.  Give the owner-scoped control plane a short chance
      // to expose the turn instead of disconnecting the SSE first and racing
      // the server into a misleading `client_disconnected` result.
      for (let attempt = 0;
        attempt < 8 && (!turn || turn.conversation_id !== convId);
        attempt += 1) {
        const current = await getActiveTurn(convId);
        turn = current.active ? current.turn : null;
        if (!turn && attempt < 7) {
          await new Promise(resolve => setTimeout(resolve, 125));
        }
      }
      if (turn) {
        const next = { ...turn, status: "interrupting" as const, steerable: false };
        activeTurnRef.current = next; setActiveTurn(next);
        // Persist the interrupt intent before allowing the local reader to
        // close.  The endpoint is idempotent, so repeated Esc/clicks are safe.
        await interruptActiveTurn(convId, turn.turn_id);
      } else {
        showToast("运行通道尚未建立，已停止本地等待", "warning");
      }
    } catch (error) {
      showToast((error as Error)?.message || "停止请求未被服务器确认，已停止本地等待", "warning");
    } finally {
      // Local browser/computer branches do not have a backend active turn; the
      // reader still needs an immediate escape hatch in that case.
      stopRef.current = true;
    }
  }

  async function reconcileConversationAfterStop(convId: string): Promise<void> {
    // `onCancelled` is emitted by the local SSE reader.  It is not authoritative:
    // the server may still be finalising the interrupted manifest. Poll briefly
    // and replace the optimistic partial with the owner-checked persisted row.
    for (let attempt = 0; attempt < 7; attempt += 1) {
      try {
        const data = await loadConversation(convId);
        const raw = (data.messages || []) as Message[];
        const stillRunning = raw.some(message => message.status === "streaming");
        if (stillRunning && attempt < 6) {
          await new Promise(resolve => setTimeout(resolve, 250 + attempt * 150));
          continue;
        }
        const mapped: Message[] = raw.map(message => {
          const process = message.run_manifest?.process;
          return {
            ...message,
            ts: message.created_at ? message.created_at * 1000 : (message.ts || Date.now()),
            // Persist only the user-visible process contract. Raw provider
            // reasoning is neither stable UI state nor safe to replay after
            // an interrupted turn.
            thinking: undefined,
            timeline: message.timeline?.length ? message.timeline : process?.timeline,
            todo: message.todo?.length ? message.todo : process?.todo,
          };
        });
        const current = useStore.getState().sessions;
        if (current.some(item => item.id === convId)) {
          useStore.setState({
            sessions: current.map(item => item.id === convId
              ? {
                  ...item,
                  title: data.conversation?.title || item.title,
                  updated_at: (data.conversation?.updated_at || 0) * 1000 || Date.now(),
                  messages: mapped,
                }
              : item),
          });
          persistSessions();
        }
        return;
      } catch {
        if (attempt < 6) {
          await new Promise(resolve => setTimeout(resolve, 250 + attempt * 150));
        }
      }
    }
  }

  async function ensureWorkspaceConversation(title: string): Promise<string> {
    if (sid) return sid;
    const conversationId = "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
    const owner = useStore.getState().user;
    const projectId = readActiveProject(owner);
    const now = Date.now();
    await createConversation(conversationId, title, projectId || undefined);
    addSession({
      id: conversationId,
      title,
      messages: [],
      created: now,
      updated_at: now,
      project_id: projectId || undefined,
    });
    set({ sid: conversationId });
    if (typeof window !== "undefined") {
      try { history.pushState({}, "", `/chat/${conversationId}`); } catch {}
    }
    return conversationId;
  }

  async function send(q?: string, regenerate = false) {
    // V93: 游客点发送 → 弹登录（Marvis 式），不打断输入内容
    if (!useStore.getState().token) { set({ loginOpen: true }); return; }
    // V295: 生成中不发送——但改为**按当前会话**判断。以前用全局 streaming 标志，导致"A 会话在生成时，
    // 切到 B 会话也不能发"；现在只拦"当前这条会话自己正在生成"，从而支持"A 在后台跑，同时在 B 里
    // 继续提问"（多会话并发）。当前会话的生成态优先看全局直播态（跨组件、跨重进都准）。
    const _curSid = sid;
    const _curLive = _curSid ? useStore.getState().liveStreams[_curSid] : undefined;
    const query = q || text.trim();
    if (_curLive?.streaming || (streaming && myStreamRef.current === _curSid)) {
      if (query || files.length > 0) await steerCurrentTurn(query);
      return;
    }
    if (!query && files.length === 0) return;
    // 本轮显式选中的功能上下文快照。成功 done 后按 id 消费；失败时保留，用户可直接重试。
    const featureContextSnapshot = regenerate ? [] : [...useStore.getState().featureContexts];
    // The document library attaches an explicit machine-readable scope. A
    // composer selection wins; otherwise use the attached library selection.
    // Never reconstruct scope from the natural-language prompt.
    const effectiveDocFilter = docFilter.length
      ? [...docFilter]
      : documentNamesFromContexts(featureContextSnapshot);
    // V269 离线守卫：后端未连接时发送必失败——直接给可行动提示，不留半截空气泡。
    // 历史与云端记录仍可查看；顶部横幅可一键重试连接。
    if (useStore.getState().backendOnline === false) {
      showToast("后端未连接：发送消息需后端在线（历史与云端记录可离线查看）", "warning");
      return;
    }
    // ── V258 循环工程 slash 命令（对齐 Claude Code /goal 与 /loop）───────────
    //   /goal 目标文字 [次数]      → 目标循环：执行→评估→打回重做，达标或次数用尽即止
    //   /loop 分钟 巡检任务        → 时间循环：每 N 分钟自动执行一次（≥5 分钟）
    //   /schedule 分钟 例行任务    → V269：/loop 的"云端例程"别名——本项目的循环本就
    //                                跑在常驻后端（关掉客户端也继续），语义与官方 /schedule 一致
    // 创建成功后提示去总控中枢看进度；循环完成/失败会经事件自动化推系统通知。
    // V272 /persona 专家人格：/persona 列出可选；/persona 验收员 启用；/persona off 还原
    if (/^\/persona(\s|$)/i.test(query)) {
      const arg = query.replace(/^\/persona/i, "").trim();
      try {
        if (!arg) { setSlashHint("可用人格：" + Object.keys(PERSONAS).join(" / ") + "。用法：/persona 验收员，/persona off 还原"); setText(""); return; }
        if (!sid) { setSlashHint("先发一条消息建立会话，再设置人格"); return; }
        if (/^(off|关闭|还原)$/i.test(arg)) {
          await sessionPatch(sid, { system_append: "" });
          setSlashHint("已还原默认人格（下一轮生效）");
        } else {
          const p = PERSONAS[arg] || Object.values(PERSONAS).find(x => x.label === arg);
          if (!p) { setSlashHint("没有这个人格。可用：" + Object.keys(PERSONAS).join(" / ")); return; }
          await sessionPatch(sid, { system_append: p.sys });
          setSlashHint(`已切换人格「${p.label}」（仅本会话，下一轮生效；/persona off 还原）`);
        }
        setText("");
      } catch (e) { setSlashHint("人格设置失败：" + ((e as Error).message || "")); }
      return;
    }
    if (/^\/(goal|loop|schedule)\s/i.test(query)) {
      try {
        if (query.toLowerCase().startsWith("/goal")) {
          const m = query.slice(5).trim().match(/^([\s\S]*?)(?:\s+(\d{1,2}))?$/);
          const g = (m?.[1] || "").trim();
          if (!g) { setSlashHint("用法：/goal 目标文字 [最大轮数≤8]"); return; }
          await loopGoal({ goal: g, max_rounds: Number(m?.[2]) || 4, conv_id: sid || undefined });
          setSlashHint("目标循环已启动——进度看「总控中枢 · 循环工程」，达标/未达成会推通知");
        } else {
          const isSchedule = query.toLowerCase().startsWith("/schedule");
          const m = query.slice(isSchedule ? 9 : 5).trim().match(/^(\d{1,3})\s+([\s\S]+)$/);
          if (!m) { setSlashHint(`用法：/${isSchedule ? "schedule" : "loop"} 间隔分钟(≥5) ${isSchedule ? "例行任务" : "巡检任务"}`); return; }
          await loopInterval({ prompt: m[2].trim(), interval_min: Number(m[1]) || 30 });
          setSlashHint(isSchedule
            ? "例程已启动——在服务器常驻执行（关掉客户端也继续），进度看「总控中枢 · 循环工程」"
            : "时间循环已启动——进度看「总控中枢 · 循环工程」");
        }
        setText("");
      } catch (e) { setSlashHint("循环创建失败：" + ((e as Error)?.message || "并发上限 5 或后端版本过旧")); }
      return;
    }
    const currentFiles = regenerate ? [] : [...files];
    let id: string | null = sid;
    const owner = useStore.getState().user;
    const selectedProjectId = readActiveProject(owner);
    let attachmentReceipts: AttachmentReceipt[] = [];
    try {
      if (!id) {
        id = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
        const title = query?.slice(0, 28) || "新对话";
        // Persistence first: the UI must not claim that a conversation exists
        // until the owner-checked server row and its project relationship do.
        await createConversation(id, title, selectedProjectId || undefined);
        const now = Date.now();
        addSession({
          id,
          title,
          messages: [],
          created: now,
          updated_at: now,
          project_id: selectedProjectId || undefined,
        });
        isFirstMsgRef.current = true;
      } else if (selectedProjectId) {
        // A conversation that already belongs to a project must not be moved
        // merely because the sidebar selection changed.  Only classify an
        // unassigned conversation into the explicitly selected project.
        const localSession = useStore.getState().sessions.find(item => item.id === id);
        if (localSession && !localSession.project_id) {
          await assignConversationProject(id, selectedProjectId);
          useStore.setState(state => ({
            sessions: state.sessions.map(item => item.id === id
              ? { ...item, project_id: selectedProjectId }
              : item),
          }));
          persistSessions();
        }
      }

      if (!regenerate) {
        attachmentReceipts = await uploadAttachmentBatch(id, currentFiles);
        addMsg(id, {
          role: "user",
          content: query || "请分析上传的文件",
          ts: Date.now(),
          ...(attachmentReceipts.length ? {
            files: attachmentReceipts.map(item => ({
              filename: item.filename,
              download_url: item.download_url,
              ...(item.resource || {}),
            })),
          } : {}),
        });
        setText("");
        if (textRef.current) textRef.current.style.height = `${COMPOSER_MIN_HEIGHT}px`;
        currentFiles.forEach(item => {
          if (item.dataUrl) URL.revokeObjectURL(item.dataUrl);
        });
        setFiles([]);
      }
    } catch (error) {
      showToast(
        `消息尚未发送：${(error as Error)?.message || "无法持久化对话或附件"}`,
        "error",
        6000,
      );
      return;
    }
    const conversationId = id;
    if (typeof window !== "undefined") {
      try { history.pushState({}, "", `/chat/${conversationId}`); } catch (_e) {}
    }
    // 截屏类附件 text 为空（analyze=0），不进文件上下文——定向解读由后端 vision 步骤做
    const fileCtx = currentFiles.map(f => f.text).filter(t => t && t.trim()).join("\n\n---\n\n") || null;
    const attachmentNames = attachmentReceipts.length
      ? attachmentReceipts.map(item => item.filename)
      : currentFiles.map(f => f.name);
    const sendQuery = query || "请分析上传的文件";
    lastQueryRef.current = { query: sendQuery, sid: conversationId };
    setStreaming(true); setStreamContent(""); setTraceSteps([]); traceRef.current = []; setThinkingContent(""); setTaskPlan(null); setTaskContract(null);
    setAgentSteps([]); agentStepsRef.current = []; setCreatedFiles([]); setAgentIteration(null); setProgress(null); setStreamSources([]);
    setLiveTimeline([]); timelineRef.current = [];
    setTodoItems([]); todoRef.current = []; todoManifestRef.current = null;
    clarifyRef.current = null;
    setLiveOrch(null); liveOrchRef.current = null;
    setLiveDelta(""); setCtxInfo(null);
    activeTurnRef.current = null; setActiveTurn(null);
    useStore.getState().set({ artifactDraft: null });
    stopRef.current = false; set({ loading: true, streamingConvId: conversationId });
    _srSrc = ""; _srHtml = "";   // V308：重置流式渲染缓存（配合新 safeRenderStream）
    _streamBuf.current = "";     // V308：重置 rAF 流式缓冲
    if (_rafId.current != null) { cancelAnimationFrame(_rafId.current); _rafId.current = null; }
    streamContentRef.current = "";
    thinkingRef.current = ""; liveMetaRef.current = {}; myStreamRef.current = conversationId;
    const _stopLive = () => { if (liveTimerRef.current) { clearInterval(liveTimerRef.current); liveTimerRef.current = null; } };
          streamTokenCount.current = 0;
          streamStartTime.current = Date.now();

    // V340 共享浏览器主链：用户不用离开 Chat 去“共享浏览器”页面手工派活。
    // Chat 负责会话/轮次，desktop dispatch 负责本机 AgentLoop + Electron 浏览器，过程与
    // 结果仍由 runner 回帖同一个 conv_id。独立浏览器页只保留作共享视图/策略/QA 控制面。
    if (browserMode && isDesktopEnv) {
      const timelineId = `tl-browser-${Date.now()}`;
      try {
        const queued = await createDispatch("desktop", "browser_use", {
          goal: sendQuery,
          conv_id: id!,
          source_chat: true,
          evidence_schema: "hashmm.browser-trajectory.v1",
          work_method: {
            retrieval: retrievalMode,
            effort: effortMode,
            run_mode: "browser",
          },
          feature_contexts: featureContextSnapshot.map(x => ({
            kind: x.kind, title: x.title, content: x.content, source: x.source,
            document_names: x.document_names,
          })),
        });
        if (featureContextSnapshot.length) {
          useStore.getState().consumeFeatureContexts(featureContextSnapshot.map(x => x.id));
        }
        const initial = [{ kind: "tool", node: "browser", tool: "browser_use", detail: `已入队 ${queued.task_id}`,
          status: "running" as const, id: timelineId }];
        setLiveTimeline(initial); timelineRef.current = initial;
        setProgress({ stage: "browser", pct: 8, msg: "共享浏览器 Agent 已入队，等待桌面 runner…" });
        // Browser Use stays inside the same Chat workspace. The detached
        // cockpit remains an explicit diagnostics surface, not an automatic
        // context switch for every task.
        const currentBrowser = useStore.getState().browserPanel;
        if (currentBrowser?.url) openBrowserInInspector(currentBrowser.url, currentBrowser.title);
        else openBrowserInInspector("https://www.bing.com/");

        let finalStatus = "";
        const terminal = (s: string) => ["done", "failed", "dead"].includes(s);
        const syncBrowserConversation = async () => {
          try {
            const loaded = await loadConversation(id!);
            const current = useStore.getState();
            const sessions = current.sessions.map(s => s.id === id ? { ...s, messages: loaded.messages || [] } : s);
            current.set({ sessions });
            persistSessions();
            if (typeof window !== "undefined") window.dispatchEvent(new Event("conv-file-created"));
          } catch { /* 服务器仍是权威来源，重开会话会再次加载 */ }
        };
        for (let i = 0; i < 30; i++) { // 前台只等约 1 分钟；更长任务转入可见的后台直播
          if (stopRef.current) break;
          const task = await getDispatch(queued.task_id);
          finalStatus = task.status;
          const pct = task.status === "claimed" ? Math.min(90, 22 + i) : task.status === "pending" ? 12 : 96;
          setProgress({ stage: "browser", pct, msg: task.status === "claimed" ? "共享浏览器正在执行，真实动作会显示在 Cockpit…" : `浏览器任务：${task.status}` });
          if (terminal(task.status)) break;
          await new Promise(resolve => setTimeout(resolve, 2000));
        }

        // runner 的权威过程/结果已经写入后端会话；短任务立即同步，长任务
        // 转到全局 liveStreams 继续轮询。这样切换会话、关闭面板甚至重新挂载
        // ChatArea 后，用户仍能看到同一任务的状态，完成时结果自动回帖。
        if (terminal(finalStatus)) await syncBrowserConversation();
        else if (!stopRef.current) {
          const conv = id!;
          useStore.getState().pushLive(conv, {
            streaming: true, content: "", thinking: "",
            timeline: initial, sources: [], plan: null, todo: [], orch: null,
            iteration: null, progress: { stage: "browser", pct: 12, msg: "浏览器任务已转入后台，结果会回到本对话。" },
            query: sendQuery, startedAt: Date.now(),
          });
          void (async () => {
            let last = finalStatus;
            for (let i = 0; i < 900; i++) { // 最多后台观察 30 分钟，队列本身不在此处被取消
              try {
                const task = await getDispatch(queued.task_id);
                last = task.status;
                useStore.getState().pushLive(conv, {
                  streaming: true,
                  timeline: [{ ...initial[0], detail: `浏览器任务 ${task.status}`, status: terminal(task.status) ? "done" : "running" }],
                  progress: { stage: "browser", pct: terminal(task.status) ? 100 : Math.min(94, 14 + Math.floor(i / 8)), msg: terminal(task.status) ? "浏览器任务已结束，正在同步结果…" : `浏览器任务：${task.status}` },
                });
                if (terminal(task.status)) {
                  await syncBrowserConversation();
                  useStore.getState().clearLive(conv);
                  return;
                }
              } catch { /* 单次网络失败不丢任务，下一轮继续 */ }
              await new Promise(resolve => setTimeout(resolve, 2000));
            }
            // 超过观察窗口只收起前端直播；任务不会被误报为成功或失败，
            // 服务端完成后仍会在下次打开会话时显示权威结果。
            if (last && !terminal(last)) useStore.getState().clearLive(conv);
          })();
        }

        if (stopRef.current) {
          addMsg(id!, { role: "assistant", content: "已停止在当前界面等待；桌面浏览器任务已进入持久队列，若已经被 runner 认领，它仍可能继续执行，结果会回到本对话。", ts: Date.now() });
        } else if (!terminal(finalStatus)) {
          addMsg(id!, { role: "assistant", content: "浏览器任务仍在后台队列执行。结果会继续回到本对话；可打开“共享浏览器”查看实时页面证据。", ts: Date.now() });
        }
      } catch (e) {
        addMsg(id!, { role: "assistant", content: "[共享浏览器派活失败] " + ((e as Error)?.message || e), ts: Date.now() });
      }
      setStreaming(false); setStreamContent(""); setProgress(null); streamContentRef.current = "";
      set({ loading: false, streamingConvId: null }); myStreamRef.current = null;
      return;
    }

    // V92: 电脑操作模式 —— LLM 决策走后端，工具在本机执行（hashmmCU），
    // 步骤实时打进现有流式区，完成后整段落库（cu_save）。
    if (cuMode && isDesktopEnv) {
      try {
        const localToolQuery = sendQuery + featureContextsForLocalTool(featureContextSnapshot);
        setProgress({ stage: "computer", pct: 5, msg: "电脑操作已读取本轮目标与已附加面板上下文…" });
        const full = await runComputerUse(localToolQuery, {
          taskId: id!,
          // CU runs locally, outside the backend context manager. Forward a
          // bounded, truthful excerpt so a long Chat still retains the original
          // goal and the latest turns without growing the tool prompt forever.
          history: buildToolHistory(msgs),
          onText: (t) => {
            if (stopRef.current) return;
            _streamBuf.current += t; streamContentRef.current = _streamBuf.current;
            if (_rafId.current == null) {
              _rafId.current = requestAnimationFrame(() => {
                _rafId.current = null;
                if (!stopRef.current) setStreamContent(_streamBuf.current);
              });
            }
          },
          onStep: (s: CuStep) => {                       // V94: 工具步骤进统一时间线卡片
            setLiveTimeline(prev => {
              if (s.status !== "running") {
                const idx = [...prev].reverse().findIndex(t => t.node === s.node && t.status === "running");
                if (idx >= 0) {
                  const real = prev.length - 1 - idx;
                  const next = prev.slice(); next[real] = { ...next[real], status: s.status };
                  timelineRef.current = next; return next;
                }
              }
              const next = [...prev, { kind: "tool", node: s.node, detail: s.detail,
                                       status: s.status, id: `tl-cu-${prev.length}-${Date.now()}` }];
              timelineRef.current = next; return next;
            });
          },
          shouldStop: () => stopRef.current,
        });
        const finalText = (full || streamContentRef.current || "").trim() || "（电脑操作无输出）";
        addMsg(id!, { id: `cu-${Date.now()}`, role: "assistant", content: finalText, created_at: Date.now() / 1000 } as never);
        cuSave(id!, sendQuery, finalText, {
          retrieval: retrievalMode,
          effort: effortMode,
          run_mode: "computer",
        }).catch(() => { /* 落库失败不打断 */ });
        if (featureContextSnapshot.length) {
          useStore.getState().consumeFeatureContexts(featureContextSnapshot.map(x => x.id));
        }
      } catch (e) {
        const errText = "[电脑操作失败] " + ((e as Error)?.message || e);
        addMsg(id!, { id: `cu-${Date.now()}`, role: "assistant", content: errText, created_at: Date.now() / 1000 } as never);
      }
      setStreaming(false); setStreamContent(""); setProgress(null); streamContentRef.current = "";
      set({ loading: false, streamingConvId: null });
      return;
    }

    // v10: Use new conversation endpoint (falls back to old if server doesn't support it)
    const useV10 = true;
    // V295 后台直播：把这条会话标记为"正在生成"并挂进全局 store；启动 250ms 定时器把 reader loop
    // 累积到 refs 的实时快照（正文/思考/时间线 + 低频元信息）持续推进 store——即使用户切走导致本
    // 组件卸载，定时器与 store 写入也照常进行（reader loop 是脱离组件生命周期的 async 循环）。切回
    // /重开时 ChatArea 直接从 store 读到最新进度，不再是空白等半天。done/error 时停表并清直播态。
    useStore.getState().pushLive(id!, { streaming: true, content: "", thinking: "", timeline: [],
      sources: [], plan: null, todo: [], orch: null, iteration: null, progress: null,
      query: sendQuery, startedAt: Date.now() });
    if (liveTimerRef.current) clearInterval(liveTimerRef.current);
    liveTimerRef.current = setInterval(() => {
      try {
        useStore.getState().pushLive(id!, {
          content: streamContentRef.current,
          // Never mirror or persist hidden model reasoning. `thinkingRef`
          // contains only our fixed public status, never provider deltas.
          thinking: thinkingRef.current,
          timeline: timelineRef.current.map(e => ({ ...e })),
          ...(liveMetaRef.current as object),
        });
      } catch { /* 快照失败不影响主流程 */ }
    }, 250);
    const streamFn = useV10
      ? (cb: Parameters<typeof chatStreamV10>[3]) => chatStreamV10(
          id!, sendQuery, fileCtx, cb, effectiveDocFilter, retrievalMode, attachmentNames, effortMode,
          featureContextSnapshot, deepMode ? "deep" : "auto",
          browserMode ? "browser" : deepMode ? "deep" : cuMode ? "computer" : "auto",
          readSelectedPluginIds(),
        )
      : (cb: Parameters<typeof chatStream>[3]) => chatStream(sendQuery, id, fileCtx, cb);

    await streamFn({
      // The stop control must abort the local SSE reader as well as request a
      // best-effort server interrupt.  Server-side interruption can arrive
      // late (or the turn id may not have been emitted yet), but the user
      // should still see an immediate, durable "interrupted" result.
      shouldStop: () => stopRef.current,
      onTurnStarted: (turn) => {
        activeTurnRef.current = turn; setActiveTurn(turn);
        liveMetaRef.current.activeTurn = turn;
      },
      onTurnState: (turn) => {
        activeTurnRef.current = turn; setActiveTurn(turn);
        liveMetaRef.current.activeTurn = turn;
      },
      onSteerApplied: () => {
        showToast("追加要求已生效", "success");
      },
      onPlan: (plan) => { setTaskPlan(plan); liveMetaRef.current.plan = plan; },
      onTaskContract: (contract) => { setTaskContract(contract); liveMetaRef.current.taskContract = contract; },
      onTrace: (data: TraceStep[]) => {
        if (Array.isArray(data) && data.length > 0) {
          setTraceSteps(prev => { const next = [...prev, ...data]; traceRef.current = next; return next; });
          // 同步写入统一有序事件流（按到达顺序，与工具事件交错）
          setLiveTimeline(prev => {
            const adds = data.map((t, i) => ({
              kind: "trace", node: (t as { node?: string }).node || "classify",
              detail: (t as { detail?: string }).detail || "",
              status: "done" as const, id: `tl-trace-${prev.length + i}-${Date.now()}`,
            }));
            const next = [...prev, ...adds]; timelineRef.current = next; return next;
          });
        }
      },
      onToken: (token) => {
        setLiveDelta("");
        if (stopRef.current) return;
        const cleanToken = sanitizeLLMOutput(token);
        if (!cleanToken) return;
        // V308 批次四：token 入缓冲区，rAF 每帧合并提交一次（不再每 token setState）
        _streamBuf.current += cleanToken;
        streamContentRef.current = _streamBuf.current;
        if (_rafId.current == null) {
          _rafId.current = requestAnimationFrame(() => {
            _rafId.current = null;
            if (stopRef.current) return;
            setStreamContent(_streamBuf.current);   // 每帧 1 次重渲，合并本帧内所有 token
          });
        }
      },
      onThinking: () => {
        // Providers may emit private reasoning deltas. Do not expose or store
        // them. Show one stable, auditable public status instead.
        const publicStatus = "正在分析任务并选择下一步";
        thinkingRef.current = publicStatus;
        setThinkingContent(publicStatus);
        setLiveTimeline(prev => {
          if (prev.some(event => event.id === "tl-public-analysis")) return prev;
          const next = [...prev, {
            kind: "status",
            node: "analysis",
            detail: "正在分析任务并选择下一步",
            status: "running" as const,
            id: "tl-public-analysis",
          }];
          timelineRef.current = next;
          return next;
        });
      },
      onTodo: (data) => {
        const items = (data.items || []) as TodoItem[];
        const incoming = normalizeTodoManifestVersion(data);
        const current = todoManifestRef.current;
        // SSE reconnects can replay earlier snapshots.  Once a versioned
        // manifest is observed, unversioned or older/different manifests must
        // not roll the visible checklist backwards.
        if (!shouldAcceptTodoManifest(current, incoming)) return;
        if (incoming) todoManifestRef.current = incoming;
        setTodoItems(items);
        todoRef.current = items;
        liveMetaRef.current.todo = items;
      },
      onDelta: (data) => { if (data.t) setLiveDelta(prev => prev + data.t); },
      onDeltaCommit: () => setLiveDelta(""),
      onFileDelta: (d) => {
        // V55: 右栏逐字写代码——草稿模式直播，权威 file 事件到达后无缝切换为真实预览
        // V215: .html 草稿按 html 路由 → 工作画布（work-canvas）在右栏边生成边渲染，而不是只当代码看
        const st = useStore.getState();
        const prev = st.artifactDraft;
        const content = prev && prev.filename === d.filename ? prev.content + d.t : d.t;
        const draftType = /\.html?$/i.test(d.filename || "") ? "html" : "code";
        st.set({
          rightPanelOpen: true,
          inspectorTab: "artifact",
          artifactDraft: { filename: d.filename, content },
          artifactPanel: { convId: sid || "", type: draftType, filename: d.filename, download_url: "" },
        });
      },
      onCtx: (d) => setCtxInfo(d),
      onStepStart: (step) => {
        setAgentSteps(prev => {
          const next = [...prev, { ...step, status: "running" }];
          agentStepsRef.current = next;
          return next;
        });
        setLiveTimeline(prev => {
          const next = [...prev, {
            kind: "tool", node: "tool", tool: step.tool,
            detail: step.detail || "",
            status: "running" as const,
            id: step.id || `tl-tool-${prev.length}-${Date.now()}`,
          }]; timelineRef.current = next; return next;
        });
      },
      onStepDone: (step) => {
        setAgentSteps(prev => {
          const idx = prev.findIndex(s => step.id ? s.id === step.id : s.tool === step.tool && s.status === "running");
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = { ...next[idx], ...step };
            agentStepsRef.current = next;
            return next;
          }
          const next = [...prev, step];
          agentStepsRef.current = next;
          return next;
        });
        // V49: 用后端配对 id 把"运行中"原地更新为完成/失败（denied 也按失败标红）
        const doneStatus: "done" | "error" =
          (step.status === "error" || step.status === "denied") ? "error" : "done";
        setLiveTimeline(prev => {
          const idx = [...prev].reverse().findIndex(e => e.kind === "tool" && e.status === "running"
            && (step.id ? e.id === step.id : (e.tool || e.detail.split(/[:(（]/)[0].trim()) === step.tool));
          if (idx >= 0) {
            const realIdx = prev.length - 1 - idx;
            const next = [...prev];
            next[realIdx] = { ...next[realIdx], status: doneStatus,
              detail: step.detail || next[realIdx].detail, elapsed_ms: step.duration_ms,
              hooks: step.hooks };
            timelineRef.current = next; return next;
          }
          // 找不到对应 running 项（极端乱序）→ 兜底追加，保证事件不丢
          const next = [...prev, {
            kind: "tool", node: "tool", tool: step.tool, detail: step.detail || "",
            status: doneStatus, elapsed_ms: step.duration_ms,
            id: step.id || `tl-tool-${prev.length}-${Date.now()}`, hooks: step.hooks,
          }]; timelineRef.current = next; return next;
        });
      },
      onFile: (file) => {
        setCreatedFiles(prev => [...prev, file]);
        // v10: Notify file panel
        if (typeof window !== "undefined") window.dispatchEvent(new Event("conv-file-created"));
        // V50: Claude 式"边生成边看"——agent 每生成一个可预览文件（代码/文档/图片），
        // 右栏自动打开并切到最新文件，带会话归属。归档类（zip 等）只给下载不开栏。
        useStore.getState().set({ artifactDraft: null });
        openArtifact(sid, file);
      },
      onIteration: (data) => { setAgentIteration(data); liveMetaRef.current.iteration = data; },
      onProgress: (data) => { setProgress(data); liveMetaRef.current.progress = data; },
      onSources: (sources) => { setStreamSources(sources); liveMetaRef.current.sources = sources; },
      onInputRequest: (data) => {
        clarifyRef.current = { question: data.question, options: data.options || [] };
      },
      onClarify: (data) => { clarifyRef.current = data; },
      onOrchestration: (data: any) => { const o = { strategy: data.strategy, members: (data.members || []).map((m: any) => ({ ...m, status: "pending" })) }; liveOrchRef.current = o; setLiveOrch(o); liveMetaRef.current.orch = o; },
      onSubagent: (data: any) => { const prev = liveOrchRef.current; if (!prev) return; const o = { ...prev, members: prev.members.map((m: any) => m.id === data.id ? { ...m, status: data.status, elapsed_ms: data.elapsed_ms ?? m.elapsed_ms, preview: data.preview ?? m.preview } : m) }; liveOrchRef.current = o; setLiveOrch(o); liveMetaRef.current.orch = o; },
      onDone: (data) => {
        interruptRequestedRef.current = null;
        activeTurnRef.current = null; setActiveTurn(null); liveMetaRef.current.activeTurn = null;
        // V308 批次四：取消挂起的 rAF，最终内容以完整缓冲为准（避免最后一帧未提交丢字）
        if (_rafId.current != null) { cancelAnimationFrame(_rafId.current); _rafId.current = null; }
        if (_streamBuf.current) streamContentRef.current = _streamBuf.current;
        // v21: Notify user when task completes (useful if tab is in background)
        if (document.hidden && data.files?.length) {
          notifyTaskComplete("code_task", `已创建 ${data.files.length} 个文件`);
        }
        if (streamSaveTimer.current) clearTimeout(streamSaveTimer.current);
        setStreamContent(prev => {
          const c = prev || streamContentRef.current || (data.status === "interrupted" ? "任务已停止。" : "（空回答）");
          // v11: Only use files from server event — NOT stale createdFiles state
          const msgFiles = data.files?.length ? data.files : undefined;
          const msgSteps = data.steps?.length ? data.steps : (agentStepsRef.current.length ? agentStepsRef.current : undefined);
          const persistedProcess = data.run_manifest?.process;
          const msgTimeline = toPublicAgentTimeline(
            persistedProcess?.timeline?.length
              ? persistedProcess.timeline
              : timelineRef.current.map(event => ({
                  ...event,
                  status: event.status === "running" ? "done" as const : event.status,
                })),
          );
          const msgTodo = persistedProcess?.todo?.length
            ? persistedProcess.todo.map(item => ({ text: item.text, status: item.status }))
            : todoRef.current.length
              ? [...todoRef.current]
              : undefined;
          addMsg(id!, { role: "assistant", content: c, sources: data.sources, groundings: data.groundings, run_manifest: data.run_manifest, trace: [...traceRef.current, ...(data.trace || [])], ts: Date.now(), status: data.status || "complete", stop_reason: data.stop_reason, tokens: data.tokens, files: msgFiles, steps: msgSteps, timeline: msgTimeline, todo: msgTodo, suggestions: data.suggestions, clarify: clarifyRef.current || undefined, orchestration: liveOrchRef.current || undefined, elapsed_ms: data.elapsed_ms });
          if (isFirstMsgRef.current) {
            isFirstMsgRef.current = false;
            generateTitle(sendQuery, c.slice(0, 100)).then(title => {
              const ss = useStore.getState().sessions.map(s => s.id === id ? { ...s, title } : s);
              if (typeof window !== "undefined") localStorage.setItem("hmm_s", JSON.stringify(ss.slice(-50)));
              useStore.setState({ sessions: ss });
            }).catch(() => {});
          }
          return "";
        });
        if (featureContextSnapshot.length) {
          useStore.getState().consumeFeatureContexts(featureContextSnapshot.map(x => x.id));
        }
        setStreaming(false); setTraceSteps([]); setAgentIteration(null); setProgress(null);
        setThinkingContent(""); thinkingRef.current = "";
        set({ loading: false, streamingConvId: null });
        // V295 直播收尾：停快照定时器 + 从全局直播态移除本会话（最终消息已落入 sessions，
        // 重进走正常历史渲染即可）。myStreamRef 清空表示本组件不再持有该会话的直播。
        _stopLive(); useStore.getState().clearLive(id!); if (myStreamRef.current === id) myStreamRef.current = null;
        apiStats().then(s => set({ stats: s })).catch(() => {});
      },
      onCancelled: () => {
        interruptRequestedRef.current = null;
        activeTurnRef.current = null; setActiveTurn(null); liveMetaRef.current.activeTurn = null;
        if (_rafId.current != null) { cancelAnimationFrame(_rafId.current); _rafId.current = null; }
        if (streamSaveTimer.current) clearTimeout(streamSaveTimer.current);
        const partial = _streamBuf.current || streamContentRef.current;
        addMsg(id!, {
          role: "assistant",
          content: partial?.trim() ? partial : "任务已停止。",
          ts: Date.now(),
          status: "interrupted",
          stop_reason: "interrupted",
          trace: [...traceRef.current],
            timeline: timelineRef.current.length
              ? toPublicAgentTimeline(timelineRef.current.map(event => ({
                  ...event,
                  status: event.status === "running" ? "error" as const : event.status,
                })))
              : undefined,
          todo: todoRef.current.length ? [...todoRef.current] : undefined,
        });
        setStreaming(false); setStreamContent(""); setTraceSteps([]); setAgentIteration(null); setProgress(null);
        setThinkingContent(""); thinkingRef.current = "";
        set({ loading: false, streamingConvId: null });
        _stopLive(); useStore.getState().clearLive(id!); if (myStreamRef.current === id) myStreamRef.current = null;
        void reconcileConversationAfterStop(id!);
      },
      onError: (error) => {
        interruptRequestedRef.current = null;
        activeTurnRef.current = null; setActiveTurn(null); liveMetaRef.current.activeTurn = null;
        if (streamSaveTimer.current) clearTimeout(streamSaveTimer.current);
        // v10: Save partial content if we have any
        const partial = streamContentRef.current;
        if (partial && partial.length > 10) {
          addMsg(id!, { role: "assistant", content: partial + "\n\n*回答生成中断，以上内容可能不完整。*", ts: Date.now() });
        } else {
          addMsg(id!, { role: "assistant", content: `请求失败: ${error}`, ts: Date.now() });
        }
        setStreaming(false); setStreamContent(""); setTraceSteps([]); setProgress(null);
        setThinkingContent(""); thinkingRef.current = "";
        set({ loading: false, streamingConvId: null });
        _stopLive(); useStore.getState().clearLive(id!); if (myStreamRef.current === id) myStreamRef.current = null;
      },
    });
  }

  function handleRegenerate() {
    if (streaming) return;
    let query = lastQueryRef.current?.query;
    const targetSid = lastQueryRef.current?.sid || sid;
    if (!query && session) {
      const userMsgs = session.messages.filter(m => m.role === "user");
      if (userMsgs.length > 0) {
        query = userMsgs[userMsgs.length - 1].content;
      }
    }
    if (!query || !targetSid) return;
    const s = useStore.getState().sessions.find(s => s.id === targetSid);
    if (s?.messages.length && s.messages[s.messages.length - 1].role === "assistant") {
      const ss = useStore.getState().sessions.map(sess => sess.id === targetSid ? { ...sess, messages: sess.messages.slice(0, -1) } : sess);
      if (typeof window !== "undefined") localStorage.setItem("hmm_s", JSON.stringify(ss.slice(-50)));
      useStore.setState({ sessions: ss, sid: targetSid });
    }
    send(query, true);
  }

  // v7.0: Branch regenerate — truncate from any assistant message and re-generate
  // V273: 升级为 CC 式回退（资料 13.3.5 Claude Code 路线）——先调服务端 rewind：
  // 权威删除该 assistant 及之后的消息，并把**会话工作区文件**还原到该回答开始前的
  // 快照（对话和文件一起回退）；服务端不可达/老会话无快照 → 无声降级为原本地截断。
  async function handleBranchRegenerate(assistantIdx: number) {
    if (streaming || !session) return;
    try {
      const aIndex = session.messages.slice(0, assistantIdx).filter(m => m.role === "assistant").length;
      const r = await rewindConversation(session.id, aIndex);
      if (r?.ok) showToast(r.files_restored ? "已回退：对话与工作台文件一起还原" : ("已回退对话" + (r.note ? "（" + r.note + "）" : "")), "success");
    } catch { /* 离线/老后端：仅本地截断，保持旧行为 */ }
    // Find the user message that preceded this assistant message
    let userQuery = "";
    for (let i = assistantIdx - 1; i >= 0; i--) {
      if (session.messages[i]?.role === "user") {
        userQuery = session.messages[i].content.split("\n").filter(l => !l.startsWith("\u{1F4CE} ")).join("\n").trim();
        break;
      }
    }
    if (!userQuery) return;
    // Truncate messages from this assistant message onward
    const truncated = session.messages.slice(0, assistantIdx);
    const ss = useStore.getState().sessions.map(s =>
      s.id === session.id ? { ...s, messages: truncated } : s
    );
    if (typeof window !== "undefined") localStorage.setItem("hmm_s", JSON.stringify(ss.slice(-50)));
    useStore.setState({ sessions: ss });
    send(userQuery, true);
  }

  function handleEdit(idx: number) {
    if (!session) return;
    const msg = session.messages[idx];
    if (!msg || msg.role !== "user") return;
    // Extract text without file badges
    const lines = msg.content.split("\n").filter(l => !l.startsWith("\u{1F4CE} "));
    setText(lines.join("\n").trim());
    // Remove this message and all after it
    const ss = useStore.getState().sessions.map(s => s.id === session.id ? { ...s, messages: s.messages.slice(0, idx) } : s);
    if (typeof window !== "undefined") localStorage.setItem("hmm_s", JSON.stringify(ss.slice(-50)));
    useStore.setState({ sessions: ss });
    textRef.current?.focus();
  }

  async function exportChat() {
    if (!session || msgs.length === 0) return;
    const md = msgs.map(m => m.role === "user" ? `**用户：**\n${m.content}` : `**助手：**\n${m.content}`).join("\n\n---\n\n");
    const filename = `${(session.title || "chat").replace(/[^\w\u4e00-\u9fff]/g, "_")}.md`;
    const r = await saveFile(filename, `# ${session.title || "对话"}\n\n${md}`);
    if (r.ok && r.path) {
      showToast(`已保存到 ${r.path}`, "success", 4000);
    } else if (r.ok) {
      showToast("已开始下载", "success");
    }
  }

  async function continueInNewChat() {
    if (!sid || !session || handoffBusy) return;
    setHandoffBusy(true);
    try {
      const requestId = typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `handoff-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const { handoff } = await createChatContinuation(sid, requestId);
      const targetId = handoff.target_conversation_id;
      addSession({
        id: targetId,
        title: `继续：${session.title || "对话"}`,
        messages: [],
        created: Date.now(),
        updated_at: Date.now(),
        project_id: handoff.project_id || undefined,
        has_durable_content: true,
        visibility_source: "server",
        revision: 1,
        sync_state: "synced",
      });
      openTabAction(targetId);
      try { history.pushState({}, "", `/chat/${targetId}`); } catch { /* no-op */ }
      showToast("已创建可验证连续性包；新 Chat 会先核对状态再继续", "success", 4500);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "创建接力 Chat 失败", "error", 4500);
    } finally {
      setHandoffBusy(false);
    }
  }

  async function resolveIncomingHandoff(action: "acknowledge" | "reject") {
    if (!sid || !incomingHandoff || handoffBusy) return;
    setHandoffBusy(true);
    try {
      const result = await transitionChatContinuation(sid, incomingHandoff.id, action);
      setIncomingHandoff(result.handoff);
      showToast(action === "acknowledge" ? "已确认接手；未完成步骤已进入当前上下文" : "已拒绝本次接力", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "更新接力状态失败", "error");
    } finally {
      setHandoffBusy(false);
    }
  }

  function onFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    const fl = e.target.files;
    e.target.value = "";
    setAttachmentMenu(false);
    if (!fl) return;
    for (let i = 0; i < fl.length; i++) {
      void processFile(fl[i], { relativePath: fl[i].name });
    }
  }
  async function onFolderInput(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(e.target.files || []);
    e.target.value = "";
    setAttachmentMenu(false);
    if (!selected.length) return;
    const totalBytes = selected.reduce((sum, item) => sum + item.size, 0);
    if (selected.length > 100 || totalBytes > 500 * 1024 * 1024) {
      showToast("文件夹最多添加 100 个文件且总大小不超过 500 MB", "warning", 5000);
      return;
    }
    // Bound concurrency so a large folder cannot saturate the renderer or the
    // upload worker.  Failed files stay out of the attachment list and surface
    // an actionable toast from processFile.
    for (let index = 0; index < selected.length; index += 4) {
      await Promise.all(selected.slice(index, index + 4).map(item =>
        processFile(item, {
          relativePath: item.webkitRelativePath || item.name,
        }),
      ));
    }
  }
  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      const behavior = typeof window !== "undefined" ? localStorage.getItem("hmm_send_behavior") || "enter" : "enter";
      const shouldSend = behavior === "ctrl_enter" ? (e.ctrlKey || e.metaKey) : !(e.ctrlKey || e.metaKey);
      if (shouldSend) { e.preventDefault(); void send(); }
    }
    if ((e.ctrlKey || e.metaKey) && e.key === "n") {
      e.preventDefault(); writeActiveProject(useStore.getState().user, ""); useStore.getState().newChat();
    }
  }

  const modelName = stats?.active_model && stats.active_model !== "—" ? stats.active_model : "";
  const charCount = text.length;
  const projectContextId = session ? (session.project_id || "") : readActiveProject(user);
  const projectContext = projectContextId
    ? readAccountProjects(user).find(project => project.id === projectContextId)
    : null;
  const publicLiveTimeline = useMemo(
    () => toPublicAgentTimeline(liveTimeline),
    [liveTimeline],
  );

  return (
    <main className="chat-area flex-1 flex flex-col min-w-0 h-full relative" style={{ background: "var(--bg-primary)" }} onDragEnter={handleDragEnter} onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop}>
      {dragOver && <div className="pointer-events-none absolute left-1/2 bottom-28 z-50 -translate-x-1/2 rounded-2xl px-5 py-3" style={{ background: "var(--bg-primary)", border: "1px solid var(--accent)", boxShadow: "var(--shadow-lg)" }}>
        <div className="flex items-center gap-2.5"><Upload size={18} style={{ color: "var(--accent)" }} /><span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>松开以添加文件或目录</span></div>
      </div>}

      {/* V103.90 多标签会话条（同时打开多个对话时显示）*/}
      {openTabs.length >= 2 && (
        <div className="flex items-stretch gap-0.5 px-2 pt-1.5 flex-shrink-0 overflow-x-auto" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          {openTabs.map(tid => {
            const s = sessions.find(x => x.id === tid);
            const active = tid === sid;
            return (
              <div key={tid} onClick={() => openTabAction(tid)}
                className="group flex items-center gap-1.5 px-3 py-1.5 rounded-t-lg cursor-pointer text-[12px] whitespace-nowrap max-w-[180px] flex-shrink-0 transition-colors"
                style={{ background: active ? "var(--bg-primary)" : "transparent", color: active ? "var(--text-primary)" : "var(--text-tertiary)", border: `1px solid ${active ? "var(--border)" : "transparent"}`, borderBottom: "none" }}>
                <span className="truncate">{s?.title || "对话"}</span>
                <button onClick={(e) => { e.stopPropagation(); closeTabAction(tid); }} className="p-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity hover:bg-[var(--bg-tertiary)]" aria-label="关闭标签" title="关闭标签"><X size={12} /></button>
              </div>
            );
          })}
        </div>
      )}

      <header className={`h-11 flex items-center px-4 flex-shrink-0 gap-2 ${isDesktopEnv && !rightPanelOpen ? "titlebar-safe" : ""}`}>
        {/* V217: 桌面(≥768)收起时左侧有 52px 图标栏承担展开职责，此按钮只在手机宽度显示 */}
        {!sbOpen && <button onClick={() => set({ sbOpen: true })} aria-label="展开侧栏" className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] md:hidden"><PanelLeft size={18} style={{ color: "var(--text-tertiary)" }} /></button>}
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {projectContext && <FolderKanban size={14} className="shrink-0" style={{ color: "var(--accent)" }} />}
          <h1 className="min-w-0 truncate text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
            {projectContext?.name || session?.title || "HashMM"}
          </h1>
          {projectContext && session?.title && (
            <span className="hidden min-w-0 truncate text-[11px] sm:block" style={{ color: "var(--text-tertiary)" }}>
              {session.title}
            </span>
          )}
          {projectContext && (
            <button onClick={() => set({ desktopView: "gworkspace", adminOpen: false, setOpen: false })}
              className="hidden shrink-0 rounded-lg px-2 py-1 text-[10px] hover:bg-[var(--bg-tertiary)] md:inline-flex"
              style={{ color: "var(--text-tertiary)" }} title="查看项目资料和进度">
              项目详情
            </button>
          )}
        </div>
        {user?.role === "admin" && modelName && <ModelSwitcher current={modelName} />}
        <button onClick={() => { const next = !focusMode; setFocusMode(next); try { localStorage.setItem("hmm_chat_focus_mode", next ? "1" : "0"); } catch { /* best effort */ } }}
          className="rounded-lg px-2 py-1.5 text-[11px] transition-colors hover:bg-[var(--bg-tertiary)]"
          style={{ background: focusMode ? "var(--accent-light)" : "transparent", color: focusMode ? "var(--accent)" : "var(--text-tertiary)" }}
          aria-pressed={focusMode} title="专注模式：折叠工具活动，正文与证据优先"><Eye size={15} className="inline mr-1" />专注</button>
        {session && <button onClick={() => void continueInNewChat()} disabled={handoffBusy}
          className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] disabled:opacity-45"
          aria-label="在新 Chat 中继续" title="生成不含私有思维链和权限的持久接力包，在新 Chat 中继续">
          {handoffBusy ? <Loader2 size={16} className="animate-spin" style={{ color: "var(--text-tertiary)" }} /> : <Forward size={16} style={{ color: "var(--text-tertiary)" }} />}
        </button>}
        {msgs.length > 0 && <button onClick={() => { setSearchOpen(true); setTimeout(() => searchRef.current?.focus(), 30); }} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" aria-label="在对话中查找" title="在对话中查找 (Ctrl+F)"><SearchIcon size={16} style={{ color: "var(--text-tertiary)" }} /></button>}
        {msgs.length > 0 && <button onClick={exportChat} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" aria-label="导出对话" title="导出 Markdown"><Download size={16} style={{ color: "var(--text-tertiary)" }} /></button>}
          <button onClick={() => {
            const next = !rightPanelOpen;
            try { localStorage.setItem("hmm_right_panel", next ? "1" : "0"); } catch { /* */ }
          set({ rightPanelOpen: next, inspectorTab: "context", ...(next ? {} : { artifactPanel: null }) });
          }} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
          style={{ background: rightPanelOpen ? "var(--accent-light)" : "transparent" }}
          aria-label={rightPanelOpen ? "关闭统一检查器" : "打开统一检查器"} title="统一检查器：上下文、Agent、运行、文件、代码与画布">
          <PanelRight size={16} style={{ color: rightPanelOpen ? "var(--accent)" : "var(--text-tertiary)" }} />
        </button>
        {isDesktopEnv && <NotificationBell inline />}
      </header>

      {incomingHandoff && !["rejected", "expired", "superseded"].includes(incomingHandoff.status) && (
        <div className="mx-4 mb-2 rounded-xl px-3 py-2.5 text-[12px]" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          <div className="flex items-start gap-2">
            <Forward size={15} className="mt-0.5 shrink-0" style={{ color: "var(--accent)" }} />
            <div className="min-w-0 flex-1">
              <div className="font-medium" style={{ color: "var(--text-primary)" }}>Verified Project Continuity · 新 Chat 继续任务</div>
              <div className="mt-0.5 line-clamp-2" style={{ color: "var(--text-secondary)" }}>
                {incomingHandoff.payload.objective || incomingHandoff.payload.latest_user_request || "已载入公开任务状态"}
              </div>
              {incomingHandoff.payload.next_action && (
                <div className="mt-1" style={{ color: "var(--text-tertiary)" }}>下一步：{incomingHandoff.payload.next_action}</div>
              )}
              {incomingHandoff.state_verification?.stale && (
                <div className="mt-1 text-amber-600 dark:text-amber-400">
                  状态需重新核对：{incomingHandoff.state_verification.reasons.join("、")}
                </div>
              )}
              <div className="mt-1" style={{ color: "var(--text-tertiary)" }}>
                接力包不继承审批权限，不把模型陈述当成测试证据。
              </div>
            </div>
            {incomingHandoff.status !== "acknowledged" ? (
              <div className="flex shrink-0 gap-1">
                <button onClick={() => void resolveIncomingHandoff("acknowledge")} disabled={handoffBusy}
                  className="rounded-lg px-2 py-1 font-medium disabled:opacity-45"
                  style={{ color: "white", background: "var(--accent)" }}>确认接手</button>
                <button onClick={() => void resolveIncomingHandoff("reject")} disabled={handoffBusy}
                  className="rounded-lg px-2 py-1 disabled:opacity-45 hover:bg-[var(--bg-tertiary)]"
                  style={{ color: "var(--text-secondary)" }}>拒绝</button>
              </div>
            ) : (
              <span className="shrink-0 rounded-full px-2 py-0.5 text-[10px]" style={{ color: "var(--accent)", background: "var(--accent-light)" }}>已接手</span>
            )}
          </div>
        </div>
      )}

      {/* V103.90 会话内搜索栏（Ctrl/⌘+F）*/}
      {searchOpen && (
        <div className="flex items-center gap-1.5 px-4 py-1.5 flex-shrink-0 anim-fade-up" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          <SearchIcon size={14} style={{ color: "var(--text-tertiary)" }} />
          <input ref={searchRef} value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); stepSearch(e.shiftKey ? -1 : 1); } else if (e.key === "Escape") { e.preventDefault(); closeSearch(); } }}
            placeholder="在对话中查找…" className="flex-1 bg-transparent outline-none text-[13px]" style={{ color: "var(--text-primary)" }} />
          <span className="text-[11px] tabular-nums" style={{ color: "var(--text-tertiary)" }}>{searchResult.flat.length ? searchIdx + 1 : 0}/{searchResult.flat.length}</span>
          <button onClick={() => stepSearch(-1)} disabled={!searchResult.flat.length} className="p-1 rounded transition-colors hover:bg-[var(--bg-tertiary)] disabled:opacity-40" title="上一个 (Shift+Enter)"><ChevronUp size={15} style={{ color: "var(--text-secondary)" }} /></button>
          <button onClick={() => stepSearch(1)} disabled={!searchResult.flat.length} className="p-1 rounded transition-colors hover:bg-[var(--bg-tertiary)] disabled:opacity-40" title="下一个 (Enter)"><ChevronDown size={15} style={{ color: "var(--text-secondary)" }} /></button>
          <button onClick={closeSearch} className="p-1 rounded transition-colors hover:bg-[var(--bg-tertiary)]" title="关闭 (Esc)"><X size={15} style={{ color: "var(--text-secondary)" }} /></button>
        </div>
      )}

      <div ref={scrollAreaRef} onScroll={onScrollArea} className="flex-1 overflow-y-auto">
        <div className="max-w-[720px] mx-auto px-5 py-6 pb-4">
          {msgs.length === 0 && !streaming && (
            <div className="flex flex-col items-center justify-center min-h-[60vh] text-center anim-fade-up">
              <NewbieTips />
              <div className="mb-5"><HashMascotHero size={180} /></div>
              <h2 className="text-[28px] font-bold mb-2" style={{ color: "var(--text-primary)" }}>HashMM</h2>
              <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>有什么可以帮你？</p>
              <div className="chat-starter-grid mt-7 grid grid-cols-1 gap-2.5 w-full max-w-[520px]">
                {/* V254: 冒号结尾的半句式卡（深度调研/写代码）改为**填入输入框**等你补全主题，
                    而不是把半句话直接发出去（旧行为会让模型收到一句没头没尾的话）。
                    深度调研卡还会顺手打开「深度检索」开关，选卡即入深检链路。 */}
                {([
                  { title: "继续最近工作", sub: "查看进度和需要你确认的事项", action: "today" },
                  { title: "从资料开始", sub: "阅读、比较并带依据地回答", action: "library" },
                  { title: "生成一个成果", sub: "说明交付物，HashMM 会持续做到可验收", prompt: "请帮我完成一个可交付成果：", fill: true },
                  { title: "研究一个问题", sub: "检索、核对来源并形成结论", prompt: "请深入研究并给出有依据的结论：", fill: true, deep: true },
                ] as Array<{ title: string; sub: string; prompt?: string; fill?: boolean; deep?: boolean; action?: "today" | "library" }>).map(s => (
                  <button key={s.title}
                    onClick={() => {
                      if (s.action === "today") {
                        set({ desktopView: "work-active" });
                      } else if (s.action === "library") {
                        set({ desktopView: "hub-knowledge" });
                      } else if (s.fill && s.prompt) {
                        setText(s.prompt);
                        if (s.deep) setDeepMode(true);
                        textRef.current?.focus();
                      }
                    }}
                    className="text-left px-4 py-3 rounded-xl transition-all hover:-translate-y-0.5 starter-card"
                    style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                    <div className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>{s.title}</div>
                    <div className="text-[11.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{s.sub}</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {(() => {
            const MSG_WINDOW = 40;
            const windowed = !showAllMsgs && msgs.length > MSG_WINDOW;
            const start = windowed ? msgs.length - MSG_WINDOW : 0;
            const shown = windowed ? msgs.slice(start) : msgs;
            return (
              <>
                {windowed && (
                  <button onClick={() => setShowAllMsgs(true)}
                    className="mx-auto mb-4 block px-3 py-1.5 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]"
                    style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                    ↑ 显示更早的 {start} 条消息
                  </button>
                )}
                {shown.map((m, j) => {
                  const i = start + j;   // 原始索引（编辑/重生成/截断必须用真索引）
                  const isHit = searchOpen && i === activeHitMsgIdx;
                  return (
                    <div key={i} id={`msg-${i}`} style={isHit ? { boxShadow: "0 0 0 2px var(--accent)", borderRadius: 12, transition: "box-shadow .2s" } : { transition: "box-shadow .2s" }}>
                      <MsgBubble msg={m} index={i} convId={sid || undefined}
                        showRegenerate={m.role === "assistant" && !streamingHere}
                        onRegenerate={() => handleBranchRegenerate(i)}
                        onEdit={m.role === "user" ? () => handleEdit(i) : undefined}
                        onSuggestion={m.role === "assistant" ? (text) => send(text) : undefined} />
                    </div>
                  );
                })}
              </>
            );
          })()}

          {streamingHere && (
            <div className="mb-6 anim-fade-up flex gap-3 group/msg">
              {/* V254: 流式回答头像与 MsgBubble 统一为品牌小哈（修"图标有问题"） */}
              <div className="w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5 overflow-hidden"
                style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)" }}>
                <HashMascot size={20} />
              </div>
              <div className="flex-1 pl-3" style={{ borderLeft: "2px solid var(--accent-light, rgba(37,99,235,0.15))" }}>
                {/* Agent status indicator */}
                {agentIteration && (
                  <div className="flex items-center gap-2 mb-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                    <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "var(--accent)" }} />
                    {agentSteps.some(s => s.status === "running")
                      ? "正在执行工具..."
                      : streamContent ? "正在生成回答..." : "正在思考..."}
                    {agentIteration.current > 1 && <span className="opacity-60">（第 {agentIteration.current} 步）</span>}
                  </div>
                )}
                {/* V103.30: 子 agent 编排实时点亮 */}
                {!focusMode && <SubAgentPanel orch={liveOrch} />}
                {/* v10: Progress bar */}
                {!focusMode && progress && (
                  <div className="mb-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{progress.msg}</span>
                      <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{progress.pct}%</span>
                    </div>
                    <div className="h-1 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
                      <div className="h-full rounded-full transition-all duration-500" style={{ width: `${progress.pct}%`, background: "var(--accent)" }} />
                    </div>
                  </div>
                )}
                {taskContract && (
                  <div className="mb-3 rounded-[12px] overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                    <div className="px-3.5 py-2.5 flex items-center gap-2" style={{ borderBottom: "1px solid var(--border)" }}>
                      <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>完成契约</div>
                      <span className="ml-auto text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>运行事实核验</span>
                    </div>
                    <div className="px-3.5 py-2.5">
                      <div className="text-[12px] leading-relaxed line-clamp-2" style={{ color: "var(--text-secondary)" }}>{taskContract.goal}</div>
                      <div className="mt-2 space-y-1">
                        {taskContract.success_criteria.slice(0, 5).map(item => (
                          <div key={item.check_id} className="flex items-start gap-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                            <span className="mt-[5px] w-1 h-1 rounded-full shrink-0" style={{ background: "var(--accent)" }} />
                            <span>{item.label}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
                {/* V300 第三期 Plan Mode：复杂任务的结构化计划（步骤 + 验收），执行前先给用户看 */}
                {taskPlan && taskPlan.steps.length > 0 && (
                  <div className="mb-3 rounded-[12px] overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                    <div className="px-3.5 py-2.5" style={{ borderBottom: "1px solid var(--border)" }}>
                      <div className="text-[12px] font-semibold" style={{ color: "var(--accent)" }}>任务计划</div>
                      <div className="text-[12.5px] mt-0.5" style={{ color: "var(--text-primary)" }}>{taskPlan.goal}</div>
                    </div>
                    <div className="px-3.5 py-2 space-y-1.5">
                      {taskPlan.steps.map((st) => (
                        <div key={st.n} className="flex gap-2.5 text-[12.5px]">
                          <span className="shrink-0 w-5 h-5 rounded-full inline-flex items-center justify-center text-[11px] font-semibold" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{st.n}</span>
                          <div className="min-w-0">
                            <div style={{ color: "var(--text-primary)" }}>{st.action}</div>
                            {st.acceptance ? <div className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>验收：{st.acceptance}</div> : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {/* v12: Live Agent Execution Log — 统一有序事件流（思考/工具按真实顺序交错，对标 Claude） */}
                {todoItems.length > 0 && <TodoCard items={todoItems} />}
                {!focusMode && publicLiveTimeline.length > 0 && (
                  <>
                    <AgentLog
                      steps={publicLiveTimeline.map((e, i) => ({
                        id: e.id || `live-${i}`,
                        node: e.node,
                        detail: e.detail,
                        tool: e.tool,
                        status: e.status,
                        elapsed_ms: e.elapsed_ms,
                        hooks: e.hooks,
                      }))}
                      visible={true}
                      onToggle={() => {}}
                    />
                    {/* V86: 质量徽章随事件实时浮现（vision/检索改写/错误恢复在生成中即可见） */}
                    <QualityBadgesRow steps={publicLiveTimeline} />
                  </>
                )}
                {focusMode && publicLiveTimeline.length > 0 && (
                  <div className="mb-2 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10.5px]" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>
                    <Loader2 size={11} className={streamingHere ? "animate-spin" : ""} />
                    已折叠 {publicLiveTimeline.length} 条工具与运行活动；证据保留在右侧检查器
                  </div>
                )}
                {liveDelta && (
                  <div className="al-narrate live-delta">{liveDelta}<span className="ld-caret" /></div>
                )}
                {ctxInfo && ctxInfo.budget > 0 && (
                  <div className="ctx-meter" title={`${ctxInfo.chars.toLocaleString()} / ${ctxInfo.budget.toLocaleString()} 字符`}>
                    上下文 {Math.min(100, Math.round((ctxInfo.chars / ctxInfo.budget) * 100))}%
                    <span className="ctx-bar"><span className="ctx-bar-fill"
                      style={{ width: `${Math.min(100, (ctxInfo.chars / ctxInfo.budget) * 100)}%`,
                               background: ctxInfo.chars / ctxInfo.budget > 0.8 ? "#f59e0b" : "var(--accent)" }} /></span>
                  </div>
                )}
                {/* v11: Skeleton loading (before first token) */}
                {!streamContent && !thinkingContent && agentSteps.length === 0 && (
                  <div className="space-y-2 anim-fade-up">
                    <div className="skeleton-shimmer h-3.5 w-[85%]" />
                    <div className="skeleton-shimmer h-3.5 w-[70%]" />
                    <div className="skeleton-shimmer h-3.5 w-[55%]" />
                  </div>
                )}
                {/* Loading dots (when thinking or tools running) */}
                {!streamContent && (thinkingContent || agentSteps.length > 0) && (
                  <div className="flex gap-1.5 mb-3">{[0,1,2].map(i => <span key={i} className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--accent)", animation: `pulse3 1.2s ease-in-out ${i*0.2}s infinite` }} />)}</div>
                )}
                {/* v9.0: Pre-sent source cards (Perplexity style) */}
                {streamSources.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-3 anim-fade-up">
                    {streamSources.map((s, i) => (
                      <span key={i} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px]"
                        style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)" }}
                        title={s.text || ""}>
                        <span className="w-4 h-4 rounded flex items-center justify-center text-[10px] font-bold flex-shrink-0"
                          style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{s.id ?? i + 1}</span>
                        <span className="truncate max-w-[140px] font-medium" style={{ color: "var(--text-secondary)" }}>{s.filename || "来源"}</span>
                        {(s.page ?? -1) > 0 && <span style={{ color: "var(--text-tertiary)" }}>p.{s.page}</span>}
                      </span>
                    ))}
                  </div>
                )}
                {/* Streaming content */}
                {streamContent && <div className="text-[14px] leading-[1.85] msg-content" style={{ color: "var(--text-primary)" }} dangerouslySetInnerHTML={{ __html: safeRenderStream(streamContent, sid || "cur") + '<span class="streaming-cursor">▍</span>' }} />}
                {/* v12: Live streaming stats */}
                {streamContent && (
                  <div className="flex items-center gap-3 mt-2 text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                    <span>{streamContent.length} 字</span>
                    <span>~{Math.round(streamContent.length / 1.8)} tokens</span>
                  </div>
                )}
                {/* Created files (during streaming) */}
                {createdFiles.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-3">
                    {createdFiles.map((f, i) => {
                      const previewable = isPanelPreviewable(f.filename);
                      return (
                        <div key={i} className="inline-flex items-center rounded-lg overflow-hidden text-[12px] font-medium"
                          style={{ background: "var(--accent-light)", border: "1px solid var(--accent)" }}>
                          {previewable ? (
                            <button onClick={() => openArtifact(sid, f)}
                              className="flex items-center gap-2 px-3 py-2 transition-colors hover:bg-[var(--accent)] hover:bg-opacity-20"
                              style={{ color: "var(--accent)" }}>
                              <Eye size={14} /> {f.filename}
                            </button>
                          ) : (
                            <span className="flex items-center gap-2 px-3 py-2" style={{ color: "var(--accent)" }}>{f.filename}</span>
                          )}
                          <a href={withToken(f.download_url)} download
                            className="px-2 py-2 transition-colors hover:bg-[var(--accent)] hover:bg-opacity-20"
                            style={{ borderLeft: "1px solid var(--accent)", color: "var(--accent)" }} title="下载">
                            <Download size={14} />
                          </a>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      {/* V103.90 回到底部按钮（滚离底部时显示）*/}
      {showScrollBtn && (
        <button onClick={scrollToBottom} aria-label="回到底部" title="回到底部"
          className="absolute right-6 bottom-[104px] z-20 w-9 h-9 rounded-full flex items-center justify-center shadow-md transition-all hover:scale-105 anim-fade-up"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <ChevronDown size={18} />
        </button>
      )}

      <div className="flex-shrink-0 px-4 pb-4 pt-1">
        <div className="max-w-[720px] mx-auto">
          {/* V257: 浮动"停止生成"移除——发送按钮本身在流式时切换为停止（ChatGPT 风格，
              长指令中途想打断不用满屏找按钮）。 */}
          {slashHint && (
            <div className="flex justify-center mb-2">
              <span className="px-3 py-1 rounded-full text-[11px]" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{slashHint}</span>
            </div>
          )}
          {featureContexts.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2 anim-fade-up" aria-label="已关联到本轮 Chat 的功能上下文">
              {featureContexts.map(ctx => (
                <div key={ctx.id} className="inline-flex items-center gap-1.5 pl-2.5 pr-1 py-1 rounded-lg text-[11px] max-w-[260px]"
                  style={{ background: "var(--accent-light)", border: "1px solid var(--accent-mid)", color: "var(--accent)" }}
                  title={`${featureContextLabel(ctx.kind)} · ${ctx.title}（发送后由服务端限量、脱敏并作为不可信数据注入）`}>
                  <Link2 size={12} className="flex-shrink-0" />
                  <span className="font-medium flex-shrink-0">{featureContextLabel(ctx.kind)}</span>
                  <span className="truncate" style={{ color: "var(--text-secondary)" }}>{ctx.title}</span>
                  <button onClick={() => useStore.getState().removeFeatureContext(ctx.id)} className="p-0.5 rounded hover:bg-[var(--bg-secondary)]" title="移除上下文">
                    <X size={11} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {files.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2 anim-fade-up">
              {files.map((f, i) => <FileChip key={f.id} f={f} onRemove={() => removeFileAt(i)} onRetry={() => retryFile(f.id)} />)}
            </div>
          )}
          {/* v10.0: Redesigned input — textarea + integrated toolbar */}
          <div className="relative rounded-2xl input-box transition-all" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="flex items-end gap-1 px-3 pt-2 pb-1">
              <textarea ref={textRef} value={text} onChange={e => setText(e.target.value)} onKeyDown={handleKeyDown}
                placeholder={streamingHere
                  ? (activeTurn?.steerable ? "补充方向，发送后作用于当前任务" : "任务正在执行，可先输入下一步要求")
                  : "给 HashMM 发消息"} rows={1}
                className="chat-composer-textarea flex-1 bg-transparent text-[14px] outline-none resize-none min-w-0 px-1 leading-6 overflow-y-auto"
                style={{ color: "var(--text-primary)", height: COMPOSER_MIN_HEIGHT, minHeight: COMPOSER_MIN_HEIGHT, maxHeight: COMPOSER_MAX_HEIGHT }} />
              {streamingHere ? (
                <div className="flex items-center gap-1 flex-shrink-0 mb-0.5">
                  {text.trim() && (
                    <button onClick={() => void steerCurrentTurn()} disabled={!activeTurn?.steerable || steeringBusy}
                      aria-label="追加到当前任务" title={activeTurn?.steerable ? "追加到当前任务（Enter）" : "等待 Agent 进入可调整阶段"}
                      className="p-2 rounded-xl text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                      style={{ background: "var(--accent)" }}>
                      {steeringBusy ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} />}
                    </button>
                  )}
                  <button onClick={() => void interruptCurrentTurn()}
                    aria-label="停止当前任务" title="停止当前任务（Esc）"
                    className="p-2 rounded-xl text-white transition-all"
                    style={{ background: "var(--text-secondary)" }}>
                    <Square size={16} />
                  </button>
                </div>
              ) : ((text.trim() || files.length > 0) && (
                <button onClick={() => void send()} aria-label="发送" title="发送（Enter）"
                  className="p-2 rounded-xl text-white transition-all flex-shrink-0 mb-0.5"
                  style={{ background: "var(--accent)" }}>
                  <ArrowUp size={16} />
                </button>
              ))}
            </div>
            {/* Bottom toolbar — compact icons */}
            <div className="composer-tools flex items-center gap-0.5 px-2 pb-1.5 pt-0">
              <div className="relative">
                <button onClick={() => setAttachmentMenu(value => !value)}
                  className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ color: "var(--text-tertiary)" }}
                  aria-label="添加文件或文件夹" title="添加文件或文件夹">
                  <Paperclip size={15} />
                </button>
                {attachmentMenu && (
                  <div className="absolute bottom-full mb-1 left-0 rounded-xl py-1 anim-fade-up"
                    style={{
                      minWidth: 168,
                      zIndex: 70,
                      background: "var(--bg-primary)",
                      border: "1px solid var(--border)",
                      boxShadow: "var(--shadow-lg)",
                    }}>
                    <button onClick={() => fileRef.current?.click()}
                      className="w-full px-3 py-2 text-[12px] flex items-center gap-2 text-left hover:bg-[var(--bg-tertiary)]"
                      style={{ color: "var(--text-primary)" }}>
                      <FileIcon size={14} />添加文件
                    </button>
                    <button onClick={() => folderRef.current?.click()}
                      className="w-full px-3 py-2 text-[12px] flex items-center gap-2 text-left hover:bg-[var(--bg-tertiary)]"
                      style={{ color: "var(--text-primary)" }}>
                      <FolderKanban size={14} />添加文件夹
                    </button>
                  </div>
                )}
              </div>
              {/* V86: 截屏进问答栏（桌面端）——框选+标注，截完即附件，发送时自动做图像理解 */}
              {isDesktopEnv && capabilityPrefs.browser && runtimeReady.has("browser_use") && (
                <div className="relative">
                  <button onClick={() => setShotMenu(v => { const nv = !v; if (nv) { try { getCU()?.precapture?.(); } catch (_e) {} } return nv; })} disabled={capturing}
                    className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] disabled:opacity-40"
                    style={{ color: "var(--text-tertiary)" }} aria-label="截取屏幕区域" title="截取屏幕区域（可标注）">
                    {capturing ? <Loader2 size={15} className="animate-spin" /> : <Camera size={15} />}
                  </button>
                  {/* V94: 微信式截屏方式选择（记忆偏好） */}
                  {shotMenu && (
                    <div className="absolute bottom-full mb-1 left-0 rounded-xl py-1 anim-fade-up"
                         style={{ minWidth: 150, zIndex: 70, background: "var(--bg-primary)",
                                  border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
                      {([["0", "直接截屏（微信式·瞬间）"], ["1", "隐藏窗口截屏"]] as const).map(([v, label]) => {
                        const cur = (typeof window !== "undefined" ? localStorage.getItem("hmm_shot_hide") : null) ?? "0";
                        const active = cur === v;
                        return (
                          <button key={v}
                            onClick={() => { localStorage.setItem("hmm_shot_hide", v); setShotMenu(false); captureScreen(v === "1"); }}
                            className="w-full text-left px-3 py-1.5 text-[12px] flex items-center gap-2 transition-colors hover:bg-[var(--bg-tertiary)]"
                            style={{ color: active ? "var(--accent)" : "var(--text-primary)" }}>
                            <span className="w-3 inline-block">{active ? <Check size={11} /> : null}</span>{label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
              <input ref={fileRef} type="file" className="hidden" onChange={onFileInput} multiple accept=".txt,.md,.py,.pdf,.json,.csv,.docx,.xlsx,.png,.jpg,.jpeg,.html,.xml,.yaml" />
              <input ref={folderRef} type="file" className="hidden" onChange={onFolderInput} multiple
                {...({ webkitdirectory: "", directory: "" } as Record<string, string>)} />
              <div className="w-px h-4 mx-0.5" style={{ background: "var(--border)" }} />
              <button onClick={cycleRetrieval}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                style={{
                  color: retrievalMode !== "auto" ? "var(--accent)" : "var(--text-tertiary)",
                  background: retrievalMode !== "auto" ? "var(--accent-light)" : "transparent",
                }}
                title={`资料检索：${retrievalMode === "auto" ? "自动选择" : retrievalMode === "mix" ? "综合检索" : retrievalMode === "kg" ? "关系检索" : retrievalMode === "global" ? "全局概览" : "相似内容"}。点击切换`}>
                <Database size={11} /><span className="composer-tool-label">
                  {retrievalMode === "auto" ? "自动" : retrievalMode === "mix" ? "综合" : retrievalMode === "kg" ? "关系" : retrievalMode === "global" ? "全局" : "相似"}
                </span>
              </button>
              {isDesktopEnv && (
                <button onClick={() => {
                    const next = !browserMode;
                    setBrowserMode(next);
                    if (next) openBrowserInInspector(useStore.getState().browserPanel?.url || "https://www.bing.com/");
                  }}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ color: browserMode ? "var(--accent)" : "var(--text-tertiary)",
                           background: browserMode ? "var(--bg-tertiary)" : "transparent" }}
                  title="共享浏览器：下一条消息直接交给桌面 Browser Agent，真实动作与结果回到当前 Chat；独立页面只作共享视图和策略控制。">
                  <Globe size={11} /><span className="composer-tool-label">浏览器</span>
                </button>
              )}
              <button onClick={() => setDeepMode(v => !v)}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                style={{ color: deepMode ? "var(--accent)" : "var(--text-tertiary)",
                         background: deepMode ? "var(--bg-tertiary)" : "transparent" }}
                title="深度检索：Self-RAG 在同一个 Chat SSE / AgentLoop 内多跳取证、自评和再检索，并继续使用会话记忆、技能与统一验收。">
                <Sparkles size={11} /><span className="composer-tool-label">深度检索</span>
              </button>
              {/* V269 努力档位（对齐 Claude Code effort）：一键在 标准→深思→快速 间轮换。
                  控制的是"整体干多少活"：快速=直答（跳过想清楚再答/交付自审，少迭代）；
                  深思=强制先规划、交付后逐条独立验收、更多迭代。作为通用偏好持久化。 */}
              <button onClick={cycleEffort}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                style={{ color: effortMode !== "standard" ? "var(--accent)" : "var(--text-tertiary)",
                         background: effortMode !== "standard" ? "var(--bg-tertiary)" : "transparent" }}
                title={effortMode === "fast"
                  ? "努力档位：快速——直接作答，省时省 token（跳过深入规划与交付自审）。点击切换"
                  : effortMode === "max"
                    ? "努力档位：深思——先想透再答、交付后逐条独立验收、更多执行迭代，最认真也最耗时。点击切换"
                    : "努力档位：标准——按任务自动拿捏。点击切换（标准→深思→快速）"}>
                <Zap size={11} /><span className="composer-tool-label">{effortMode === "fast" ? "快速" : effortMode === "max" ? "深思" : "标准"}</span>
              </button>
              {/* V267 模型快速切换：点一下在「系统默认 → 我的模型1 → 我的模型2 → …」间轮换，
                  当场调 prefer 接口生效——不用再进设置翻页。治"每次都用默认模型答"。 */}
              {myModelList.length > 0 && (
                <button onClick={cycleMyModel}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ color: curModelName !== "系统默认" ? "var(--accent)" : "var(--text-tertiary)" }}
                  title={`回答模型：${curModelName}。点击切换（系统默认 → 你在设置里添加的模型），当场生效`}>
                  <Cpu size={11} /><span className="composer-tool-label">{curModelName}</span>
                </button>
              )}
              {/* V254 多智能体：改为打开**操作面板**（TeamPanel）——V253 的实现只是把示例
                  文字塞进输入框，回车后被当普通消息发出去，用户点了等于没点。现在：
                  面板里 目标 → 预览分工（可改可删）→ 启动 → 四色实时状态 → 汇总。
                  输入框已有内容时自动带入面板作为目标。 */}
              {capabilityPrefs.team && runtimeReady.has("multi_agent") && <button
                onClick={() => setTeamOpen(true)}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                style={{ color: teamOpen ? "var(--accent)" : "var(--text-tertiary)", background: teamOpen ? "var(--bg-tertiary)" : "transparent" }}
                title="多智能体协作：基于当前输入预览分工，执行后把汇总和控制室画布回帖到本会话">
                <Users size={11} /><span className="composer-tool-label">多智能体</span>
              </button>}
              {(selectedPluginIds === undefined || selectedPluginIds.length > 0) && <button
                onClick={() => {
                  set({ desktopView: "plugins", adminOpen: false, setOpen: false });
                }}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                style={{ color: desktopView === "plugins" ? "var(--accent)" : "var(--text-tertiary)", background: desktopView === "plugins" ? "var(--bg-tertiary)" : "transparent" }}
                title="插件：选择经过管理员审核并已加载的能力，下一轮 Chat 只向模型暴露你选中的插件工具。">
                <Blocks size={11} /><span className="composer-tool-label">插件{selectedPluginIds?.length ? ` ${selectedPluginIds.length}` : ""}</span>
              </button>}
              {/* V217: 画布链路的 composer 侧入口 —— 一键插指令模板（不代发，你可改可删），
                  与 ArtifactPanel「画布讲解」按钮、画布内 composer 汇入同一条链路 */}
              {capabilityPrefs.canvas && runtimeReady.has("canvas") && <div className="relative">
                <button onClick={() => {
                    const rect = canvasButtonRef.current?.getBoundingClientRect();
                    if (rect) setCanvasMenuPos({ left: Math.max(8, Math.min(rect.left, window.innerWidth - 184)), bottom: Math.max(8, window.innerHeight - rect.top + 6) });
                    openCanvasMenu();
                  }}
                  ref={canvasButtonRef}
                  aria-haspopup="menu"
                  aria-expanded={canvasMenu}
                  aria-controls="composer-canvas-menu"
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ color: canvasMenu ? "var(--accent)" : "var(--text-tertiary)" }}
                  title="工作画布：选个模板立即在右栏起稿（无需先发消息）。可直接书写、划选提问、让 agent 接着创作。">
                  <LayoutTemplate size={11} /><span className="composer-tool-label">画布</span>
                </button>
                {canvasMenu && <CanvasMenuPortal>
                  <button type="button" className="fixed inset-0 z-[119] cursor-default" aria-label="关闭画布菜单"
                    onClick={() => setCanvasMenu(false)} style={{ background: "transparent" }} />
                  <div id="composer-canvas-menu" role="menu" className="fixed py-1 rounded-xl z-[120] w-[176px] max-h-[min(70vh,460px)] overflow-y-auto"
                    style={{ left: canvasMenuPos.left, bottom: canvasMenuPos.bottom, background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
                    {/* V225 模板库：一键起稿；落盘与打开链路同 V222 点击即开 */}
                    {([["blank", "空白画布", "自由书写"], ["progress", "进度报告", "概览·风险·待拍板"],
                       ["review", "评审意见", "结论·问题清单"], ["compare", "方案对比", "候选·维度·结论"],
                       ["data", "数据看板", "SVG 图表·改数即重画"],
                       ["warroom", "多智能体作战室", "编队·分工·交接·验收"]] as const)
                      .map(([kind, label, sub]) => (
                      <button key={kind}
                        onClick={async () => {
                          setCanvasMenu(false);
                          const d = new Date();
                          const fname = `${label}-${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}-${String(d.getHours()).padStart(2, "0")}${String(d.getMinutes()).padStart(2, "0")}.html`;
                          const html = canvasTemplateHtml(kind);
                          try {
                            const cid = await ensureWorkspaceConversation(label);
                            const r = await saveConvFile(cid, fname, html);
                            set({ artifactDraft: null });
                            openArtifact(cid, { filename: fname, download_url: r.download_url });
                          } catch {
                            // 离线、未登录或后端暂不可达时仍应“点击即开”，不能退化成只回填
                            // 一句提示词。草稿先留在本地 store；恢复连接后可继续交给 Chat/Agent，
                            // 有服务端会话的正常路径仍由 saveConvFile 权威落盘。
                            set({ artifactDraft: { filename: fname, content: html } });
                            openArtifact(sid, { filename: fname, download_url: "" });
                          }
                        }}
                        className="w-full text-left px-3 py-1.5 hover:bg-[var(--bg-secondary)] transition-colors">
                        <div className="text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{label}</div>
                        <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{sub}</div>
                      </button>
                    ))}
                    {myTpls && myTpls.length > 0 && (
                      <>
                        <div className="mx-3 my-1" style={{ borderTop: "1px solid var(--border)" }} />
                        <div className="px-3 pt-0.5 pb-1 flex items-center gap-2">
                          <span className="text-[9.5px] font-semibold flex-1" style={{ color: "var(--text-tertiary)" }}>我的模板</span>
                          <button onClick={() => { try { window.open(withToken("/api/canvas/templates/export"), "_blank"); } catch { /* */ } }}
                            className="text-[9.5px] hover:underline" style={{ color: "var(--accent)" }} title="导出全部模板为 JSON（备份/迁移）">导出</button>
                          <label className="text-[9.5px] hover:underline cursor-pointer" style={{ color: "var(--accent)" }} title="导入模板 JSON（合并，重名自动改名）">
                            导入<input type="file" accept="application/json" hidden onChange={async e => {
                              const f = e.target.files?.[0]; e.target.value = "";
                              if (!f) return;
                              try {
                                const j = JSON.parse(await f.text());
                                const items = Array.isArray(j) ? j : (j.items || []);
                                const r = await importCanvasTemplates(items);
                                const rr = await listCanvasTemplates(); setMyTpls(rr.items || []);
                                set({ pendingPrompt: "" });
                                window.alert(`模板导入完成：成功 ${r.imported} 个${r.skipped ? `，跳过 ${r.skipped} 个（超限/超大/缺字段）` : ""}`);
                              } catch { window.alert("导入失败：文件不是有效的模板 JSON"); }
                            }} />
                          </label>
                        </div>
                        {myTpls.slice(0, 6).map(t => (
                          <div key={t.id} className="flex items-center hover:bg-[var(--bg-secondary)] transition-colors">
                          <button
                            onClick={async () => {
                              setCanvasMenu(false);
                              try {
                                const tpl = await getCanvasTemplate(t.id);
                                const cid = await ensureWorkspaceConversation(t.name);
                                const d = new Date();
                                const fname = `${t.name}-${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}-${String(d.getHours()).padStart(2, "0")}${String(d.getMinutes()).padStart(2, "0")}.html`;
                                const r = await saveConvFile(cid, fname, tpl.html);
                                openArtifact(cid, { filename: fname, download_url: r.download_url });
                              } catch (e) { /* 拉取失败静默 */ }
                            }}
                            className="flex-1 text-left px-3 py-1.5 min-w-0">
                            <div className="text-[11.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{t.name}</div>
                          </button>
                          <button onClick={async e => { e.stopPropagation();
                              try { const r = await promoteCanvasTemplate(t.id); window.alert(`已升为组织模板：${r.name}（全员起稿菜单可见）`);
                                const rr = await listCanvasTemplates(); setOrgTpls(rr.org_items || []); } catch { window.alert("升组织失败（老后端需升级到 V230+）"); } }}
                            className="px-2 text-[9.5px] shrink-0 hover:underline" style={{ color: "var(--accent)" }}
                            title="复制到组织模板库，全员可用（删除需管理员）">↑组织</button>
                          </div>
                        ))}
                      </>
                    )}
                    {orgTpls.length > 0 && (
                      <>
                        <div className="mx-3 my-1" style={{ borderTop: "1px solid var(--border)" }} />
                        <div className="px-3 pt-0.5 pb-1 text-[9.5px] font-semibold" style={{ color: "var(--text-tertiary)" }}>组织模板</div>
                        {orgTpls.slice(0, 6).map(t => (
                          <button key={t.id}
                            onClick={async () => {
                              setCanvasMenu(false);
                              try {
                                const tpl = await getCanvasTemplate(t.id);
                                const cid = await ensureWorkspaceConversation(t.name);
                                const d = new Date();
                                const fname = `${t.name}-${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}-${String(d.getHours()).padStart(2, "0")}${String(d.getMinutes()).padStart(2, "0")}.html`;
                                const r = await saveConvFile(cid, fname, tpl.html);
                                openArtifact(cid, { filename: fname, download_url: r.download_url });
                              } catch (e) { /* */ }
                            }}
                            className="w-full text-left px-3 py-1.5 hover:bg-[var(--bg-secondary)] transition-colors">
                            <div className="text-[11.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{t.name}</div>
                            <div className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>由 {t.by} 分享</div>
                          </button>
                        ))}
                      </>
                    )}
                    {mktTpls.length > 0 && (
                      <>
                        <div className="mx-3 my-1" style={{ borderTop: "1px solid var(--border)" }} />
                        <div className="px-3 pt-0.5 pb-1 text-[9.5px] font-semibold" style={{ color: "var(--text-tertiary)" }}>模板市场</div>
                        {mktTpls.map(t => (
                          <div key={t.id} className="flex items-center px-3 py-1.5 hover:bg-[var(--bg-secondary)] transition-colors">
                            <div className="flex-1 min-w-0">
                              <div className="text-[11.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{t.name}</div>
                              <div className="text-[9.5px] truncate" style={{ color: "var(--text-tertiary)" }}>{t.desc}</div>
                            </div>
                            <button onClick={async e => { e.stopPropagation();
                                try { const r = await marketInstall(t.id); window.alert(`已装进「我的模板」：${r.name}`);
                                  const rr = await listCanvasTemplates(); setMyTpls(rr.items || []); } catch { window.alert("安装失败（老后端需升级到 V230+）"); } }}
                              className="px-2 text-[9.5px] shrink-0 hover:underline" style={{ color: "var(--accent)" }}>安装</button>
                          </div>
                        ))}
                      </>
                    )}
                  </div>
                </CanvasMenuPortal>}
              </div>}
              {isDesktopEnv && capabilityPrefs.computer && runtimeReady.has("computer_use") && (
                <button onClick={() => setCuMode(v => !v)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ color: cuMode ? "var(--accent)" : "var(--text-tertiary)",
                           background: cuMode ? "var(--bg-tertiary)" : "transparent" }}
                  title="电脑操作：AI 调用本机 shell/文件/屏幕工具完成任务（每步可见、可审计）">
                  <Monitor size={11} /><span className="composer-tool-label">电脑操作</span>
                </button>
              )}
              {isDesktopEnv && capabilityPrefs.computer && runtimeReady.has("computer_use") && cuMode && (
                <button onClick={() => setReplayOpen(true)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ color: "var(--text-tertiary)" }}
                  title="查看 AI 在本机执行过的每一步操作（点击/输入/按键），供随时核查">
                  <ScrollText size={11} /><span className="composer-tool-label">操作记录</span>
                </button>
              )}
              <DocFilterChips selected={docFilter} onChange={setDocFilter} />
              <div className="flex-1" />
              {charCount > 0 && <span className="text-[10px] px-1" style={{ color: charCount > 7000 ? "#ef4444" : "var(--text-tertiary)" }}>{charCount}</span>}
              {/* 移除输入框区的对话文件面板：文件已在消息气泡内以 FileCard 卡片呈现，此处重复多余 */}
            </div>
          </div>
        </div>
      </div>

      {/* V99: Computer Use 操作回放审计浮层 */}
      <CuReplayPanel open={replayOpen} onClose={() => setReplayOpen(false)} />
      {/* 多 Agent 是当前 Chat 的一个可审阅步骤：保留当前会话和输入，
          启动后由后端把汇总、角色产出和控制室画布写回原对话。 */}
      <TeamPanel
        open={teamOpen}
        initialGoal={text}
        onClose={() => setTeamOpen(false)}
        onCompleted={() => {
          setTeamOpen(false);
          showToast("协作结果已回到当前对话，可继续追问或验收", "success");
        }}
      />

      {/* v27: Drag overlay */}
      {dragOver && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 pointer-events-none">
          <div className="p-8 rounded-xl text-center" style={{ background: "var(--bg-primary)", border: "2px dashed var(--accent)" }}>
            <span className="inline-flex items-center gap-2 text-[16px]" style={{ color: "var(--accent)" }}><Paperclip size={18} /> 松开上传文件</span>
          </div>
        </div>
      )}
      {/* File Preview Modal */}
      {/* v27: Per-conversation custom prompt */}
      {customPromptOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={() => setCustomPromptOpen(false)}>
          <div className="absolute inset-0 bg-black/40" />
          <div className="relative w-full max-w-lg p-6 rounded-xl shadow-2xl" style={{ background: "var(--bg-primary)" }}
               onClick={e => e.stopPropagation()}>
            <h3 className="text-[14px] font-semibold mb-3" style={{ color: "var(--text-primary)" }}>对话自定义指令</h3>
            <textarea value={convPrompt} onChange={e => setConvPrompt(e.target.value)}
              className="w-full h-32 p-3 rounded-lg text-[13px] resize-none outline-none"
              style={{ background: "var(--bg-secondary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
              placeholder="为这个对话设置专属系统指令... (例如: 本次对话专注于跨模态哈希研究)" />
            <div className="flex justify-end gap-2 mt-3">
              <button onClick={() => setCustomPromptOpen(false)}
                className="px-3 py-1.5 text-[12px] rounded-lg" style={{ color: "var(--text-secondary)" }}>取消</button>
              <button onClick={() => {
                if (sid) void saveConversationPrompt(sid, convPrompt)
                  .catch(error => showToast(error instanceof Error ? error.message : "自定义指令保存失败", "error"));
                setCustomPromptOpen(false);
              }} className="px-3 py-1.5 text-[12px] rounded-lg" style={{ background: "var(--accent)", color: "white" }}>保存</button>
            </div>
          </div>
        </div>
      )}
      {/* v20: Command Palette */}
      <CommandPalette
        isOpen={cmdPaletteOpen}
        onClose={() => setCmdPaletteOpen(false)}
        onNewChat={() => { /* trigger new chat */ }}
        convId={sid || undefined}
      />
    </main>
  );
}


/** 新手 30 秒（V257）：首次进入显示三条上手要点，读过即收（localStorage）。 */
function NewbieTips() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    try { if (!localStorage.getItem("hmm_tips_seen")) setShow(true); } catch { /* */ }
  }, []);
  if (!show) return null;
  return (
    <div className="w-full max-w-[520px] mb-5 rounded-2xl px-4 py-3 text-left relative"
      style={{ background: "var(--accent-light)", border: "1px solid var(--border)" }}>
      <div className="text-[12px] font-bold mb-1.5" style={{ color: "var(--accent)" }}>30 秒上手</div>
      <div className="text-[11.5px] leading-relaxed space-y-1" style={{ color: "var(--text-secondary)" }}>
        <div>1. 左侧只保留「进行中」「成果」「资料」；新工作直接从对话开始。</div>
        <div>2. 先说清目标和想要的成果，HashMM 会自动选择检索、浏览器、电脑或协作方式。</div>
        <div>3. 右侧工作区统一查看依据、过程和成果；需要确认时会明确停下来等你决定。</div>
      </div>
      <button onClick={() => { setShow(false); try { localStorage.setItem("hmm_tips_seen", "1"); } catch { /* */ } }}
        className="absolute top-2.5 right-3 text-[11px] px-2 py-0.5 rounded-lg"
        style={{ color: "var(--text-tertiary)", border: "1px solid var(--border)" }}>知道了</button>
    </div>
  );
}
