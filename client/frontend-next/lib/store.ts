import { create } from "zustand";
import type { Message, Session, Stats, User } from "./types";
import { ensureTab, closeTab as closeTabPure, pruneTab } from "./tabs";
import { isLocale, type Locale } from "./i18n";

interface S {
  token: string | null; refreshToken: string | null; user: User | null;
  sessions: Session[]; sid: string | null;
  openTabs: string[];   // V103.90 多标签：同时打开的会话 id 列表
  sbOpen: boolean; setOpen: boolean; adminOpen: boolean; desktopView: "workbench" | "files" | "terminal" | "usage" | "backend" | "remote" | "memory" | "evolution" | "quality" | "audit" | "scheduled" | "routing" | "runs" | "discovery" | null; upgradeOpen: boolean; loginOpen: boolean; profileOpen: boolean; helpOpen: boolean; releaseNotesOpen: boolean; bugReportOpen: boolean; settingsTab: string; adminTab: "models" | "users" | "kbs" | "logs" | "docs" | "skills" | "kg" | "templates" | "tools" | "settings" | "channels" | "eval" | "validity"; pendingPrompt: string;
  dark: boolean; accent: string; fontSize: number;
  locale: Locale;   // V103.90 界面语言
  stats: Stats | null; loading: boolean; streamingConvId: string | null;
  customPrompt: string;
  editingMsg: string | null; // message content being edited
  artifactPanel: { convId?: string; type: string; filename: string; download_url: string; pages?: number; outline?: string[] } | null;
  artifactDraft: { filename: string; content: string } | null;  // V55: 右栏逐字直播草稿
  set: (partial: Partial<S>) => void;
  addSession: (s: Session) => void;
  addMsg: (sid: string, m: Message) => void;
  updateMsg: (sid: string, idx: number, m: Partial<Message>) => void;
  deleteSession: (sid: string) => void;
  renameSession: (sid: string, title: string) => void;
  togglePin: (sid: string) => void;
  newChat: () => void;
  openTab: (id: string) => void;
  closeTab: (id: string) => void;
  syncTabs: () => void;
  setLocale: (l: Locale) => void;
  logout: () => void;
}

function loadSessions(): Session[] {
  try { if (typeof window === "undefined") return []; return JSON.parse(localStorage.getItem("hmm_s") || "[]"); } catch { return []; }
}
function saveSessions(ss: Session[]) {
  if (typeof window !== "undefined") localStorage.setItem("hmm_s", JSON.stringify(ss.slice(-50)));
}

export const useStore = create<S>((set, get) => ({
  token: null, refreshToken: null, user: null, sessions: [], sid: null, openTabs: [],
  sbOpen: true, setOpen: false, adminOpen: false, desktopView: null, upgradeOpen: false, loginOpen: false, profileOpen: false, helpOpen: false, releaseNotesOpen: false, bugReportOpen: false, settingsTab: "general", adminTab: "models", pendingPrompt: "",
  dark: false, accent: "#2563eb", fontSize: 14, locale: "zh", stats: null, loading: false, streamingConvId: null,
  customPrompt: "", editingMsg: null, artifactPanel: null, artifactDraft: null,

  set: (p) => set(p),
  addSession: (s) => { const ss = [...get().sessions, s]; saveSessions(ss); set({ sessions: ss, sid: s.id }); },
  addMsg: (sid, m) => { const ss = get().sessions.map(s => s.id === sid ? { ...s, messages: [...s.messages, m] } : s); saveSessions(ss); set({ sessions: ss }); },
  updateMsg: (sid, idx, partial) => {
    const ss = get().sessions.map(s => {
      if (s.id !== sid) return s;
      const msgs = [...s.messages];
      if (idx >= 0 && idx < msgs.length) msgs[idx] = { ...msgs[idx], ...partial };
      return { ...s, messages: msgs };
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
  renameSession: (sid, title) => {   // V103.90 会话重命名（乐观本地更新；API 由调用方触发）
    const t = (title || "").trim() || "对话";
    const ss = get().sessions.map(s => s.id === sid ? { ...s, title: t } : s);
    saveSessions(ss); set({ sessions: ss });
  },
  newChat: () => { set({ sid: null, editingMsg: null }); try { history.pushState({}, "", "/"); } catch {} },
  // V103.90 多标签会话
  openTab: (id) => { if (!id) return; set({ openTabs: ensureTab(get().openTabs, id), sid: id }); },
  closeTab: (id) => { const r = closeTabPure(get().openTabs, id, get().sid); set({ openTabs: r.tabs, sid: get().sid === id ? r.nextActive : get().sid }); },
  syncTabs: () => { const t = ensureTab(get().openTabs, get().sid); if (t.length !== get().openTabs.length) set({ openTabs: t }); },
  setLocale: (l) => { if (typeof window !== "undefined") localStorage.setItem("hmm_locale", l); set({ locale: l }); },
  logout: () => {
    // V93: 游客可浏览架构下，登出/过期 → 弹出登录弹窗而非全屏拦截
    set({ loginOpen: true });
    if (typeof window !== "undefined") {
      localStorage.removeItem("hmm_token"); localStorage.removeItem("hmm_user");
      localStorage.removeItem("hmm_refresh");
      // Clear the cached conversation list too — otherwise the next user who
      // logs in inherits the previous user's sidebar (stale, cross-user, and
      // full of entries that 404 on click).
      localStorage.removeItem("hmm_s");
    }
    set({ token: null, refreshToken: null, user: null, sessions: [], sid: null, openTabs: [], adminOpen: false });
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
  try {
    const r = await fetch("/api/conversations", { headers: { Authorization: `Bearer ${tok}` } });
    if (!r.ok) return;
    const data = await r.json();
    const serverConvs: Array<{ id: string; title?: string; created_at?: number; pinned?: number }> =
      data?.conversations || [];
    const cachedById = new Map<string, Session>(
      useStore.getState().sessions.map(s => [s.id, s] as const),
    );
    const reconciled: Session[] = serverConvs.map(conv => {
      const cached = cachedById.get(conv.id);
      return {
        id: conv.id,
        title: conv.title || cached?.title || "对话",
        messages: cached?.messages || [],   // reuse cached messages; load on click if empty
        created: conv.created_at ? conv.created_at * 1000 : (cached?.created || Date.now()),
        pinned: conv.pinned === 1,
      };
    }).sort((a, b) => b.created - a.created).slice(0, 100);
    useStore.setState({ sessions: reconciled });
    saveSessions(reconciled);
  } catch { /* offline / transient — keep whatever is cached */ }
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
      const sb = sp.get("sb_token");
      if (sb) {
        localStorage.setItem("hmm_token", sb);
        sp.delete("sb_token");
        // App 同时注入 refresh token → 存好，客户端才能自己续期（否则几秒后令牌过期/401 就被登出）
        const sbRefresh = sp.get("sb_refresh");
        if (sbRefresh) { localStorage.setItem("hmm_refresh", sbRefresh); sp.delete("sb_refresh"); }
        // 立刻把新令牌热推给被控投屏窗（远程中继）——否则它还拿着上次过期的令牌，relay/push 一直 401（黑屏）。
        try { import("@/lib/desktop").then(m => m.getRemote?.()?.updateAccountToken?.(sb)).catch(() => {}); } catch { /* */ }
        const clean = window.location.pathname + (sp.toString() ? "?" + sp.toString() : "") + window.location.hash;
        window.history.replaceState({}, "", clean);
      }
    } catch { /* */ }
  }
  const token = typeof window !== "undefined" ? localStorage.getItem("hmm_token") : null;
  const refreshToken = typeof window !== "undefined" ? localStorage.getItem("hmm_refresh") : null;
  const customPrompt = (typeof window !== "undefined" && localStorage.getItem("hmm_prompt")) || "";
  const fontSize = typeof window !== "undefined" ? parseInt(localStorage.getItem("hmm_fontsize") || "14", 10) : 14;
  const savedLocale = typeof window !== "undefined" ? localStorage.getItem("hmm_locale") : null;
  const locale: Locale = isLocale(savedLocale) ? savedLocale : "zh";
  let user: User | null = null;
  try { const u = typeof window !== "undefined" ? localStorage.getItem("hmm_user") : null; if (u) user = JSON.parse(u); } catch {}
  useStore.setState({ sessions: ss, dark, accent, token, refreshToken, user, customPrompt, fontSize, locale });
  if (typeof window !== "undefined") document.documentElement.style.setProperty("--msg-font-size", `${fontSize}px`);

  // v11: Sync conversations from server — the server list is authoritative for
  // the current user. syncConversations() replaces the sidebar with that list
  // (reusing cached messages), pruning stale / other-user / deleted entries
  // that previously lingered and 404'd on click.
  if (typeof window !== "undefined" && token) {
    // 启动先续期再同步：更新/重开后存的 access token 常已过期。先用 refresh token 换新，
    // 否则历史拉不到、且随后各请求 401 会把你登出——这就是"每次更新都要重新登录"的根因。
    import("./api").then(m => m.ensureFreshToken().then(() => {
      syncConversations(useStore.getState().token || token);
    })).catch(() => { syncConversations(token); });
  }
}

export function saveAuth(token: string, user: User, refreshToken?: string) {
  localStorage.setItem("hmm_token", token); localStorage.setItem("hmm_user", JSON.stringify(user));
  if (refreshToken) localStorage.setItem("hmm_refresh", refreshToken);
  // Switching users: drop the previous user's cached sidebar immediately, then
  // pull the new user's conversations from the server. Without this, the
  // sidebar kept showing the prior user's chats until a manual refresh, and
  // clicking them 404'd.
  localStorage.removeItem("hmm_s");
  useStore.setState({ token, refreshToken: refreshToken ?? useStore.getState().refreshToken, user, sessions: [], sid: null });
  syncConversations(token);
}

/** Update the access (and optionally refresh) token in place — used by the
 *  silent-refresh flow. Does NOT touch the conversation list. */
export function setTokens(token: string, refreshToken?: string) {
  if (typeof window !== "undefined") {
    localStorage.setItem("hmm_token", token);
    if (refreshToken) localStorage.setItem("hmm_refresh", refreshToken);
  }
  useStore.setState({ token, refreshToken: refreshToken ?? useStore.getState().refreshToken });
}
