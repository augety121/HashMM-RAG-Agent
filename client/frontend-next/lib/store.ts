import { create } from "zustand";
import type { Message, Session, Stats, User, TaskContract } from "./types";
import { ensureTab, closeTab as closeTabPure, pruneTab } from "./tabs";
import { isLocale, type Locale } from "./i18n";
import { mergeFeatureContexts, type FeatureContext } from "./chatContext";
import { initialInspectorOpen } from "./inspectorStartup";
import type { BrowserEvidenceLocator } from "./desktop";
import { accountSubject, hydrateAccountSessions, readAccountSessions, writeAccountSessions } from "./accountWorkspaceCache";

export type AuthSessionState =
  | "anonymous"
  | "authenticated"
  | "refreshing"
  | "offline-valid"
  | "reauth-required";

function _desktopAuthBridge(): any {
  return typeof window !== "undefined" ? (window as any).hashmmDesktop : null;
}

function _clearRefreshCredential(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem("hmm_refresh");
  const bridge = _desktopAuthBridge();
  if (bridge?.authSessionClear) bridge.authSessionClear().catch(() => {});
}

function _saveRefreshCredential(refreshToken: string | null, subject = ""): void {
  if (typeof window === "undefined") return;
  const bridge = _desktopAuthBridge();
  if (bridge?.authSessionSave) {
    localStorage.removeItem("hmm_refresh");
    if (refreshToken) bridge.authSessionSave(refreshToken, subject).catch(() => {});
    else bridge.authSessionClear?.().catch(() => {});
    return;
  }
  if (refreshToken) localStorage.setItem("hmm_refresh", refreshToken);
  else localStorage.removeItem("hmm_refresh");
}

/** V295 一次自测运行的全局快照——测试中枢从这里读，退出/重进/刷新都能看到进度与结果。 */
export interface SelfTestRun {
  running: boolean;
  results: Array<Record<string, unknown>>;   // 逐项结果（SelfTestResult[]）
  summary: { pass: number; fail: number; skip: number; total: number } | null;
  progress: { done: number; total: number; cur: string };
  reportMd: string;
  reportPath: string;
  ids: string[];            // 本次要跑的套件 id（按展示顺序）
  startedAt: number;
  ts: number;
}

/** V295 一条会话的后台直播快照——ChatArea 从这里读实时进度，任何时刻重进都能立刻看到。 */
export interface LiveStream {
  streaming: boolean;               // 是否仍在生成
  content: string;                  // 正文（累积）
  thinking: string;                 // 思考过程（累积）
  timeline: Array<Record<string, unknown>>;  // 统一有序事件流（思考/工具交错）
  sources: Array<Record<string, unknown>>;    // 来源
  plan: { goal: string; steps: { n: number; action: string; acceptance: string }[] } | null;
  taskContract: TaskContract | null;
  todo: Array<Record<string, unknown>>;       // 任务清单
  orch: Record<string, unknown> | null;       // 子 agent 编排实时态
  iteration: { current: number; max: number } | null;
  progress: { stage: string; pct: number; msg: string } | null;
  activeTurn?: {
    turn_id: string; conversation_id: string; goal: string; started_at: number;
    status: "running" | "interrupting"; mode: string; steerable: boolean; pending_steers: number;
  } | null;
  query: string;                    // 本轮用户问题（重进时可回显"正在回答：…"）
  startedAt: number;                // 开始时间戳（算已生成多久）
  ts: number;                       // 最后更新时间戳
}

interface S {
  token: string | null; refreshToken: string | null; user: User | null;
  authSessionState: AuthSessionState;
  sessions: Session[]; sid: string | null;
  openTabs: string[];   // V103.90 多标签：同时打开的会话 id 列表
  sbOpen: boolean; setOpen: boolean; adminOpen: boolean; desktopView: "personal" | "work-active" | "work-results" | "work-detail" | "hub-knowledge" | "hub-agents" | "hub-operations" | "hub-device" | "gworkspace" | "canvas-home" | "plugins" | "agents" | "docstudio" | "workbench" | "files" | "terminal" | "browser" | "usage" | "backend" | "remote" | "memory" | "evolution" | "quality" | "audit" | "collab" | "scheduled" | "routing" | "runs" | "discovery" | "advanced" | "selftest" | null; workDetailId: string; workDetailParent: "work-active" | "work-results"; upgradeOpen: boolean; loginOpen: boolean; profileOpen: boolean; helpOpen: boolean; releaseNotesOpen: boolean; bugReportOpen: boolean; settingsTab: string; adminTab: "overview" | "models" | "users" | "kbs" | "logs" | "docs" | "skills" | "kg" | "templates" | "tools" | "settings" | "channels" | "eval" | "validity" | "agents" | "routing" | "advanced" | "scheduled" | "runs" | "quality" | "audit" | "selftest" | "backend" | "usage" | "memory" | "evolution" | "discovery" | "collab"; pendingPrompt: string; pendingRunMode: "" | "deep" | "browser" | "computer" | "team" | "canvas";
  dark: boolean; accent: string; fontSize: number;
  locale: Locale;   // V103.90 界面语言
  stats: Stats | null; loading: boolean; streamingConvId: string | null;
  // V295 后台直播态：把"正在生成"的实时快照按会话 id 挂在全局 store，而不是绑在 ChatArea 局部
  // state 上。这样切走再切回、开新会话、甚至整页刷新后重进，都能立刻看到那条对话仍在生成的
  // 实时进度（思考/正文/工具时间线/来源），而不是一片空白等半天——这是"后台执行、重进可见"的
  // 底座，桌面端与 App（同一套 frontend）一起受益。每条 = 一个会话的直播快照，done/error 即清。
  liveStreams: Record<string, LiveStream>;
  // V295 测试中枢后台态：把自测的运行态（进度/逐项结果/汇总/报告）挂在全局 store 并持久化到
  // localStorage。诉求原话："刚测完退出再打开测试中枢，之前的任务没了"。现在：测试在后台跑、
  // 退出/切页/整页刷新后重进，都能看到上次/正在进行的测试进度与结果，还能继续在别处操作。
  selftest: SelfTestRun | null;
  // V269 离线模式：后端可达性（null=未知/启动中，true=在线，false=离线——壳层代回 502/网络错）。
  // 离线只降级功能（发消息/文件/知识库），绝不影响登录态与本地/云端历史的查看。
  backendOnline: boolean | null;
  // V340 功能上下文桥：面板数据只在内存短驻，随下一次成功 Chat 消费；不落 localStorage。
  featureContexts: FeatureContext[];
  customPrompt: string;
  editingMsg: string | null; // message content being edited
  rightPanelOpen: boolean;   // Chat 右侧统一工作区（上下文 / 浏览器 / 产物）
  inspectorTab: "context" | "browser" | "artifact";
  browserPanel: {
    url: string; title?: string; evidenceId?: string; convId?: string;
    locator?: BrowserEvidenceLocator; verifyOnOpen?: boolean;
  } | null;
  artifactPanel: { convId?: string; type: string; filename: string; download_url: string; pages?: number; outline?: string[] } | null;
  artifactTabs: Array<{ convId?: string; type: string; filename: string; download_url: string; pages?: number; outline?: string[] }>;
  artifactDraft: { filename: string; content: string } | null;  // V55: 右栏逐字直播草稿
  set: (partial: Partial<S>) => void;
  // V295 后台直播：合并式写入某会话的直播快照（reader loop 每次回调都调它，脱离组件生命周期）；
  // clearLive 在 done/error 时移除该会话直播态（最终消息已落入 sessions）。
  pushLive: (convId: string, patch: Partial<LiveStream>) => void;
  clearLive: (convId: string) => void;
  attachFeatureContext: (context: FeatureContext) => void;
  removeFeatureContext: (id: string) => void;
  consumeFeatureContexts: (ids: string[]) => void;
  // V295 测试中枢：合并式更新自测运行快照并持久化到 localStorage（退出/刷新后重进可恢复）。
  setSelftest: (patch: Partial<SelfTestRun> | null) => void;
  addSession: (s: Session) => void;
  addMsg: (sid: string, m: Message) => void;
  updateMsg: (sid: string, idx: number, m: Partial<Message>) => void;
  deleteSession: (sid: string) => void;
  renameSession: (sid: string, title: string) => void;
  togglePin: (sid: string) => void;
  archiveSession: (sid: string, archived: boolean) => void;   // V239 归档/还原
  newChat: () => void;
  openTab: (id: string) => void;
  closeTab: (id: string) => void;
  syncTabs: () => void;
  setLocale: (l: Locale) => void;
  logout: () => void;
  requireReauth: () => void;
}

function storedUser(): User | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem("hmm_user");
    return raw ? JSON.parse(raw) as User : null;
  } catch {
    return null;
  }
}
function loadSessions(user: User | null = storedUser()): Session[] {
  try { return readAccountSessions(user); } catch { return []; }
}
function saveSessions(ss: Session[], user: User | null = storedUser()) {
  try { writeAccountSessions(user, ss); } catch { /* 缓存失败不能阻断 Chat */ }
}

/** Conversation ordering used by every local/cloud reconciliation path.
 *
 * A conversation belongs where it was most recently active, not where it was
 * originally created.  Keeping this in one helper prevents project chats from
 * jumping to an old position after an account switch or an incremental sync.
 */
export function sortConversationSessions(items: Session[]): Session[] {
  return [...items].sort((a, b) => {
    const pin = Number(!!b.pinned) - Number(!!a.pinned);
    if (pin) return pin;
    return (b.content_activity_at || b.created || 0) - (a.content_activity_at || a.created || 0);
  });
}

/** Match the server's meaningful-only sidebar projection for local shells.
 *
 * Feature surfaces may reserve an id before the first durable message/run.
 * Keeping those ids in the local cache must not make them reappear when the
 * server correctly hides them from the recent list.
 */
export function isMeaningfulSession(session: Session): boolean {
  return (session.messages || []).some(message => (
    Boolean(String(message.content || "").trim())
    || ["streaming", "waiting_input", "waiting_approval"].includes(String(message.status || ""))
    || Boolean((message.files || []).length)
    || Boolean(message.run_manifest)
  ));
}

/** Server visibility metadata is authoritative; message bodies are lazy cache. */
export function isSidebarVisibleSession(session: Session): boolean {
  if ((session.visibility_source === "server" || session.visibility_source === "cloud")
      && typeof session.has_durable_content === "boolean") {
    return session.has_durable_content;
  }
  return isMeaningfulSession(session);
}
/** V269：把当前会话列表（含已加载的消息）落盘到本地缓存。
 *  服务器/云端拉到消息后调用它，才能做到"看过一次，后端离线也能看"。 */
export function persistSessions() {
  try { saveSessions(useStore.getState().sessions); } catch { /* 存储满等异常不影响主流程 */ }
}

export const useStore = create<S>((set, get) => ({
  token: null, refreshToken: null, user: null, authSessionState: "anonymous",
  sessions: [], sid: null, openTabs: [],
  sbOpen: true, setOpen: false, adminOpen: false, desktopView: null, workDetailId: "", workDetailParent: "work-active", upgradeOpen: false, loginOpen: false, profileOpen: false, helpOpen: false, releaseNotesOpen: false, bugReportOpen: false, settingsTab: "general", adminTab: "overview", pendingPrompt: "", pendingRunMode: "",
  dark: false, accent: "#2563eb", fontSize: 14, locale: "zh", stats: null, loading: false, streamingConvId: null,
  liveStreams: {},
  // V295 测试中枢：初始从 localStorage 恢复上次运行快照（进程内切页靠 store，整页刷新靠这个）。
  // 恢复时把 running 强制置 false——上次的后台 JS 循环已随页面卸载消失，不该显示成"仍在跑"。
  selftest: (() => {
    if (typeof window === "undefined") return null;
    try {
      const raw = localStorage.getItem("hmm_selftest");
      if (!raw) return null;
      const v = JSON.parse(raw);
      return v && typeof v === "object" ? { ...v, running: false } : null;
    } catch { return null; }
  })(),
  backendOnline: null,
  featureContexts: [],
  customPrompt: "", editingMsg: null,
  // A fresh desktop entry starts with the workspace inspector closed.  Browser,
  // artifact and context actions still open it explicitly for the current task;
  // a stale preference from an earlier process must not cover the Chat canvas.
  rightPanelOpen: initialInspectorOpen(
    typeof window === "undefined" ? null : localStorage.getItem("hmm_right_panel"),
  ),
  inspectorTab: "context", browserPanel: null, artifactPanel: null, artifactTabs: [], artifactDraft: null,

  set: (p) => set(p),
  // V295 后台直播写入：合并快照到 liveStreams[convId]。reader loop 每次回调都调它——即使
  // ChatArea 已卸载（用户切走），store 仍被更新；用户切回/重开时读到的就是最新进度。
  pushLive: (convId, patch) => {
    if (!convId) return;
    const cur = get().liveStreams[convId] || {
      streaming: false, content: "", thinking: "", timeline: [], sources: [],
      plan: null, taskContract: null, todo: [], orch: null, iteration: null, progress: null,
      query: "", startedAt: Date.now(), ts: Date.now(),
    };
    set({ liveStreams: { ...get().liveStreams, [convId]: { ...cur, ...patch, ts: Date.now() } } });
  },
  clearLive: (convId) => {
    if (!convId) return;
    const next = { ...get().liveStreams };
    delete next[convId];
    set({ liveStreams: next });
  },
  attachFeatureContext: (context) => {
    set({ featureContexts: mergeFeatureContexts(get().featureContexts, context) });
  },
  removeFeatureContext: (id) => {
    set({ featureContexts: get().featureContexts.filter(x => x.id !== id) });
  },
  consumeFeatureContexts: (ids) => {
    const used = new Set(ids);
    set({ featureContexts: get().featureContexts.filter(x => !used.has(x.id)) });
  },
  // V295 测试中枢：合并更新自测快照并落 localStorage。传 null 清空。
  setSelftest: (patch) => {
    if (patch === null) {
      set({ selftest: null });
      try { if (typeof window !== "undefined") localStorage.removeItem("hmm_selftest"); } catch { /* */ }
      return;
    }
    const cur = get().selftest || {
      running: false, results: [], summary: null, progress: { done: 0, total: 0, cur: "" },
      reportMd: "", reportPath: "", ids: [], startedAt: Date.now(), ts: Date.now(),
    };
    const next = { ...cur, ...patch, ts: Date.now() };
    set({ selftest: next });
    try {
      if (typeof window !== "undefined") {
        // 结果体可能较大——只缓存必要字段，reportMd 截断防超 localStorage 配额
        const slim = { ...next, reportMd: (next.reportMd || "").slice(0, 200000) };
        localStorage.setItem("hmm_selftest", JSON.stringify(slim));
      }
    } catch { /* 配额满等：内存态仍在，仅整页刷新恢复会缺 */ }
  },
  addSession: (s) => { const ss = [...get().sessions, s]; saveSessions(ss); set({ sessions: ss, sid: s.id }); },
  addMsg: (sid, m) => { const now = Date.now(); const ss = get().sessions.map(s => s.id === sid ? { ...s, messages: [...s.messages, m], updated_at: now, content_activity_at: now } : s); saveSessions(ss); set({ sessions: ss }); },
  updateMsg: (sid, idx, partial) => {
    const ss = get().sessions.map(s => {
      if (s.id !== sid) return s;
      const msgs = [...s.messages];
      if (idx >= 0 && idx < msgs.length) msgs[idx] = { ...msgs[idx], ...partial };
      const now = Date.now();
      return { ...s, messages: msgs, updated_at: now, content_activity_at: now };
    });
    saveSessions(ss); set({ sessions: ss });
  },
  deleteSession: (sid) => {
    const ss = get().sessions.filter(s => s.id !== sid); saveSessions(ss);
    const r = pruneTab(get().openTabs, sid, get().sid);   // V103.90 删会话→摘标签并切相邻
    set({ sessions: ss, openTabs: r.tabs, sid: get().sid === sid ? r.nextActive : get().sid });
  },
  togglePin: (sid) => {
    const ss = get().sessions.map(s => s.id === sid ? { ...s, pinned: !s.pinned } : s);
    saveSessions(ss); set({ sessions: ss });
  },
  archiveSession: (sid, archived) => {   // V239 归档：本地乐观标记（API 由调用方触发）；归档的从主列表隐去
    const ss = get().sessions.map(s => s.id === sid ? { ...s, archived } : s);
    saveSessions(ss);
    const cur = get().sid;
    set({ sessions: ss, sid: (archived && cur === sid) ? null : cur });   // 归档当前会话则切空
  },
  renameSession: (sid, title) => {   // V103.90 会话重命名（乐观本地更新；API 由调用方触发）
    const t = (title || "").trim() || "对话";
    const now = Date.now();
    const ss = get().sessions.map(s => s.id === sid ? { ...s, title: t, updated_at: now, metadata_updated_at: now } : s);
    saveSessions(ss); set({ sessions: ss });
  },
  newChat: () => {
    // “新对话”是全局回到 Chat 的入口。仅清空 sid 会让侧栏高亮已经切换，
    // 但项目/插件/画布等工作空间仍覆盖在主区，造成看似点击失效。
    set({
      sid: null,
      editingMsg: null,
      desktopView: null,
      adminOpen: false,
      setOpen: false,
      pendingPrompt: "",
      pendingRunMode: "",
    });
    try { history.pushState({}, "", "/"); } catch {}
  },
  // V103.90 多标签会话
  openTab: (id) => { if (!id) return; set({ openTabs: ensureTab(get().openTabs, id), sid: id }); },
  closeTab: (id) => { const r = closeTabPure(get().openTabs, id, get().sid); set({ openTabs: r.tabs, sid: get().sid === id ? r.nextActive : get().sid }); },
  syncTabs: () => { const t = ensureTab(get().openTabs, get().sid); if (t.length !== get().openTabs.length) set({ openTabs: t }); },
  setLocale: (l) => { if (typeof window !== "undefined") localStorage.setItem("hmm_locale", l); set({ locale: l }); },
  logout: () => {
    // V93: 游客可浏览架构下，登出/过期 → 弹出登录弹窗而非全屏拦截
    saveSessions(get().sessions, get().user);
    set({ loginOpen: true });
    if (typeof window !== "undefined") {
      localStorage.removeItem("hmm_token"); localStorage.removeItem("hmm_user");
      _clearRefreshCredential(); localStorage.removeItem("hmm_login_at");
      // Legacy global data must never survive a sign-out. Account-scoped
      // records are intentionally retained so this same account can continue
      // local projects after it signs in again.
      localStorage.removeItem("hmm_s");
    }
    set({ token: null, refreshToken: null, user: null, authSessionState: "anonymous", sessions: [], sid: null, openTabs: [], adminOpen: false, desktopView: null, workDetailId: "", featureContexts: [] });
  },
  requireReauth: () => {
    // A server-confirmed credential rejection is distinct from an explicit
    // user logout.  Keep that distinction in state so one rejection produces
    // one actionable login surface instead of an unobservable modal loop.
    saveSessions(get().sessions, get().user);
    if (typeof window !== "undefined") {
      localStorage.removeItem("hmm_token"); localStorage.removeItem("hmm_user");
      _clearRefreshCredential(); localStorage.removeItem("hmm_login_at");
      // Only remove the obsolete global key. The owner-bound cache remains.
      localStorage.removeItem("hmm_s");
    }
    set({
      token: null, refreshToken: null, user: null,
      authSessionState: "reauth-required", loginOpen: true,
      sessions: [], sid: null, openTabs: [], adminOpen: false,
      desktopView: null, workDetailId: "", featureContexts: [],
    });
  },
}));

/**
 * Reconcile the sidebar with the server's conversation list for the CURRENT
 * user. The server is authoritative for which conversations exist and who owns
 * them; the local `hmm_s` cache is only a per-conversation message cache. So we
 * replace the session list with the server's list, reusing any cached messages
 * by id. This removes stale entries (deleted conversations, or another user's
 * conversations left over from a previous login) that used to linger and 404
 * when clicked.
 */
export async function syncConversations(token: string) {
  if (typeof window === "undefined" || !token) return;
  // 先续期：更新/重开后存的 access token 可能已过期，直接拿去拉会 401 → 历史空（"客户端历史不同步"）。
  // 用 refresh token 换新后再拉。动态 import 避免与 api.ts 形成静态循环依赖。
  try { const m = await import("./api"); await m.ensureFreshToken(); } catch { /* */ }
  const tok = useStore.getState().token || token;
  const accountId = String(useStore.getState().user?.id || useStore.getState().user?.username || "account");
  const cursorKey = `hmm_conv_sync_${accountId.replace(/[^\w.-]/g, "_")}`;
  const tombstoneCursorKey = `hmm_conv_tombstones_${accountId.replace(/[^\w.-]/g, "_")}`;
  const cursor = localStorage.getItem(cursorKey) || "";
  let offline = false;   // V269：后端不可达（网络错 / 壳层代回 502·503）→ 走云端兜底
  try {
    // The old implementation accepted the server's default first 50 rows as
    // the complete history.  Project conversations outside that window then
    // disappeared from both "项目" and "最近".  Walk the owner-scoped cursor
    // until the server says the list is complete (bounded defensively).
    const serverConvs: Array<{
      id: string; title?: string; created_at?: number; updated_at?: number;
      content_activity_at?: number; metadata_updated_at?: number;
      project_id?: string; pinned?: number; archived?: number;
      has_durable_content?: number | boolean; visibility_source?: "server";
      last_activity_at?: number; revision?: number; sync_state?: string;
    }> = [];
    let pageCursor = "";
    let data: {
      conversations?: typeof serverConvs;
      sync_cursor?: string;
      page?: { has_more?: boolean; next_cursor?: string | null; next_offset?: number | null };
    } = {};
    let page = 0;
    while (true) {
      const params = new URLSearchParams({ limit: "200" });
      if (page === 0 && cursor) params.set("since", cursor);
      if (pageCursor) params.set("cursor", pageCursor);
      const r = await fetch(`/api/conversations?${params.toString()}`, {
        headers: { Authorization: `Bearer ${tok}` },
      });
      if (r.status === 502 || r.status === 503) { offline = true; throw new Error("offline"); }
      if (!r.ok) return;
      data = await r.json();
      serverConvs.push(...(data?.conversations || []));
      const next = data?.page?.next_cursor;
      if (!data?.page?.has_more || typeof next !== "string" || !next) break;
      if (next === pageCursor) throw new Error("conversation pagination did not advance");
      pageCursor = next;
      page += 1;
      if (page >= 1000) throw new Error("conversation pagination exceeded safety limit");
    }
    if (typeof data?.sync_cursor === "string" && data.sync_cursor) localStorage.setItem(cursorKey, data.sync_cursor);
    let tombstoneIds = new Set<string>();
    try {
      const tombstoneSince = Number(localStorage.getItem(tombstoneCursorKey) || 0);
      const tombstoneResponse = await fetch(
        `/api/conversations/tombstones?since=${encodeURIComponent(String(Number.isFinite(tombstoneSince) ? tombstoneSince : 0))}&limit=2000`,
        { headers: { Authorization: `Bearer ${tok}` } },
      );
      if (tombstoneResponse.ok) {
        const tombstoneData = await tombstoneResponse.json() as {
          tombstones?: Array<{ conversation_id?: string; deleted_at?: number }>;
          next_since?: number;
        };
        tombstoneIds = new Set(
          (tombstoneData.tombstones || []).map(item => String(item.conversation_id || "")).filter(Boolean),
        );
        const nextSince = Number(tombstoneData.next_since || tombstoneSince || 0);
        if (Number.isFinite(nextSince)) localStorage.setItem(tombstoneCursorKey, String(nextSince));
      }
    } catch { /* Main list is still authoritative; tombstones are an offline convergence aid. */ }
    const uniqueServerConvs = [...new Map(serverConvs.map(conv => [conv.id, conv] as const)).values()]
      .filter(conv => !tombstoneIds.has(conv.id));
    const cachedById = new Map<string, Session>(
      useStore.getState().sessions.map(s => [s.id, s] as const),
    );
    const reconciled: Session[] = uniqueServerConvs.map(conv => {
      const cached = cachedById.get(conv.id);
      return {
        id: conv.id,
        title: conv.title || cached?.title || "对话",
        messages: cached?.messages || [],   // reuse cached messages; load on click if empty
        created: conv.created_at ? conv.created_at * 1000 : (cached?.created || Date.now()),
        updated_at: conv.updated_at ? conv.updated_at * 1000 : (cached?.updated_at || cached?.created),
        content_activity_at: (conv.last_activity_at || conv.content_activity_at)
          ? Number(conv.last_activity_at || conv.content_activity_at) * 1000
          : (cached?.content_activity_at || cached?.created),
        metadata_updated_at: conv.metadata_updated_at
          ? conv.metadata_updated_at * 1000 : cached?.metadata_updated_at,
        project_id: typeof conv.project_id === "string" ? conv.project_id : cached?.project_id,
        pinned: conv.pinned === 1,
        has_durable_content: conv.has_durable_content === true || conv.has_durable_content === 1,
        visibility_source: "server" as const,
        revision: Number(conv.revision || cached?.revision || 1),
        sync_state: conv.sync_state || "synced",
        // V241 修复归档 bug 元凶：默认列表(/api/conversations)只返回未归档，此处若丢 archived，
        // 每次 sync 都会把本地已归档标记冲掉，导致"归档一个→sync 后全乱"。
        // 后端已归档的不在 serverConvs 里→保留本地 cached.archived；返回里带 archived 就以它为准。
        archived: conv.archived === 1 ? true : (conv.archived === 0 ? false : cached?.archived),
      };
    });
    // V241: 本地已归档的会话不在默认列表里，单独保留，避免 sync 后"归档的对话凭空消失"
    const serverIds = new Set(reconciled.map(x => x.id));
    const localArchived = useStore.getState().sessions.filter(
      s => s.archived && !serverIds.has(s.id) && !tombstoneIds.has(s.id),
    );
    const localDrafts = useStore.getState().sessions.filter(s =>
      !s.archived && s.visibility_source !== "server" && !serverIds.has(s.id)
      && !tombstoneIds.has(s.id) && isMeaningfulSession(s),
    );
    const merged = sortConversationSessions([...reconciled, ...localArchived, ...localDrafts]);
    useStore.setState({ sessions: merged });
    saveSessions(merged);
    return;
  } catch { offline = true; /* 后端不可达/瞬时失败：进入云端兜底，本地缓存始终保留 */ }
  // ── V269 离线云端历史：后端没启动时，用用户自己的 Supabase JWT 直读云端会话表 ──
  // 后端平时把每个会话/消息实时推到 Supabase（supabase_sync.py，RLS 仅本人可读）。
  // 这里只读不写：换新设备、缓存被清、后端离线——都能看到完整历史列表；
  // 云端补充标题/置顶，本地已缓存的消息原样保留。云端也不可用则保持本地缓存不动。
  if (!offline) return;
  try {
    const { listCloudConversations } = await import("./supabase");
    const rows = await listCloudConversations(tok);
    if (!rows.length) return;
    const cachedById = new Map<string, Session>(useStore.getState().sessions.map(s => [s.id, s] as const));
    const fromCloud: Session[] = rows.map(c => ({
      id: c.id,
      title: c.title || cachedById.get(c.id)?.title || "对话",
      messages: cachedById.get(c.id)?.messages || [],
      created: (c.created_at ? Date.parse(c.created_at) : 0) || cachedById.get(c.id)?.created || Date.now(),
      updated_at: (c.updated_at ? Date.parse(c.updated_at) : 0) || cachedById.get(c.id)?.updated_at,
      // Legacy cloud schemas do not expose the split activity clock. Never use
      // their overloaded updated_at to relabel old Chats as "today".
      content_activity_at: cachedById.get(c.id)?.content_activity_at
        || ((c.created_at ? Date.parse(c.created_at) : 0) || cachedById.get(c.id)?.created),
      project_id: typeof (c as unknown as { project_id?: unknown }).project_id === "string"
        ? (c as unknown as { project_id: string }).project_id
        : cachedById.get(c.id)?.project_id,
      pinned: !!c.pinned,
      archived: c.archived === true ? true : cachedById.get(c.id)?.archived,
      has_durable_content: cachedById.get(c.id)?.has_durable_content ?? true,
      visibility_source: "cloud" as const,
    }));
    const cloudIds = new Set(fromCloud.map(x => x.id));
    const localOnly = useStore.getState().sessions.filter(s => !cloudIds.has(s.id) && isMeaningfulSession(s));
    const merged = sortConversationSessions([...fromCloud, ...localOnly]).slice(0, 10000);
    useStore.setState({ sessions: merged });
    saveSessions(merged);
  } catch { /* 云端也不可用：保留本地缓存 */ }
}

export function init() {
  const ss = loadSessions();

  // v7.0: Proper theme mode detection (light / dark / system)
  let dark = false;
  if (typeof window !== "undefined") {
    const themeMode = localStorage.getItem("hmm_theme_mode") || "light";
    if (themeMode === "system") {
      dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    } else if (themeMode === "dark") {
      dark = true;
    } else {
      dark = false;
    }
    // Persist computed value for CSS toggle
    localStorage.setItem("hmm_dark", dark ? "1" : "");

    // v7.0: Listen for system preference changes (when mode is "system")
    try {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      mq.addEventListener("change", (e) => {
        const currentMode = localStorage.getItem("hmm_theme_mode");
        if (currentMode === "system") {
          const newDark = e.matches;
          useStore.getState().set({ dark: newDark });
          localStorage.setItem("hmm_dark", newDark ? "1" : "");
        }
      });
    } catch { /* matchMedia listener not supported */ }
  }

  const accent = (typeof window !== "undefined" && localStorage.getItem("hmm_accent")) || "#2563eb";
  // App WebView 联动：URL 带 sb_token（App 注入的 Supabase 会话令牌）→ 存为令牌并清理 URL，
  // 让客户端以同一 Supabase 身份自动登录（后端已支持验 Supabase token）。
  if (typeof window !== "undefined") {
    try {
      const sp = new URLSearchParams(window.location.search);
      // V383: reject credentials in URLs. App now supplies only a 60-second,
      // single-use capability which is scrubbed before it is exchanged.
      const sb: string | null = null;
      const code = sp.get("wv_code");
      const original = sp.toString();
      sp.delete("sb_token");
      sp.delete("sb_refresh");
      if (code) sp.delete("wv_code");
      const sanitized = window.location.pathname + (sp.toString() ? "?" + sp.toString() : "") + window.location.hash;
      if (sp.toString() !== original) window.history.replaceState({}, "", sanitized);
      if (code) {
        fetch("/api/auth/webview/consume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code }),
          cache: "no-store",
        }).then(async response => {
          if (!response.ok) throw new Error("webview bootstrap rejected");
          const session = await response.json();
          if (!session?.token) throw new Error("webview bootstrap missing token");
          localStorage.setItem("hmm_token", session.token);
           if (session.refresh_token) _saveRefreshCredential(
             session.refresh_token, String(session.user?.id || session.user?.username || ""),
           );
          if (session.user) localStorage.setItem("hmm_user", JSON.stringify(session.user));
          localStorage.setItem("hmm_login_at", String(Date.now()));
          window.location.replace(sanitized);
        }).catch(() => {
          localStorage.removeItem("hmm_token");
           _clearRefreshCredential();
        });
      }
      if (sb) {
        localStorage.setItem("hmm_token", sb);
        localStorage.setItem("hmm_login_at", String(Date.now()));   // App 注入的会话同样享受 7 天窗口
        sp.delete("sb_token");
        // App 同时注入 refresh token → 存好，客户端才能自己续期（否则几秒后令牌过期/401 就被登出）
        const sbRefresh = sp.get("sb_refresh");
        if (sbRefresh) {
          _saveRefreshCredential(
            sbRefresh,
            String(useStore.getState().user?.id || useStore.getState().user?.username || ""),
          );
          sp.delete("sb_refresh");
        }
        // 立刻把新令牌热推给被控投屏窗（远程中继）——否则它还拿着上次过期的令牌，relay/push 一直 401（黑屏）。
        try { import("@/lib/desktop").then(m => m.getRemote?.()?.updateAccountToken?.(sb)).catch(() => {}); } catch { /* */ }
        const clean = window.location.pathname + (sp.toString() ? "?" + sp.toString() : "") + window.location.hash;
        window.history.replaceState({}, "", clean);
      }
    } catch { /* */ }
  }
  // Session restoration has exactly one refresh path: api.ensureFreshToken().
  // The former age-based branch refreshed Supabase directly while startup API
  // calls refreshed through api.ts. Rotating refresh tokens can only be
  // consumed once, so those concurrent paths intermittently logged users out
  // immediately after a successful password login.
  const token = typeof window !== "undefined" ? localStorage.getItem("hmm_token") : null;
  const refreshToken = typeof window !== "undefined" && !_desktopAuthBridge()
    ? localStorage.getItem("hmm_refresh") : null;
  const customPrompt = (typeof window !== "undefined" && localStorage.getItem("hmm_prompt")) || "";
  const fontSize = typeof window !== "undefined" ? parseInt(localStorage.getItem("hmm_fontsize") || "14", 10) : 14;
  const savedLocale = typeof window !== "undefined" ? localStorage.getItem("hmm_locale") : null;
  const locale: Locale = isLocale(savedLocale) ? savedLocale : "zh";
  let user: User | null = null;
  try { const u = typeof window !== "undefined" ? localStorage.getItem("hmm_user") : null; if (u) user = JSON.parse(u); } catch {}
  // V217: 恢复用户显式收起的侧栏形态（收起=52px 图标栏）。只在桌面宽度读——
  // 手机上 App.tsx 会按宽度自动收起且不写该键，避免移动端行为污染桌面偏好。
  let sbOpen = true;
  try {
    if (typeof window !== "undefined" && window.innerWidth >= 768) {
      sbOpen = localStorage.getItem("hmm_sb") !== "0";
    }
  } catch { /* */ }
  // V222: URL 直达桌面视图（App 工作台"桌面模块"入口用）：?view=usage|audit|runs|scheduled|
  // quality|evolution|advanced|memory|routing|discovery（不含 terminal/workbench 等桌面强依赖项）。
  let bootView: S["desktopView"] = null;
  try {
    const v = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("view") : null;
    const allow = ["personal", "hub-knowledge", "hub-agents", "hub-operations", "hub-device", "usage", "audit", "runs", "scheduled", "quality", "evolution", "advanced", "memory", "routing", "discovery", "gworkspace", "canvas-home", "plugins", "agents", "docstudio", "selftest"] as const;
    if (v && (allow as readonly string[]).includes(v)) bootView = v as S["desktopView"];
    // V259: 手机 WebView（App 直达）窄屏时自动收起侧栏——桌面布局不再挤成一条缝
    if (v && typeof window !== "undefined" && window.innerWidth < 768) sbOpen = false;
  } catch { /* */ }
  useStore.setState({
    sessions: ss, dark, accent, token, refreshToken, user,
    authSessionState: token ? "authenticated" : "anonymous",
    customPrompt, fontSize, locale, sbOpen,
    ...(bootView ? { desktopView: bootView } : {}),
  });
  if (user) {
    const expected = accountSubject(user);
    hydrateAccountSessions(user).then((hydrated) => {
      const currentUser = useStore.getState().user;
      if (!hydrated.length || accountSubject(currentUser) !== expected) return;
      const current = useStore.getState().sessions;
      const bodies = new Map(hydrated.map(session => [session.id, session.messages]));
      useStore.setState({
        sessions: current.map(session => bodies.has(session.id)
          ? { ...session, messages: bodies.get(session.id) || [] }
          : session),
      });
    }).catch(() => {});
  }
  if (typeof window !== "undefined") document.documentElement.style.setProperty("--msg-font-size", `${fontSize}px`);

  // v11: Sync conversations from server — the server list is authoritative for
  // the current user. syncConversations() replaces the sidebar with that list
  // (reusing cached messages), pruning stale / other-user / deleted entries
  // that previously lingered and 404'd on click.
  if (typeof window !== "undefined" && token) {
    // 启动先续期再同步：更新/重开后存的 access token 常已过期。先用 refresh token 换新，
    // 否则历史拉不到、且随后各请求 401 会把你登出——这就是"每次更新都要重新登录"的根因。
    import("./api").then(m => m.ensureFreshToken(600).then(() => {
      syncConversations(useStore.getState().token || token);
    })).catch(() => { syncConversations(token); });
  }
}

export function saveAuth(token: string, user: User, refreshToken?: string) {
  const previous = useStore.getState();
  saveSessions(previous.sessions, previous.user);
  const nextRefresh = typeof refreshToken === "string" && refreshToken.trim()
    ? refreshToken.trim() : null;
  localStorage.setItem("hmm_token", token); localStorage.setItem("hmm_user", JSON.stringify(user));
  _saveRefreshCredential(nextRefresh, String(user.id || user.username || ""));
  // V203: 记录本次密码登录时间 —— 7 天内静默续期免登录（与 App 一致），超期才要求重登。
  localStorage.setItem("hmm_login_at", String(Date.now()));
  // Switching users loads only that account's local workspace. Server sync
  // still performs owner-authoritative reconciliation, but it no longer
  // destroys the local continuity of the account we just left.
  localStorage.removeItem("hmm_s");
  const cached = readAccountSessions(user);
  useStore.setState({
    token,
    refreshToken: _desktopAuthBridge() ? null : nextRefresh,
    user,
    authSessionState: "authenticated",
    sessions: cached,
    sid: null,
    loginOpen: false,
  });
  hydrateAccountSessions(user).then((hydrated) => {
    if (!hydrated.length || useStore.getState().user?.id !== user.id) return;
    useStore.setState({ sessions: hydrated, sid: null });
  }).catch(() => {});
  syncConversations(token);
}

/** Update the access (and optionally refresh) token in place — used by the
 *  silent-refresh flow. Does NOT touch the conversation list. */
export function setTokens(token: string, refreshToken?: string) {
  const hasRefreshUpdate = refreshToken !== undefined;
  const nextRefresh = hasRefreshUpdate
    ? (typeof refreshToken === "string" && refreshToken.trim() ? refreshToken.trim() : null)
    : useStore.getState().refreshToken;
  if (typeof window !== "undefined") {
    localStorage.setItem("hmm_token", token);
    if (hasRefreshUpdate) {
      _saveRefreshCredential(
        nextRefresh,
        String(useStore.getState().user?.id || useStore.getState().user?.username || ""),
      );
    }
    // V266: 每次成功刷新令牌都滚动更新登录时间戳——这是"用了几天后点卡片被要求
    // 重新登录"的根因。此前只有初次登录写 hmm_login_at，7 天滑动窗口从不续期，
    // 活跃用户到第 7 天必被登出。现在只要在用（能刷新出新令牌），窗口就往后滚。
    localStorage.setItem("hmm_login_at", String(Date.now()));
  }
  useStore.setState({
    token,
    refreshToken: _desktopAuthBridge() ? null : nextRefresh,
    authSessionState: "authenticated",
  });
  // Token rotation must reach the privileged desktop services immediately.
  // Waiting for App's periodic keepalive leaves dispatch/remote heartbeats on
  // the consumed token and makes the App show “等待电脑” for the same account.
  if (typeof window !== "undefined") {
    import("@/lib/desktop")
      .then(m => m.getRemote?.()?.updateAccountToken?.(token))
      .catch(() => {});
  }
}
