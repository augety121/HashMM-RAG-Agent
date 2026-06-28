"use client";
import { useEffect, useState, Component, ReactNode } from "react";
import { useStore, init, setTokens, syncConversations } from "@/lib/store";
import { closeArtifactIfNotConv } from "@/lib/artifact";
import { stats as fetchStats, loadConversation } from "@/lib/api";
import { refreshSupabaseToken } from "@/lib/supabase";
import type { Message } from "@/lib/types";
import { Sidebar } from "./Sidebar";
import { ChatArea } from "./ChatArea";
import { ArtifactPanel } from "./ArtifactPanel";
import type { ArtifactItem } from "./ArtifactPanel";
import { SettingsModal } from "./SettingsModal";
import { AdminPanel } from "./AdminPanel";
import { DesktopPanel } from "./DesktopPanel";
import { DesktopTitlebar } from "./DesktopTitlebar";
import { getDesktop, getRemote } from "@/lib/desktop";
import { UpgradeModal } from "./UpgradeModal";
import { ProfileModal } from "./ProfileModal";
import { HelpModal, ReleaseNotesModal, BugReportModal } from "./HelpModals";
import { LoginPage } from "./LoginPage";
import { LoginModal } from "./LoginModal";
import { WelcomeModal } from "./WelcomeModal";
import { CloseConfirmModal } from "./CloseConfirmModal";
import { ToastProvider } from "./Toast";
import { showToast } from "@/lib/toast";

// 解析 JWT 的过期时间（秒级 unix）；失败返回 0。用于远程被控保活判断令牌是否临期。
function _jwtExp(t: string): number {
  try { const p = JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))); return typeof p.exp === "number" ? p.exp : 0; }
  catch { return 0; }
}

class ErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  constructor(props: { children: ReactNode }) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(e: Error) { return { error: e.message + "\n" + e.stack }; }
  render() {
    if (this.state.error) return (
      <div style={{ padding: 40, fontFamily: "monospace", whiteSpace: "pre-wrap", color: "#ef4444", fontSize: 12, background: "#09090b", minHeight: "100vh" }}>
        <h2 style={{ fontSize: 16, marginBottom: 12 }}>运行错误</h2>
        <pre style={{ background: "#18181b", padding: 16, borderRadius: 8, overflow: "auto" }}>{this.state.error}</pre>
        <button onClick={() => { this.setState({ error: null }); location.reload(); }}
          style={{ marginTop: 16, padding: "8px 16px", background: "#2563eb", color: "#fff", border: "none", borderRadius: 8, cursor: "pointer" }}>
          重新加载
        </button>
      </div>
    );
    return this.props.children;
  }
}

function AppInner() {
  const { dark, accent, setOpen, adminOpen, upgradeOpen, profileOpen, helpOpen, releaseNotesOpen, bugReportOpen, token, artifactPanel } = useStore();
  const [serverStatus, setServerStatus] = useState<{ ready: boolean; status: string; detail: string }>({ ready: false, status: "connecting", detail: "" });
  // 右侧 artifact 面板可拖拽宽度（像 Claude，用户可拉）。范围 320~900px。
  const [panelWidth, setPanelWidth] = useState(480);
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = panelWidth;
    const onMove = (ev: MouseEvent) => {
      // 往左拖变宽（面板在右侧）
      const next = Math.min(900, Math.max(320, startW + (startX - ev.clientX)));
      setPanelWidth(next);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  useEffect(() => {
    init();
    fetchStats().then(s => useStore.getState().set({ stats: s })).catch(() => {});
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      useStore.getState().set({ sbOpen: false });
    }

    // v12: Poll /api/health until server is ready
    let cancelled = false;
    async function pollHealth() {
      while (!cancelled) {
        try {
          const r = await fetch("/api/health");
          if (r.ok) {
            const data = await r.json();
            setServerStatus({ ready: !!data.ready, status: data.status || "unknown", detail: data.detail || "" });
            if (data.ready) return; // Done polling
          }
        } catch {
          setServerStatus({ ready: false, status: "connecting", detail: "连接服务器..." });
        }
        await new Promise(resolve => setTimeout(resolve, 1500));
      }
    }
    pollHealth();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    // v10.0: URL routing — initial deep-link /chat/{id} selects it on mount.
    if (typeof window !== "undefined") {
      const match = window.location.pathname.match(/^\/chat\/(.+)$/);
      if (match) useStore.getState().set({ sid: match[1] });
    }
  }, []);

  useEffect(() => {
    // V104 接力：手机端把对话交接到本桌面端时，自动切到该会话（sid 变更会触发现有的消息加载与自愈）。
    const w = window as unknown as { hashmmHandoff?: { onOpen: (cb: (d: { conv_id?: string; title?: string }) => void) => void } };
    if (!w.hashmmHandoff?.onOpen) return;
    w.hashmmHandoff.onOpen((data) => {
      const cid = data?.conv_id;
      if (cid) useStore.getState().set({ sid: cid });
    });
  }, []);

  // Load a conversation's messages whenever it becomes the active one. Clicking
  // a sidebar item only sets `sid` (history.pushState, no reload) and
  // server-synced sessions start with empty messages — so without this, opening
  // a chat showed the empty welcome screen (the bug being reported). On failure
  // (404 = deleted, or not owned by / not accessible to the current user) we
  // self-heal: drop the stale entry, return home, and tell the user — instead
  // of stranding them on a blank home screen.
  const sid = useStore(s => s.sid);

  // 桌面端 + 已登录：打开客户端即自动常驻「账号直连」被控（类 UU 远程的常驻被控）。
  // V105 让手机端「我的设备」里【始终】看到这台电脑在线、可一键远程，且不会过一会就掉线。
  // 根因（已查实）：被控窗启动时拿到的 Supabase access_token 约 1 小时过期，过期后在线上报静默 401、
  // 设备 45s 后从列表消失。而后端 /api/auth/refresh 只续后端自家 JWT，续不了 Supabase 令牌。
  // 故这里周期性地：①临期就直连 Supabase 续 access_token（空闲也续）②把最新令牌热推给被控窗保活
  // ③被控窗若已不在（崩溃/被关）就重新拉起（看门狗）。失败全部静默，绝不影响其它功能。
  useEffect(() => {
    const d = getDesktop();
    if (!d || !token) return;
    let cancelled = false;
    const remote = getRemote();
    if (!remote) return;

    const arm = async () => {
      try {
        const s = await remote.accountHostStatus?.();
        if (cancelled || (s && s.on)) return;
        await remote.startAccountHost?.(useStore.getState().token || token);
      } catch { /* 远程被控自启失败不影响其它功能 */ }
    };
    const keepAlive = async () => {
      try {
        const cur = useStore.getState().token;
        const rt = useStore.getState().refreshToken;
        // 临近过期（<10 分钟）就直连 Supabase 续期；空闲时也照续，确保被控窗永远有新令牌。
        if (cur && rt && _jwtExp(cur) - Math.floor(Date.now() / 1000) < 600) {
          const fresh = await refreshSupabaseToken(rt);
          if (!cancelled && fresh) setTokens(fresh.access_token, fresh.refresh_token);
        }
        if (cancelled) return;
        const now = useStore.getState().token;
        if (now) remote.updateAccountToken?.(now);   // 把当前（已续期）令牌热推给被控窗
        await arm();                                  // 被控窗不在了就重新拉起
      } catch { /* 静默 */ }
    };

    arm();          // 立即确保已托管
    keepAlive();    // 立即喂一次最新令牌
    const timer = setInterval(keepAlive, 4 * 60 * 1000);  // 之后每 4 分钟保活（远低于 token 1h 寿命）
    return () => { cancelled = true; clearInterval(timer); };
  }, [token]);

  // V50: 右栏与会话关联——切换/新建会话时，面板里挂的若不是当前会话的文件则关闭。
  // 放在这里（而非散落在 Sidebar/新建按钮）是因为 sid 是所有切换路径的汇聚点。
  useEffect(() => { closeArtifactIfNotConv(sid); }, [sid]);
  useEffect(() => {
    if (!sid) return;
    const sess = useStore.getState().sessions.find(s => s.id === sid);
    if (sess && sess.messages.length > 0) return; // already loaded or streaming
    let cancelled = false;
    loadConversation(sid).then(data => {
      if (cancelled) return;
      const conv = data.conversation;
      const msgs = (data.messages || []).map((m: Message) => ({
        role: m.role,
        content: m.content,
        thinking: m.thinking || undefined,
        steps: (m.steps && m.steps.length ? m.steps
          : ((m as any).tool_calls && (m as any).tool_calls.length ? (m as any).tool_calls : undefined)) as any,
        files: m.files || undefined,
        sources: m.sources || undefined,
        suggestions: m.suggestions || undefined,
        ts: m.created_at ? m.created_at * 1000 : (m.ts || Date.now()),
      }));
      const sessions = useStore.getState().sessions;
      if (sessions.find(s => s.id === sid)) {
        useStore.setState({
          sessions: sessions.map(s => s.id === sid
            ? { ...s, title: conv?.title || s.title, messages: msgs }
            : s),
        });
      } else {
        useStore.getState().addSession({
          id: sid,
          title: conv?.title || "对话",
          messages: msgs,
          created: (conv?.created_at || 0) * 1000 || Date.now(),
          pinned: conv?.pinned === 1,
        });
      }
    }).catch(() => {
      if (cancelled) return;
      // deleteSession removes the stale entry, persists hmm_s, and clears sid
      // when it was the active one.
      useStore.getState().deleteSession(sid);
      try { history.pushState({}, "", "/"); } catch { /* no history API */ }
      showToast("对话不存在或无权访问，已从列表移除", "warning");
    });
    return () => { cancelled = true; };
  }, [sid]);

  // 跨端联动：App 上发的消息，桌面这边也要自动出现（两端共用同一 Supabase）。
  // 桌面前端没装 supabase-js，这里用轻量轮询实现自动同步（大厂在不便接 WebSocket 时也常用短轮询）：
  // 每 7 秒、且页面可见、且当前没有在流式输出时——
  //   ① 刷新会话列表（App 新建的对话自动出现在侧栏，复用现成对账逻辑，安全）；
  //   ② 重拉当前打开会话的消息，仅当服务端比本地“多”（对端有新增）时才覆盖，绝不打断本地流式、不抖动。
  useEffect(() => {
    if (typeof window === "undefined") return;
    let busy = false;
    const tick = async () => {
      if (busy || document.visibilityState === "hidden") return;
      const tok = useStore.getState().token;
      if (!tok) return;
      busy = true;
      try {
        await syncConversations(tok);                                   // ① 侧栏
        const curSid = useStore.getState().sid;
        if (curSid && useStore.getState().streamingConvId !== curSid) {  // ② 不在流式才动消息
          const data = await loadConversation(curSid).catch(() => null);
          if (data && Array.isArray(data.messages)) {
            const mapped = (data.messages as Message[]).map((m: Message) => ({
              role: m.role,
              content: m.content,
              thinking: m.thinking || undefined,
              steps: (m.steps && m.steps.length ? m.steps
                : ((m as any).tool_calls && (m as any).tool_calls.length ? (m as any).tool_calls : undefined)) as any,
              files: m.files || undefined,
              sources: m.sources || undefined,
              suggestions: m.suggestions || undefined,
              ts: m.created_at ? m.created_at * 1000 : (m.ts || Date.now()),
            }));
            const sessions = useStore.getState().sessions;
            const cur = sessions.find(s => s.id === curSid);
            // 仅当对端确实新增了消息、且此刻仍未在流式 → 覆盖（避免打断/抖动/回退）
            if (cur && mapped.length > cur.messages.length &&
                useStore.getState().streamingConvId !== curSid) {
              useStore.setState({
                sessions: sessions.map(s => s.id === curSid ? { ...s, messages: mapped } : s),
              });
            }
          }
        }
      } catch { /* 离线/瞬时失败：忽略，下轮再试 */ }
      finally { busy = false; }
    };
    const iv = setInterval(tick, 7000);
    return () => clearInterval(iv);
  }, []);

  // Listen for resize to auto-collapse
  useEffect(() => {
    if (typeof window === "undefined") return;
    function onResize() {
      if (window.innerWidth < 768 && useStore.getState().sbOpen) {
        useStore.getState().set({ sbOpen: false });
      }
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    document.body.style.background = dark ? "#09090b" : "#ffffff";
    try { getDesktop()?.setOverlay?.(dark); } catch {}
  }, [dark]);

  useEffect(() => {
    document.documentElement.style.setProperty("--accent", accent);
    const r = parseInt(accent.slice(1, 3), 16);
    const g = parseInt(accent.slice(3, 5), 16);
    const b = parseInt(accent.slice(5, 7), 16);
    document.documentElement.style.setProperty("--accent-light", `rgba(${r},${g},${b},0.08)`);
    document.documentElement.style.setProperty("--accent-mid", `rgba(${r},${g},${b},0.15)`);
  }, [accent]);

  useEffect(() => {
    // V64: 桌面端菜单导航（终端/设置）→ 同一套 UI 内切换
    const d = getDesktop();
    if (!d || !d.onNav) return;
    const off = d.onNav((view) => {
      if (view === "terminal") useStore.getState().set({ desktopView: "terminal" } as any);
      else if (view === "settings") useStore.getState().set({ setOpen: true });
      else if (view === "files" || view === "usage") useStore.getState().set({ desktopView: view } as any);
    });
    return off;
  }, []);

  // V93: Marvis 式——未登录也进主工作台（游客可浏览），登录走居中遮罩弹窗

  return (
    <div className="flex flex-col h-screen overflow-hidden" style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* V65: 桌面端无边框窗口的自定义标题栏（web 打开为 null） */}
      <DesktopTitlebar />
      <LoginModal />
      <WelcomeModal />
      <div className="flex flex-1 min-h-0 overflow-hidden">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 h-full overflow-hidden">
        {/* v12: Server loading status banner */}
        {!serverStatus.ready && (
          <div className="flex-shrink-0 flex items-center gap-3 px-4 py-2" style={{ background: "var(--accent-light)", borderBottom: "1px solid var(--border)" }}>
            <div className="w-3 h-3 rounded-full animate-pulse" style={{ background: "var(--accent)" }} />
            <span className="text-[12px] font-medium" style={{ color: "var(--accent)" }}>
              {serverStatus.status === "connecting" ? "连接服务器..." :
               serverStatus.status === "loading_models" ? "加载 AI 模型..." :
               serverStatus.status === "loading_index" ? "加载检索索引..." :
               serverStatus.status === "loading_llm" ? "连接 LLM 服务..." :
               "初始化中..."}
            </span>
            {serverStatus.detail && <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{serverStatus.detail}</span>}
            <div className="flex-1 h-1 rounded-full overflow-hidden ml-2" style={{ background: "var(--bg-tertiary)" }}>
              <div className="h-full rounded-full transition-all duration-700"
                style={{
                  background: "var(--accent)",
                  width: serverStatus.status === "loading_models" ? "30%" :
                         serverStatus.status === "loading_index" ? "60%" :
                         serverStatus.status === "loading_llm" ? "85%" : "15%",
                }} />
            </div>
          </div>
        )}
        <div className="flex flex-1 min-w-0 h-full overflow-hidden">
          <div className={`${artifactPanel ? "flex-1 min-w-0" : "flex-1"} h-full overflow-hidden`}>
            <ChatArea />
          </div>
          {artifactPanel && (
            <>
              {/* 可拖拽分隔条：鼠标按住左右拖动调节面板宽度（对标 Claude） */}
              <div
                onMouseDown={startResize}
                className="hidden lg:block flex-shrink-0 cursor-col-resize group"
                style={{ width: "6px" }}
                title="拖动调整宽度"
              >
                <div className="h-full w-[2px] mx-auto transition-colors group-hover:bg-[var(--accent)]"
                     style={{ background: "var(--border)" }} />
              </div>
              <div className="flex-shrink-0 hidden lg:flex h-full" style={{ width: `${panelWidth}px` }}>
                <ArtifactPanel
                  artifact={artifactPanel as ArtifactItem}
                  onClose={() => useStore.getState().set({ artifactPanel: null })}
                />
              </div>
            </>
          )}
        </div>
      </div>
      {setOpen && <SettingsModal />}
      {adminOpen && <AdminPanel />}
      <DesktopPanel />
      {upgradeOpen && <UpgradeModal />}
      {profileOpen && <ProfileModal />}
      {helpOpen && <HelpModal />}
      {releaseNotesOpen && <ReleaseNotesModal />}
      {bugReportOpen && <BugReportModal />}
      <CloseConfirmModal />
      </div>
    </div>
  );
}

export default function App() {
  return <ErrorBoundary><ToastProvider><AppInner /></ToastProvider></ErrorBoundary>;
}
