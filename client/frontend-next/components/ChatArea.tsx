"use client";
import { useRef, useEffect, useState, useCallback, useMemo } from "react";
import { useStore } from "@/lib/store";
import { searchMessages, stepHit } from "@/lib/messageSearch";
import { isDesktop, getCU, saveFile } from "@/lib/desktop";
import { showToast } from "@/lib/toast";
import { runComputerUse, cuSave, type CuStep } from "@/lib/cu";
import { CuReplayPanel } from "./CuReplayPanel";
import { openArtifact, isPanelPreviewable } from "@/lib/artifact";
import { TodoCard, type TodoItem } from "./TodoCard";
import { chatStream, chatStreamV10, generateTitle, createConversation, saveStreamingMessage, quickListModels, setDefaultModel, stats as apiStats, withToken, deepSearch } from "@/lib/api";
import { renderMsg, sanitizeLLMOutput } from "@/lib/render";
import type { TraceStep, Source } from "@/lib/types";
import { MsgBubble } from "./MsgBubble";
import HashMascotHero from "./HashMascotHero";
import { Paperclip, ArrowUp, X, Zap, BookOpen, GitCompare, HelpCircle, PanelLeft, Square,
         FileText, Image, FileCode, FileSpreadsheet, File as FileIcon, ChevronDown, Check,
         Download, Loader2, Upload, Eye, Search as SearchIcon, Network, Code2, Globe, Sparkles, Database, Bot, Camera, Monitor, ScrollText, ChevronUp } from "lucide-react";
import { CommandPalette } from "./CommandPalette";
import { TemplateMarket } from "./TemplateMarket";
import { ThinkingPanel } from "./ThinkingPanel";
import { AgentLog } from "./AgentLog";
import { SubAgentPanel } from "./SubAgentPanel";
import { QualityBadgesRow } from "./QualityBadges";
import { notifyTaskComplete, requestNotificationPermission } from "@/lib/notifications";
import { DocFilterChips } from "./DocFilterChips";

interface UFile { name: string; size: string; ext: string; text: string; dataUrl?: string; }

// Throttled streaming renderer — full render every 300 chars, simple append between
let _srLen = 0, _srHtml = "";
function safeRenderStream(text: string): string {
  const delta = text.length - _srLen;
  if (_srLen > 300 && delta > 0 && delta < 300 && _srHtml) {
    return _srHtml + text.slice(_srLen).replace(/\n/g, "<br>");
  }
  // Strip markdown headers → bold (Claude-style) before rendering
  let t = text.replace(/^(#{1,4})\s+(.+)$/gm, (_, _h, title) => `**${title.trim()}**\n`);
  // Close unclosed code/math blocks
  const cc = (t.match(/```/g) || []).length;
  if (cc % 2 !== 0) t += "\n```";
  const mc = (t.match(/\$\$/g) || []).length;
  if (mc % 2 !== 0) t += "$$";
  try {
    _srHtml = renderMsg(t);
    _srLen = text.length;
    return _srHtml;
  } catch (_e) {
    _srHtml = t.replace(/\n/g, "<br>");
    _srLen = text.length;
    return _srHtml;
  }
}

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

function FileChip({ f, onRemove }: { f: UFile; onRemove: () => void }) {
  const { Icon, color } = fIcon(f.ext);
  // V86: 图片附件（截屏走这里）直接显示缩略图，所见即所发
  if (f.dataUrl) {
    return <div className="relative inline-block rounded-lg overflow-hidden anim-fade-up" style={{ border: "1px solid var(--border)" }}>
      <img src={f.dataUrl} alt={f.name} title={f.name} className="block" style={{ height: 56, maxWidth: 120, objectFit: "cover" }} />
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
    <span className="flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>{f.size}</span>
    <button onClick={onRemove} className="p-0.5 rounded hover:bg-[var(--bg-secondary)] flex-shrink-0"><X size={11} style={{ color: "var(--text-tertiary)" }} /></button>
  </div>;
}

function ModelSwitcher({ current }: { current: string }) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<Array<{ id: string; name: string; is_default: number }>>([]);
  return <div className="relative">
    <button onClick={async () => { if (open) { setOpen(false); return; } setModels(await quickListModels()); setOpen(true); }}
      className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium transition-all hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--accent)" }}>
      {current} <ChevronDown size={11} />
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
  const { sessions, sid, stats, sbOpen, customPrompt, artifactPanel, streamingConvId } = useStore();
  const set = useStore(s => s.set);
  const addSession = useStore(s => s.addSession);
  const addMsg = useStore(s => s.addMsg);
  // V103.90 多标签
  const openTabs = useStore(s => s.openTabs);
  const openTabAction = useStore(s => s.openTab);
  const closeTabAction = useStore(s => s.closeTab);
  const syncTabs = useStore(s => s.syncTabs);
  const [text, setText] = useState("");
  // 复跑：从「运行轨迹」带来的问题，填进输入框（不自动发，用户审一眼再发）
  const pendingPrompt = useStore(s => s.pendingPrompt);
  useEffect(() => {
    if (pendingPrompt) {
      setText(pendingPrompt);
      useStore.getState().set({ pendingPrompt: "" });
      setTimeout(() => textRef.current?.focus(), 50);
    }
  }, [pendingPrompt]);
  const [streaming, setStreaming] = useState(false);
  const [isDesktopEnv, setIsDesktopEnv] = useState(false);
  useEffect(() => { setIsDesktopEnv(isDesktop()); }, []);
  const [streamContent, setStreamContent] = useState("");
  const [traceSteps, setTraceSteps] = useState<TraceStep[]>([]);
  const traceRef = useRef<TraceStep[]>([]);
  const [thinkingContent, setThinkingContent] = useState("");
  const [agentSteps, setAgentSteps] = useState<Array<{ tool: string; status: string; detail: string; args?: Record<string, unknown>; duration_ms?: number; id?: string }>>([]);
  // 统一有序事件流（对标 Claude：思考/工具/正文按真实发生顺序交错，而非思考全堆前面）。
  // 每个元素: {kind: "trace"|"tool", node, detail, tool?, status, elapsed_ms, id}
  const [liveTimeline, setLiveTimeline] = useState<Array<{ kind: string; node: string; detail: string; tool?: string; status: "done" | "running" | "error"; elapsed_ms?: number; id: string }>>([]);
  // V50: 任务清单（update_todo 全量覆盖，原地替换渲染；done 时持久化进消息）
  const [todoItems, setTodoItems] = useState<TodoItem[]>([]);
  const todoRef = useRef<TodoItem[]>([]);
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
  const [templateOpen, setTemplateOpen] = useState(false);
  const [docFilter, setDocFilter] = useState<string[]>([]);
  const [retrievalMode, setRetrievalMode] = useState<"auto" | "naive" | "kg" | "mix" | "global">("auto");
  const [streamSources, setStreamSources] = useState<Source[]>([]);

  // v10: Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "n") {
        e.preventDefault();
        useStore.getState().newChat();
      }
      if (e.key === "Escape" && streaming) {
        stopRef.current = true;
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
  }, [streaming, text]);
  const [files, setFiles] = useState<UFile[]>([]);
  const [uploading, setUploading] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const stopRef = useRef(false);
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
  // V103.7 客户端加窗：超长会话默认只渲染最近 MSG_WINDOW 条消息，更早的折叠到按钮后，
  // 把 DOM 体积/渲染开销压下来（治超长对话卡顿）。切会话时重置为加窗。
  const [showAllMsgs, setShowAllMsgs] = useState(false);
  useEffect(() => { setShowAllMsgs(false); }, [sid]);
  useEffect(() => { syncTabs(); }, [sid, syncTabs]);   // V103.90 任何地方设了 sid → 自动开标签

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
  // V103.1: 流式 UI 只在「正在生成的那个会话」里显示。切到别的会话时，原会话仍在后台跑、
  // 结果照常落库；回到该会话再看见它的实时进度。避免 token 串台、避免误锁别的会话。
  const streamingHere = streaming && (streamingConvId === null || sid === streamingConvId);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs.length, streaming, streamContent]);
  const adjustHeight = useCallback(() => { const ta = textRef.current; if (ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 180) + "px"; } }, []);
  useEffect(() => { adjustHeight(); }, [text, adjustHeight]);

  useEffect(() => {
    function handlePaste(e: ClipboardEvent) {
      const items = e.clipboardData?.items; if (!items) return;
      for (let i = 0; i < items.length; i++) { if (items[i].kind === "file") { e.preventDefault(); const f = items[i].getAsFile(); if (f) processFile(f); return; } }
    }
    document.addEventListener("paste", handlePaste);
    return () => document.removeEventListener("paste", handlePaste);
  }, []);

  function handleDragOver(e: React.DragEvent) { e.preventDefault(); setDragOver(true); }
  function handleDragLeave(e: React.DragEvent) { e.preventDefault(); setDragOver(false); }
  function handleDrop(e: React.DragEvent) { e.preventDefault(); setDragOver(false); const fl = e.dataTransfer.files; for (let i = 0; i < fl.length; i++) processFile(fl[i]); }

  async function processFile(f: globalThis.File, opts?: { skipAnalyze?: boolean }) {
    if (!useStore.getState().token) { useStore.getState().set({ loginOpen: true }); return; }
    const fname = f.name; setUploading(prev => [...prev, fname]);
    const fd = new FormData(); fd.append("file", f);
    // V86: 截屏即贴即发——跳过上传时的泛图片分析（定向解读由 /stream 按用户问题做）
    if (opts?.skipAnalyze) fd.append("analyze", "0");
    const h: Record<string, string> = {}; const token = useStore.getState().token; if (token) h["Authorization"] = `Bearer ${token}`;
    // 图片附件本地缩略图（objectURL，发完/移除时回收）
    const isImg = ["png","jpg","jpeg","gif","webp","bmp"].includes((fname.split(".").pop() || "").toLowerCase());
    const dataUrl = isImg ? URL.createObjectURL(f) : undefined;
    try {
      const ctrl = new AbortController(); const tmout = setTimeout(() => ctrl.abort(), 120000);
      const r = await fetch("/api/upload", { method: "POST", headers: h, body: fd, signal: ctrl.signal }); clearTimeout(tmout);
      const data = await r.json();
      const ext = (data.filename || "").split(".").pop()?.toLowerCase() || "";
      const size = f.size > 1048576 ? `${(f.size / 1048576).toFixed(1)}M` : `${Math.round(f.size / 1024)}K`;
      setFiles(prev => [...prev, { name: data.filename, size, ext, text: data.text, dataUrl }]);
    } catch (e: unknown) {
      if (dataUrl) URL.revokeObjectURL(dataUrl);
      if (e instanceof Error && e.name === "AbortError") setFiles(prev => [...prev, { name: fname, size: "超时", ext: "err", text: `[${fname} 上传超时]` }]);
    }
    setUploading(prev => prev.filter(n => n !== fname));
  }

  function removeFileAt(i: number) {
    setFiles(prev => {
      const f = prev[i];
      if (f?.dataUrl) URL.revokeObjectURL(f.dataUrl);
      return prev.filter((_, j) => j !== i);
    });
  }

  // V86: 问答栏截屏（视觉型 Computer Use 入口）——微信式框选+标注，截完直接成为附件。
  // hideSelf 让主窗在截屏瞬间隐身，不会把 HashMM 自己截进去。
  const [capturing, setCapturing] = useState(false);
  const [shotMenu, setShotMenu] = useState(false);   // V94: 截屏方式选择（微信式）
  const [cuMode, setCuMode] = useState(false);   // V92: 电脑操作模式（CU 工具循环，桌面 only）
  const [deepMode, setDeepMode] = useState(false);   // 深度检索（Self-RAG）：走后端 /api/deepsearch
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

  async function send(q?: string, regenerate = false) {
    // V93: 游客点发送 → 弹登录（Marvis 式），不打断输入内容
    if (!useStore.getState().token) { set({ loginOpen: true }); return; }
    if (streaming) return;   // V69: 生成中不发送（但输入框保持可打字——Claude 同款体验）
    const query = q || text.trim();
    if (!query && files.length === 0) return;
    setText(""); if (textRef.current) textRef.current.style.height = "auto";
    const currentFiles = regenerate ? [] : [...files]; if (!regenerate) setFiles([]);
    let id = sid;
    if (!id) {
      id = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
      addSession({ id, title: query ? query.slice(0, 28) : "新对话", messages: [], created: Date.now() });
      isFirstMsgRef.current = true;
      // v10: Create conversation on server + update URL
      createConversation(id, query?.slice(0, 28) || "新对话").catch(() => {});
    }
    // v10: Update URL
    if (typeof window !== "undefined") {
      try { history.pushState({}, "", `/chat/${id}`); } catch (_e) {}
    }
    if (!regenerate) {
      const fns = currentFiles.map(f => f.name);
      // V86: 附件改为结构化 files 元数据（图片直接渲染缩略图卡，刷新后仍在），
      // 不再用 "📎 文件名" 塞进消息正文（旧消息由 UserBubble 兼容解析）。
      addMsg(id, {
        role: "user",
        content: query || "请分析上传的文件",
        ts: Date.now(),
        ...(fns.length > 0 ? { files: fns.map(n => ({ filename: n, download_url: `/api/files/${encodeURIComponent(n)}` })) } : {}),
      });
    }
    // 截屏类附件 text 为空（analyze=0），不进文件上下文——定向解读由后端 vision 步骤做
    const fileCtx = currentFiles.map(f => f.text).filter(t => t && t.trim()).join("\n\n---\n\n") || null;
    const attachmentNames = currentFiles.map(f => f.name);
    currentFiles.forEach(f => { if (f.dataUrl) URL.revokeObjectURL(f.dataUrl); });
    const sendQuery = query || "请分析上传的文件";
    lastQueryRef.current = { query: sendQuery, sid: id };
    setStreaming(true); setStreamContent(""); setTraceSteps([]); traceRef.current = []; setThinkingContent("");
    setAgentSteps([]); setCreatedFiles([]); setAgentIteration(null); setProgress(null); setStreamSources([]);
    setLiveTimeline([]); timelineRef.current = [];
    setTodoItems([]); todoRef.current = [];
    clarifyRef.current = null;
    setLiveOrch(null); liveOrchRef.current = null;
    setLiveDelta(""); setCtxInfo(null);
    useStore.getState().set({ artifactDraft: null });
    stopRef.current = false; set({ loading: true, streamingConvId: id });
    _srLen = 0; _srHtml = "";
    streamContentRef.current = "";
          streamTokenCount.current = 0;
          streamStartTime.current = Date.now();

    // V92: 电脑操作模式 —— LLM 决策走后端，工具在本机执行（hashmmCU），
    // 步骤实时打进现有流式区，完成后整段落库（cu_save）。
    if (cuMode && isDesktopEnv) {
      try {
        const full = await runComputerUse(sendQuery, {
          history: msgs.slice(-8).map(m => ({ role: m.role, content: typeof m.content === "string" ? m.content : "" })),
          onText: (t) => { if (!stopRef.current) setStreamContent(prev => { const n = prev + t; streamContentRef.current = n; return n; }); },
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
        cuSave(id!, sendQuery, finalText).catch(() => { /* 落库失败不打断 */ });
      } catch (e) {
        const errText = "[电脑操作失败] " + ((e as Error)?.message || e);
        addMsg(id!, { id: `cu-${Date.now()}`, role: "assistant", content: errText, created_at: Date.now() / 1000 } as never);
      }
      setStreaming(false); setStreamContent(""); streamContentRef.current = "";
      set({ loading: false, streamingConvId: null });
      return;
    }

    // 深度检索（Self-RAG）：走后端 /api/deepsearch（模型驱动多跳 + 自评 + 自适应再检索 + 忠实度门控）。
    // 非流式：拿到完整答案整段落库，并附 是否通过自评 / 置信度 / 再检索轮数 / 来源。
    if (deepMode) {
      try {
        const r = await deepSearch(sendQuery, { max_hops: 3 });
        const meta = r.degraded ? ""
          : "\n\n---\n" + (r.grounded ? "✓ 已通过自评" : "⚠ 资料可能不足")
            + (r.confidence != null ? ` · 置信度 ${Math.round(r.confidence * 100)}%` : "")
            + (r.rounds ? ` · 再检索 ${r.rounds} 轮` : "");
        addMsg(id!, {
          id: `ds-${Date.now()}`, role: "assistant",
          content: (r.answer || "（无答案）") + meta,
          sources: r.sources || [], trace: r.trace,
          ts: Date.now(), created_at: Date.now() / 1000,
        } as never);
      } catch (e) {
        addMsg(id!, {
          id: `ds-${Date.now()}`, role: "assistant",
          content: "[深度检索失败] " + ((e as Error)?.message || e)
            + "（确认已连后端，且后端启动时带了 HASHMM_SEARCHR1_LORA 等模型环境变量）",
          ts: Date.now(), created_at: Date.now() / 1000,
        } as never);
      }
      setStreaming(false); setStreamContent(""); streamContentRef.current = "";
      set({ loading: false, streamingConvId: null });
      return;
    }

    // v10: Use new conversation endpoint (falls back to old if server doesn't support it)
    const useV10 = true;
    const streamFn = useV10
      ? (cb: Parameters<typeof chatStreamV10>[3]) => chatStreamV10(id!, sendQuery, fileCtx, cb, docFilter, retrievalMode, attachmentNames)
      : (cb: Parameters<typeof chatStream>[3]) => chatStream(sendQuery, id, fileCtx, cb);

    await streamFn({
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
        setStreamContent(prev => {
          const next = prev + cleanToken;
          streamContentRef.current = next;
          // v10: Intermediate save every 3s
          if (streamSaveTimer.current) clearTimeout(streamSaveTimer.current);
          streamSaveTimer.current = setTimeout(() => {
            // v14: Intermediate save — skip if no message ID (server saves on done)
            if (id && streamContentRef.current.length > 50) {
              // Server handles persistence via done event; this is just a safety net
              // saveStreamingMessage requires a msg_id which we don't have client-side
            }
          }, 3000);
          return next;
        });
      },
      onThinking: (data) => {
        if (data.content) setThinkingContent(prev => prev ? prev + "\n" + data.content : data.content!);
      },
      onTodo: (data) => {
        const items = (data.items || []) as TodoItem[];
        setTodoItems(items); todoRef.current = items;
      },
      onDelta: (data) => { if (data.t) setLiveDelta(prev => prev + data.t); },
      onDeltaCommit: () => setLiveDelta(""),
      onFileDelta: (d) => {
        // V55: 右栏逐字写代码——草稿模式直播，权威 file 事件到达后无缝切换为真实预览
        const st = useStore.getState();
        const prev = st.artifactDraft;
        const content = prev && prev.filename === d.filename ? prev.content + d.t : d.t;
        st.set({
          artifactDraft: { filename: d.filename, content },
          artifactPanel: { convId: sid || "", type: "code", filename: d.filename, download_url: "" },
        });
      },
      onCtx: (d) => setCtxInfo(d),
      onStepStart: (step) => {
        setAgentSteps(prev => [...prev, { ...step, status: "running" }]);
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
          if (idx >= 0) { const next = [...prev]; next[idx] = { ...next[idx], ...step }; return next; }
          return [...prev, step];
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
              detail: step.detail || next[realIdx].detail, elapsed_ms: step.duration_ms };
            timelineRef.current = next; return next;
          }
          // 找不到对应 running 项（极端乱序）→ 兜底追加，保证事件不丢
          const next = [...prev, {
            kind: "tool", node: "tool", tool: step.tool, detail: step.detail || "",
            status: doneStatus, elapsed_ms: step.duration_ms,
            id: step.id || `tl-tool-${prev.length}-${Date.now()}`,
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
      onIteration: (data) => setAgentIteration(data),
      onProgress: (data) => setProgress(data),
      onSources: (sources) => setStreamSources(sources),
      onClarify: (data) => { clarifyRef.current = data; },
      onOrchestration: (data: any) => { const o = { strategy: data.strategy, members: (data.members || []).map((m: any) => ({ ...m, status: "pending" })) }; liveOrchRef.current = o; setLiveOrch(o); },
      onSubagent: (data: any) => { const prev = liveOrchRef.current; if (!prev) return; const o = { ...prev, members: prev.members.map((m: any) => m.id === data.id ? { ...m, status: data.status, elapsed_ms: data.elapsed_ms ?? m.elapsed_ms, preview: data.preview ?? m.preview } : m) }; liveOrchRef.current = o; setLiveOrch(o); },
      onDone: (data) => {
        // v21: Notify user when task completes (useful if tab is in background)
        if (document.hidden && data.files?.length) {
          notifyTaskComplete("code_task", `已创建 ${data.files.length} 个文件`);
        }
        if (streamSaveTimer.current) clearTimeout(streamSaveTimer.current);
        setStreamContent(prev => {
          const c = prev || streamContentRef.current || "（空回答）";
          // v11: Only use files from server event — NOT stale createdFiles state
          const msgFiles = data.files?.length ? data.files : undefined;
          const msgThinking = data.thinking || thinkingContent || undefined;
          const msgSteps = data.steps?.length ? data.steps : (agentSteps.length ? agentSteps : undefined);
          // 存有序事件流（思考/工具交错），供历史重放保持 Claude 式交错顺序
          const msgTimeline = timelineRef.current.length ? timelineRef.current.map(e => ({ ...e })) : undefined;
          addMsg(id!, { role: "assistant", content: c, sources: data.sources, trace: [...traceRef.current, ...(data.trace || [])], ts: Date.now(), tokens: data.tokens, files: msgFiles, thinking: msgThinking, steps: msgSteps, timeline: msgTimeline, todo: todoRef.current.length ? [...todoRef.current] : undefined, suggestions: data.suggestions, clarify: clarifyRef.current || undefined, orchestration: liveOrchRef.current || undefined, elapsed_ms: data.elapsed_ms });
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
        setStreaming(false); setTraceSteps([]); setAgentIteration(null); setProgress(null);
        set({ loading: false, streamingConvId: null });
        apiStats().then(s => set({ stats: s })).catch(() => {});
      },
      onError: (error) => {
        if (streamSaveTimer.current) clearTimeout(streamSaveTimer.current);
        // v10: Save partial content if we have any
        const partial = streamContentRef.current;
        if (partial && partial.length > 10) {
          addMsg(id!, { role: "assistant", content: partial + "\n\n*回答生成中断，以上内容可能不完整。*", ts: Date.now() });
        } else {
          addMsg(id!, { role: "assistant", content: `请求失败: ${error}`, ts: Date.now() });
        }
        setStreaming(false); setStreamContent(""); setTraceSteps([]); setProgress(null);
        set({ loading: false, streamingConvId: null });
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
  function handleBranchRegenerate(assistantIdx: number) {
    if (streaming || !session) return;
    // Find the user message that preceded this assistant message
    let userQuery = "";
    for (let i = assistantIdx - 1; i >= 0; i--) {
      if (session.messages[i]?.role === "user") {
        userQuery = session.messages[i].content.split("\n").filter(l => !l.startsWith("📎 ")).join("\n").trim();
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
    const lines = msg.content.split("\n").filter(l => !l.startsWith("📎 "));
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

  function onFileInput(e: React.ChangeEvent<HTMLInputElement>) { const fl = e.target.files; if (!fl) return; e.target.value = ""; for (let i = 0; i < fl.length; i++) processFile(fl[i]); }
  function handleKeyDown(e: React.KeyboardEvent) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } if ((e.ctrlKey || e.metaKey) && e.key === "n") { e.preventDefault(); useStore.getState().newChat(); } }

  const modelName = stats?.active_model && stats.active_model !== "—" ? stats.active_model : "";
  const charCount = text.length;

  return (
    <main className="flex-1 flex flex-col min-w-0 h-full relative" style={{ background: "var(--bg-primary)" }} onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop}>
      {dragOver && <div className="absolute inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(37,99,235,0.08)", border: "3px dashed var(--accent)" }}>
        <div className="flex flex-col items-center gap-3"><Upload size={48} style={{ color: "var(--accent)" }} /><span className="text-lg font-semibold" style={{ color: "var(--accent)" }}>拖放文件到这里上传</span></div>
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

      <header className={`h-11 flex items-center px-4 flex-shrink-0 gap-2 ${isDesktopEnv && !artifactPanel ? "titlebar-safe" : ""}`}>
        {!sbOpen && <button onClick={() => set({ sbOpen: true })} aria-label="展开侧栏" className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"><PanelLeft size={18} style={{ color: "var(--text-tertiary)" }} /></button>}
        <h1 className="text-sm font-medium truncate flex-1" style={{ color: "var(--text-secondary)" }}>{session?.title || "HashMM-RAG"}</h1>
        {modelName && <ModelSwitcher current={modelName} />}
        {msgs.length > 0 && <button onClick={() => { setSearchOpen(true); setTimeout(() => searchRef.current?.focus(), 30); }} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" aria-label="在对话中查找" title="在对话中查找 (Ctrl+F)"><SearchIcon size={16} style={{ color: "var(--text-tertiary)" }} /></button>}
        {msgs.length > 0 && <button onClick={exportChat} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" aria-label="导出对话" title="导出 Markdown"><Download size={16} style={{ color: "var(--text-tertiary)" }} /></button>}
      </header>

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
              <div className="mb-5"><HashMascotHero size={156} /></div>
              <h2 className="text-[28px] font-bold mb-2" style={{ color: "var(--text-primary)" }}>HashMM</h2>
              <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>有什么可以帮你？</p>
              <div className="mt-7 grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-[520px]">
                {[
                  { title: "基于知识库回答", sub: "带出处地回答专业问题", prompt: "基于知识库，回答我接下来的问题，并标注出处。" },
                  { title: "提炼上传的资料", sub: "把文档要点整理成清单", prompt: "把知识库里的资料提炼成结构化要点清单。" },
                  { title: "深度调研一个主题", sub: "多源检索后综合成报告", prompt: "帮我深度调研一个主题：" },
                  { title: "写一段代码", sub: "实现并保存成可运行文件", prompt: "帮我写一段代码，实现：" },
                ].map((s) => (
                  <button key={s.title} onClick={() => send(s.prompt)}
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
              <div className="w-5 h-5 rounded-md flex items-center justify-center text-white text-[9px] font-bold flex-shrink-0 mt-1"
                style={{ background: "linear-gradient(135deg, #2563eb, #7c3aed)" }}>H</div>
              <div className="flex-1 pl-3" style={{ borderLeft: "2px solid var(--accent-light, rgba(37,99,235,0.15))" }}>
                {/* Thinking block (Claude-style, collapsible) */}
                {thinkingContent && (
                  <ThinkingPanel content={thinkingContent} streaming={streaming} defaultOpen />
                )}
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
                <SubAgentPanel orch={liveOrch} />
                {/* v10: Progress bar */}
                {progress && (
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
                {/* v12: Live Agent Execution Log — 统一有序事件流（思考/工具按真实顺序交错，对标 Claude） */}
                {todoItems.length > 0 && <TodoCard items={todoItems} />}
                {liveTimeline.length > 0 && (
                  <>
                    <AgentLog
                      steps={liveTimeline.map((e, i) => ({
                        id: e.id || `live-${i}`,
                        node: e.node,
                        detail: e.detail,
                        tool: e.tool,
                        status: e.status,
                        elapsed_ms: e.elapsed_ms,
                      }))}
                      visible={true}
                      onToggle={() => {}}
                    />
                    {/* V86: 质量徽章随事件实时浮现（vision/检索改写/错误恢复在生成中即可见） */}
                    <QualityBadgesRow steps={liveTimeline} />
                  </>
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
                        <span className="w-4 h-4 rounded flex items-center justify-center text-[8px] font-bold flex-shrink-0"
                          style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{s.id ?? i + 1}</span>
                        <span className="truncate max-w-[140px] font-medium" style={{ color: "var(--text-secondary)" }}>{s.filename || "来源"}</span>
                        {(s.page ?? -1) > 0 && <span style={{ color: "var(--text-tertiary)" }}>p.{s.page}</span>}
                      </span>
                    ))}
                  </div>
                )}
                {/* Streaming content */}
                {streamContent && <div className="text-[14px] leading-[1.85] msg-content" style={{ color: "var(--text-primary)" }} dangerouslySetInnerHTML={{ __html: safeRenderStream(streamContent) + '<span class="streaming-cursor">▍</span>' }} />}
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
          {streamingHere && <div className="flex justify-center mb-2"><button onClick={() => { stopRef.current = true; }} className="flex items-center gap-1.5 px-4 py-1.5 rounded-full text-xs font-medium" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}><Square size={12} /> 停止生成</button></div>}
          {(files.length > 0 || uploading.length > 0) && (
            <div className="flex flex-wrap gap-1.5 mb-2 anim-fade-up">
              {uploading.length > 0 && (
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-[11px]" style={{ background: "var(--accent-light)", border: "1px solid var(--accent)", color: "var(--accent)" }}>
                  <Loader2 size={13} className="animate-spin flex-shrink-0" />
                  <span className="font-medium">
                    {uploading.length === 1 ? uploading[0] : `${uploading.length} 个文件`}
                  </span>
                  <span className="text-[10px] opacity-70">解析中</span>
                  {/* Mini progress bar */}
                  <div className="w-16 h-1 rounded-full overflow-hidden" style={{ background: "var(--accent-mid)" }}>
                    <div className="h-full rounded-full animate-pulse" style={{ background: "var(--accent)", width: "60%" }} />
                  </div>
                </div>
              )}
              {files.map((f, i) => <FileChip key={i} f={f} onRemove={() => removeFileAt(i)} />)}
            </div>
          )}
          {/* v10.0: Redesigned input — textarea + integrated toolbar */}
          <div className="rounded-2xl input-box transition-all" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="flex items-end gap-1 px-3 py-2">
              <textarea ref={textRef} value={text} onChange={e => setText(e.target.value)} onKeyDown={handleKeyDown}
                placeholder={streamingHere ? "正在生成…（可先输入，结束后发送）" : (streaming ? "另一个会话正在后台生成…完成后即可发送" : "有什么想问的？")} rows={1}
                className="flex-1 bg-transparent text-[14px] outline-none resize-none min-w-0 py-1.5 px-1 leading-relaxed"
                style={{ color: "var(--text-primary)", maxHeight: 180 }} />
              {(text.trim() || files.length > 0) && (
                <button onClick={() => send()} disabled={streaming} aria-label="发送"
                  className="p-2 rounded-xl text-white transition-all disabled:opacity-30 flex-shrink-0 mb-0.5"
                  style={{ background: "var(--accent)" }}>
                  <ArrowUp size={16} />
                </button>
              )}
            </div>
            {/* Bottom toolbar — compact icons */}
            <div className="flex items-center gap-0.5 px-2 pb-1.5 pt-0">
              <button onClick={() => fileRef.current?.click()} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }} aria-label="上传文件" title="上传文件">
                <Paperclip size={15} />
              </button>
              {/* V86: 截屏进问答栏（桌面端）——框选+标注，截完即附件，发送时自动做图像理解 */}
              {isDesktopEnv && (
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
                            <span className="w-3 inline-block">{active ? "✓" : ""}</span>{label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
              <input ref={fileRef} type="file" className="hidden" onChange={onFileInput} multiple accept=".txt,.md,.py,.pdf,.json,.csv,.docx,.xlsx,.png,.jpg,.jpeg,.html,.xml,.yaml" />
              <div className="w-px h-4 mx-0.5" style={{ background: "var(--border)" }} />
              <button onClick={() => setRetrievalMode(m => m === "auto" ? "mix" : m === "mix" ? "naive" : m === "naive" ? "kg" : m === "kg" ? "global" : "auto")}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                style={{ color: retrievalMode === "auto" ? "var(--accent)" : "var(--text-tertiary)" }}
                title={`检索模式: ${retrievalMode === "auto" ? "自动" : retrievalMode === "mix" ? "混合" : retrievalMode === "kg" ? "图谱" : retrievalMode === "global" ? "全局" : "向量"}`}>
                {retrievalMode === "auto" ? "自动" : retrievalMode === "mix" ? "混合" : retrievalMode === "kg" ? "图谱" : retrievalMode === "global" ? "全局" : "向量"}
              </button>
              <button onClick={() => setDeepMode(v => !v)}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                style={{ color: deepMode ? "var(--accent)" : "var(--text-tertiary)",
                         background: deepMode ? "var(--bg-tertiary)" : "transparent" }}
                title="深度检索（Self-RAG）：模型驱动多跳 + 自我校验 + 不足自动再检索 + 忠实度门控。需后端启动时带模型环境变量。">
                <Sparkles size={11} /> 深度检索
              </button>
              {isDesktopEnv && (
                <button onClick={() => setCuMode(v => !v)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ color: cuMode ? "var(--accent)" : "var(--text-tertiary)",
                           background: cuMode ? "var(--bg-tertiary)" : "transparent" }}
                  title="电脑操作：AI 调用本机 shell/文件/屏幕工具完成任务（每步可见、可审计）">
                  <Monitor size={11} /> 电脑操作
                </button>
              )}
              {isDesktopEnv && cuMode && (
                <button onClick={() => setReplayOpen(true)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ color: "var(--text-tertiary)" }}
                  title="查看 AI 在本机执行过的每一步操作（点击/输入/按键），供随时核查">
                  <ScrollText size={11} /> 操作记录
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
                if (sid) fetch(`/api/conversations/${sid}/prompt`, {
                  method: "PATCH", headers: {"Content-Type":"application/json"},
                  body: JSON.stringify({prompt: convPrompt})
                });
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
