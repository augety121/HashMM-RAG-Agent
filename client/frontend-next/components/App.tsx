"use client";
import { useEffect, useState, Component, ReactNode } from "react";
import { useStore, init, syncConversations, persistSessions } from "@/lib/store";
import { closeArtifactIfNotConv } from "@/lib/artifact";
import { stats as fetchStats, loadConversation, probeBackend, ensureFreshToken, flushWorkCommandOutbox } from "@/lib/api";
import { getCloudMessages } from "@/lib/supabase";
import type { Message } from "@/lib/types";
import { Sidebar } from "./Sidebar";
import { ChatArea } from "./ChatArea";
import { WorkspaceInspector } from "./WorkspaceInspector";
import { SettingsModal } from "./SettingsModal";
import { AdminPanel } from "./AdminPanel";
import { DesktopPanel } from "./DesktopPanel";
import { DesktopTitlebar } from "./DesktopTitlebar";

import { getCheckpoint, getDesktop, getRemote } from "@/lib/desktop";
import { UpgradeModal } from "./UpgradeModal";
import { ProfileModal } from "./ProfileModal";
import { HelpModal, ReleaseNotesModal, BugReportModal } from "./HelpModals";
import { LoginPage } from "./LoginPage";
import { LoginModal } from "./LoginModal";
import { WelcomeModal } from "./WelcomeModal";
import { CloseConfirmModal } from "./CloseConfirmModal";
import { DesktopPromptModal } from "./DesktopPromptModal";
import { ToastProvider } from "./Toast";
import { showToast } from "@/lib/toast";
import { WifiOff } from "lucide-react";
import { clampInspectorWidth } from "@/lib/workspaceLayout";

// 解析 JWT 的过期时间（秒级 unix）；失败返回 0。用于远程被控保活判断令牌是否临期。
// V295 整页刷新/重开后的"生成中"重连：后端在生成期间每 ~1.5s 把 partial 落库到那条 status=
// streaming 的助手消息。这里轮询会话，把不断增长的 partial 拉回来更新对应气泡，直到该消息 status
// 变为非 streaming（完成/出错），或超过兜底上限（防僵死轮询）。同一会话只跑一个轮询。
const _reattaching = new Set<string>();

function restoredToolCalls(message: Record<string, unknown>): unknown[] | undefined {
  const raw = (Array.isArray(message.steps) && message.steps.length ? message.steps
    : Array.isArray(message.tool_calls) ? message.tool_calls : []) as Array<Record<string, unknown>>;
  const steps = raw.filter(item => item?.node !== "orchestration");
  return steps.length ? steps : undefined;
}

function restoredTimeline(message: Record<string, unknown>): Message["timeline"] | undefined {
  if (Array.isArray(message.timeline) && message.timeline.length) {
    return message.timeline as Message["timeline"];
  }
  const persisted = (message.run_manifest as Message["run_manifest"] | undefined)?.process?.timeline;
  if (Array.isArray(persisted) && persisted.length) {
    return persisted.map(item => ({
      ...item,
      kind: item.kind || "status",
      status: item.status || "done",
    })) as Message["timeline"];
  }
  const raw = (Array.isArray(message.tool_calls) ? message.tool_calls : []) as Array<Record<string, unknown>>;
  const timeline = raw.filter(item => item?.node !== "orchestration").map((item, index) => {
    const rawStatus = String(item.status || item.state || "done");
    const status = rawStatus === "running" || rawStatus === "error" || rawStatus === "failed"
      ? (rawStatus === "failed" ? "error" : rawStatus) : "done";
    return {
      id: String(item.id || `persisted-${index}`),
      kind: item.tool ? "tool" : "trace",
      node: String(item.node || item.tool || "step"),
      tool: item.tool ? String(item.tool) : undefined,
      detail: String(item.detail || ""),
      status,
      elapsed_ms: typeof item.elapsed_ms === "number" ? item.elapsed_ms
        : typeof item.duration_ms === "number" ? item.duration_ms : undefined,
      hooks: Array.isArray(item.hooks) ? item.hooks as import("@/lib/types").HookRun[] : undefined,
    };
  });
  return timeline.length ? timeline : undefined;
}

function restoredTodo(message: Record<string, unknown>): Message["todo"] | undefined {
  if (Array.isArray(message.todo) && message.todo.length) {
    return message.todo as Message["todo"];
  }
  const persisted = (message.run_manifest as Message["run_manifest"] | undefined)?.process?.todo;
  return Array.isArray(persisted) && persisted.length
    ? persisted.map(item => ({
        text: String(item.text || ""),
        status: String(item.status || "pending"),
      }))
    : undefined;
}

function restoredOrchestration(message: Record<string, unknown>): Message["orchestration"] | undefined {
  if (message.orchestration && typeof message.orchestration === "object") {
    return message.orchestration as Message["orchestration"];
  }
  const raw = (Array.isArray(message.tool_calls) ? message.tool_calls : []) as Array<Record<string, unknown>>;
  const record = raw.find(item => item?.node === "orchestration");
  if (!record || !Array.isArray(record.members)) return undefined;
  return {
    team_id: record.team_id ? String(record.team_id) : undefined,
    strategy: String(record.strategy || ""),
    status: record.status ? String(record.status) as NonNullable<Message["orchestration"]>["status"] : undefined,
    retry_of: record.retry_of ? String(record.retry_of) : undefined,
    members: record.members as NonNullable<Message["orchestration"]>["members"],
  };
}

function reattachStreaming(convId: string): void {
  if (!convId || _reattaching.has(convId)) return;
  _reattaching.add(convId);
  let ticks = 0;
  const MAX_TICKS = 400;   // 400 × 1.5s ≈ 10 分钟兜底上限
  const timer = setInterval(async () => {
    ticks++;
    // 切走到别的会话就停（回来会重新触发）；超时也停
    if (ticks > MAX_TICKS || useStore.getState().sid !== convId) {
      clearInterval(timer); _reattaching.delete(convId); return;
    }
    try {
      const data = await loadConversation(convId);
      const raw = (data.messages || []) as unknown as Array<Record<string, unknown>>;
      const last = raw[raw.length - 1] as { role?: string; status?: string; content?: string } | undefined;
      // 把最新消息映射进 store（含增长中的 partial）
      const msgs = raw.map((m) => ({
        role: m.role as string,
        content: (m.content as string) || "",
        // Provider reasoning is private and must not reappear after reload.
        // The durable user-facing process is restored from run_manifest.process.
        thinking: undefined,
        steps: restoredToolCalls(m) as never,
        files: (m.files as unknown[]) || undefined,
        sources: (m.sources as unknown[]) || undefined,
        groundings: (m.groundings as Record<string, unknown>) || undefined,
        run_manifest: (m.run_manifest as Message["run_manifest"]) || undefined,
        timeline: restoredTimeline(m),
        todo: restoredTodo(m),
        orchestration: restoredOrchestration(m),
        elapsed_ms: (m.elapsed_ms as number) || undefined,
        suggestions: (m.suggestions as unknown[]) || undefined,
        status: (m.status as string) || undefined,
        ts: m.created_at ? (m.created_at as number) * 1000 : (m.ts as number) || Date.now(),
      })) as never[];
      const sessions = useStore.getState().sessions;
      if (sessions.find(s => s.id === convId)) {
        useStore.setState({ sessions: sessions.map(s => s.id === convId ? { ...s, messages: msgs } : s) });
        persistSessions();
      }
      // 生成结束（末条不再是 streaming）→ 停止轮询
      if (!last || last.role !== "assistant" || last.status !== "streaming") {
        clearInterval(timer); _reattaching.delete(convId);
      }
    } catch { /* 单次拉取失败：下个 tick 再试 */ }
  }, 1500);
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
  const { dark, accent, setOpen, adminOpen, upgradeOpen, profileOpen, helpOpen, releaseNotesOpen, bugReportOpen, token, artifactPanel, rightPanelOpen, sbOpen } = useStore();
  const desktopView = useStore(s => s.desktopView);
  // Keep ChatArea mounted while a workspace/settings page is visible.  Long
  // tasks, streaming state and the unified inspector therefore continue to
  // live in the same Chat session instead of being torn down by navigation.
  const chatVisible = !setOpen && !adminOpen && !desktopView;
  const backendOnline = useStore(s => s.backendOnline);
  useEffect(() => {
    if (!token) return;
    const flush = () => { flushWorkCommandOutbox().catch(() => {}); };
    window.addEventListener("online", flush);
    window.addEventListener("hmm-backend-online", flush);
    document.addEventListener("visibilitychange", flush);
    flush();
    return () => {
      window.removeEventListener("online", flush);
      window.removeEventListener("hmm-backend-online", flush);
      document.removeEventListener("visibilitychange", flush);
    };
  }, [token]);
  // V269: 后端从离线恢复 → 轻提示一次（重同步由 api 层已自动完成）
  useEffect(() => {
    const onBack = () => showToast("后端已恢复连接，历史与统计已自动同步", "success");
    window.addEventListener("hmm-backend-online", onBack);
    return () => window.removeEventListener("hmm-backend-online", onBack);
  }, []);
  const [serverStatus, setServerStatus] = useState<{ ready: boolean; status: string; detail: string }>({ ready: false, status: "connecting", detail: "" });
  // 右侧统一检查器可拖拽，但必须给 Chat 留出可阅读、可输入的最小宽度。
  const [panelWidth, setPanelWidth] = useState(() => {
    if (typeof window === "undefined") return 480;
    const saved = Number(localStorage.getItem("hmm_right_panel_width") || 480);
    return clampInspectorWidth(saved, window.innerWidth, useStore.getState().sbOpen);
  });
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = panelWidth;
    let currentW = startW;
    const onMove = (ev: MouseEvent) => {
      // 往左拖变宽（面板在右侧）
      const next = clampInspectorWidth(startW + (startX - ev.clientX), window.innerWidth, sbOpen);
      currentW = next;
      setPanelWidth(next);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      try { localStorage.setItem("hmm_right_panel_width", String(currentW)); } catch { /* */ }
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  useEffect(() => {
    const keepChatUsable = () => setPanelWidth(current => clampInspectorWidth(current, window.innerWidth, sbOpen));
    keepChatUsable();
    window.addEventListener("resize", keepChatUsable);
    return () => window.removeEventListener("resize", keepChatUsable);
  }, [sbOpen]);

  useEffect(() => {
    init();
    fetchStats().then(s => useStore.getState().set({ stats: s })).catch(() => {});
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      useStore.getState().set({ sbOpen: false });
    }

    // v12: Poll /api/health until server is ready
    // V269: 加入离线判定与退避——502/503（壳层代离线）或网络错连续 2 次 → 标记离线
    // （亮离线横幅，隐去"连接服务器…"跑马灯），轮询间隔 1.5s→6s→20s 退避，不再每
    // 1.5s 死磕；任何真实应答（含就绪中）都标在线。恢复后由 api._setBackendOnline
    // 统一触发侧栏/统计重同步。
    let cancelled = false;
    let fails = 0;
    async function pollHealth() {
      while (!cancelled) {
        try {
          const r = await fetch("/api/health");
          if (r.status === 502 || r.status === 503) throw new Error("offline");
          useStore.getState().set({ backendOnline: true });
          fails = 0;
          if (r.ok) {
            const data = await r.json();
            setServerStatus({ ready: !!data.ready, status: data.status || "unknown", detail: data.detail || "" });
            if (data.ready) return; // Done polling
          }
        } catch {
          fails += 1;
          setServerStatus({ ready: false, status: "connecting", detail: "" });
          if (fails >= 2) useStore.getState().set({ backendOnline: false });
        }
        await new Promise(resolve => setTimeout(resolve, fails >= 8 ? 20000 : fails >= 3 ? 6000 : 1500));
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
    // Device resume preserves the same Chat id. It is not a cross-Chat
    // continuation and therefore never creates/copies a conversation.
    const w = window as unknown as {
      hashmmDeviceResume?: { onOpen: (cb: (d: { conv_id?: string; title?: string; run_id?: string; checkpoint_id?: string }) => void) => void };
      hashmmHandoff?: { onOpen: (cb: (d: { conv_id?: string; title?: string; run_id?: string; checkpoint_id?: string }) => void) => void };
    };
    const bridge = w.hashmmDeviceResume || w.hashmmHandoff;
    if (!bridge?.onOpen) return;
    bridge.onOpen((data) => {
      const cid = data?.conv_id;
      if (cid) {
        const receipt = {
          conv_id: cid,
          title: String(data?.title || ""),
          run_id: String(data?.run_id || ""),
          checkpoint_id: String(data?.checkpoint_id || ""),
          received_at: Date.now(),
        };
        try { localStorage.setItem("hmm_last_device_resume", JSON.stringify(receipt)); } catch { /* memory navigation still works */ }
        window.dispatchEvent(new CustomEvent("hmm-device-resume-open", { detail: receipt }));
        useStore.getState().set({ sid: cid, desktopView: null });
      }
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

  // V203：桌面端启动即自动开启远程服务（授权码 + 局域网 + 投屏窗）。
  // 用户诉求：打开客户端就是可被远程的，不用再去「远程」页手动点开启。
  // 尊重用户选择：远程页有「随客户端自动开启」开关（hmm_remote_auto，默认开）。
  // 带 60s 看门狗：投屏窗崩溃/被误关后自动拉回。与登录无关（授权码模式无需账号）。
  useEffect(() => {
    const remote = getRemote();
    if (!remote) return;
    let cancelled = false;
    const ensure = async () => {
      try {
        if (localStorage.getItem("hmm_remote_auto") === "0") return;   // 用户显式关掉了自动
        const s = await remote.status();
        if (cancelled || (s && s.running)) return;
        await remote.start({});
      } catch { /* 远程自启失败绝不影响主流程 */ }
    };
    ensure();
    const timer = setInterval(ensure, 60 * 1000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  // 桌面端 + 已登录：打开客户端即自动常驻「账号直连」被控（类 UU 远程的常驻被控）。  // V105 让手机端「我的设备」里【始终】看到这台电脑在线、可一键远程，且不会过一会就掉线。
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
        if (cancelled || (s && s.registered)) return;
        await remote.startAccountHost?.(useStore.getState().token || token);
      } catch { /* 远程被控自启失败不影响其它功能 */ }
    };
    const keepAlive = async () => {
      try {
        // V298 修复"刚登录又叫我登录"：以前这里直连 refreshSupabaseToken(rt) 续期，与 _fetch 里
        // 的单飞刷新（ensureFreshToken → _doRefresh）各刷各的；Supabase 刷新令牌一次性轮换，两处
        // 撞同一个 rt 必有一个用到已消费的 rt → 401 → 全局登出。现在统一走同一单飞：<10 分钟到期
        // 才刷、且全应用只有一个在飞刷新，轮换令牌不再被并发消费。
        await ensureFreshToken(600);
        if (cancelled) return;
        const now = useStore.getState().token;
        if (now) remote.updateAccountToken?.(now);   // 把当前（已续期）令牌热推给被控窗
        await arm();                                  // 被控窗不在了就重新拉起
      } catch { /* 静默 */ }
    };

    arm();          // 立即确保已托管
    keepAlive();    // 立即喂一次最新令牌
    const hostWatchdog = setInterval(arm, 15 * 1000);       // V1800: 注册/链路失败 15s 内恢复，不再等 4 分钟
    const timer = setInterval(keepAlive, 4 * 60 * 1000);  // 之后每 4 分钟保活（远低于 token 1h 寿命）
    return () => { cancelled = true; clearInterval(hostWatchdog); clearInterval(timer); };
  }, [token]);

  // V50: 右栏与会话关联——切换/新建会话时，面板里挂的若不是当前会话的文件则关闭。
  // 放在这里（而非散落在 Sidebar/新建按钮）是因为 sid 是所有切换路径的汇聚点。
  useEffect(() => {
    closeArtifactIfNotConv(sid);
    // Browser Use, Computer Use, rewind checkpoints and their approvals must
    // belong to the Chat that launched them. Every session-switch path funnels
    // through `sid`, so this also keeps background/long tasks recoverable.
    void getCheckpoint()?.setTask(sid || "session").catch(() => { /* web build or old desktop */ });
  }, [sid]);
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
        thinking: undefined,
        steps: restoredToolCalls(m as unknown as Record<string, unknown>) as Message["steps"],
        files: m.files || undefined,
        sources: m.sources || undefined,
        groundings: m.groundings || undefined,
        run_manifest: m.run_manifest || undefined,
        timeline: restoredTimeline(m as unknown as Record<string, unknown>),
        todo: restoredTodo(m as unknown as Record<string, unknown>),
        orchestration: restoredOrchestration(m as unknown as Record<string, unknown>),
        elapsed_ms: m.elapsed_ms || undefined,
        suggestions: m.suggestions || undefined,
        status: (m as any).status || undefined,   // V295: 携带 status，供整页刷新后识别 streaming 消息并重连
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
          updated_at: (conv?.updated_at || conv?.created_at || 0) * 1000 || Date.now(),
          project_id: typeof conv?.project_id === "string" ? conv.project_id : undefined,
          pinned: conv?.pinned === 1,
          archived: conv?.archived === 1,
        });
      }
      persistSessions();   // V269: 拉到的消息落盘本地缓存——看过一次，后端离线也能看
      // V295 整页刷新兜底：若末条是「仍在生成」的助手消息（后端每 ~1.5s 落库 partial），
      // 说明这条会话在别处/刷新前正后台生成——启动轻量轮询把 partial 持续拉回来更新气泡，直到
      // status 变 complete/error。配合内存直播态（liveStreams，管同会话内切换），实现"关页重开也能
      // 看到之前的进度"。桌面端与 App 同一套逻辑。
      try {
        const last = msgs[msgs.length - 1] as { role?: string; status?: string } | undefined;
        if (last && last.role === "assistant" && last.status === "streaming") {
          reattachStreaming(sid);
        }
      } catch { /* 重连非关键，失败不影响历史展示 */ }
    }).catch(async (err: unknown) => {
      if (cancelled) return;
      const status = (err as { status?: number })?.status;
      if (status === 404 || status === 403) {
        // 服务器**明确**说"没有/无权"——才移除并回首页。V269 关键修复：此前任何错误
        // （包括"后端未连接"）都走到这里，后端一离线，点一个历史删一个，这就是
        // "没启动后端历史记录就没了"的元凶。现在网络级失败绝不删本地缓存。
        useStore.getState().deleteSession(sid);
        try { history.pushState({}, "", "/"); } catch { /* no history API */ }
        showToast("对话不存在或无权访问，已从列表移除", "warning");
        return;
      }
      // 后端未连接：本地缓存已能展示则静默；没缓存再直读 Supabase 云端记录兜底；
      // 都没有仅提示"恢复连接后自动加载"，绝不删除。
      const sess = useStore.getState().sessions.find(s => s.id === sid);
      if (sess && sess.messages.length > 0) return;
      try {
        const tok = useStore.getState().token;
        if (tok) {
          const rows = await getCloudMessages(tok, sid);
          if (!cancelled && rows.length) {
            const cloudMsgs = rows.map(m => ({
              role: m.role as Message["role"],
              content: m.content,
              thinking: undefined,
              steps: (Array.isArray(m.tool_calls) && m.tool_calls.length ? m.tool_calls : undefined) as Message["steps"],
              files: (Array.isArray(m.files) && m.files.length ? m.files : undefined) as Message["files"],
              sources: (Array.isArray(m.sources) && m.sources.length ? m.sources : undefined) as Message["sources"],
              suggestions: (Array.isArray(m.suggestions) && m.suggestions.length ? m.suggestions : undefined) as Message["suggestions"],
              status: (m.status as Message["status"]) || undefined,
              ts: (m.created_at ? Date.parse(m.created_at) : 0) || Date.now(),
            }));
            const sessions = useStore.getState().sessions;
            if (sessions.find(s => s.id === sid)) {
              useStore.setState({ sessions: sessions.map(s => s.id === sid ? { ...s, messages: cloudMsgs } : s) });
            } else {
              useStore.getState().addSession({ id: sid, title: "对话", messages: cloudMsgs, created: Date.now(), pinned: false });
            }
            persistSessions();
            return;
          }
        }
      } catch { /* 云端不可用：走下方提示 */ }
      if (!cancelled) showToast("后端未连接：该对话暂无本地/云端缓存，恢复连接后自动加载", "warning");
    });
    return () => { cancelled = true; };
  }, [sid]);

  // 跨端联动：App 上发的消息，桌面这边也要自动出现（两端共用同一 Supabase）。
  // 桌面前端没装 supabase-js，先用可见性自适应的增量对账兜底：
  // 前台约 30 秒、后台约 5 分钟，并在窗口重新可见/网络恢复时立即对账。
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
              thinking: undefined,
              steps: restoredToolCalls(m as unknown as Record<string, unknown>) as Message["steps"],
              files: m.files || undefined,
              sources: m.sources || undefined,
              groundings: m.groundings || undefined,
              run_manifest: m.run_manifest || undefined,
              timeline: restoredTimeline(m as unknown as Record<string, unknown>),
              todo: restoredTodo(m as unknown as Record<string, unknown>),
              orchestration: restoredOrchestration(m as unknown as Record<string, unknown>),
              elapsed_ms: m.elapsed_ms || undefined,
              suggestions: m.suggestions || undefined,
              status: m.status || undefined,
              ts: m.created_at ? m.created_at * 1000 : (m.ts || Date.now()),
            }));
            const sessions = useStore.getState().sessions;
            const cur = sessions.find(s => s.id === curSid);
            // 大厂标准：不只看"数量变多"，也要认对端的【编辑/重生成】（数量没变但内容变了）。
            // 用内容签名（角色+长度+尾部）比对；但绝不在本地领先时回退、绝不打断流式。
            if (cur && useStore.getState().streamingConvId !== curSid) {
              const sigOf = (arr: Array<{ role?: string; content?: unknown }>) =>
                arr.map(m => {
                  const c = typeof m.content === "string" ? m.content : "";
                  return (m.role || "") + ":" + c.length + ":" + c.slice(-12);
                }).join("|");
              const serverHasMore = mapped.length > cur.messages.length;                 // 对端新增
              const sameLenButEdited = mapped.length === cur.messages.length &&
                mapped.length > 0 && sigOf(mapped) !== sigOf(cur.messages);               // 对端编辑/重生成
              // 只在"对端更全/被改过"时覆盖；服务端更少（本地领先、云端没跟上）则不动，避免丢消息
              if (serverHasMore || sameLenButEdited) {
                useStore.setState({
                  sessions: sessions.map(s => s.id === curSid ? { ...s, messages: mapped } : s),
                });
              }
            }
          }
        }
      } catch { /* 离线/瞬时失败：忽略，下轮再试 */ }
      finally { busy = false; }
    };
    let timer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;
    const schedule = () => {
      if (stopped) return;
      const base = document.visibilityState === "hidden" ? 5 * 60_000 : 30_000;
      const jitter = Math.floor(Math.random() * 5_000);
      timer = setTimeout(async () => { await tick(); schedule(); }, base + jitter);
    };
    const reconcileNow = () => { if (!busy) tick().catch(() => {}); };
    document.addEventListener("visibilitychange", reconcileNow);
    window.addEventListener("online", reconcileNow);
    schedule();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", reconcileNow);
      window.removeEventListener("online", reconcileNow);
    };
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
    // V203: 页头图标章等用的是 --accent-grad。此前 JS 只覆写 --accent，
    // --accent-grad 停留在 CSS 默认值 → 打包桌面端 11 个面板页头一直是另一种颜色。
    // 现在从主色派生浅→深渐变，主色改哪都全站跟随。
    const lighten = (v: number) => Math.min(255, Math.round(v + (255 - v) * 0.28));
    document.documentElement.style.setProperty(
      "--accent-grad",
      `linear-gradient(135deg, rgb(${lighten(r)},${lighten(g)},${lighten(b)}) 0%, ${accent} 100%)`,
    );
  }, [accent]);

  useEffect(() => {
    // V64: 桌面端菜单导航（终端/设置）→ 同一套 UI 内切换
    const d = getDesktop();
    if (!d || !d.onNav) return;
    const off = d.onNav((view) => {
      if (view === "terminal") useStore.getState().set({ desktopView: "terminal" } as any);
      else if (view === "settings" || view === "settings-about") useStore.getState().set({
        setOpen: true, adminOpen: false, desktopView: null,
        ...(view === "settings-about" ? { settingsTab: "about" } : {}),
      });
      else if (view === "files" || view === "usage") useStore.getState().set({ desktopView: view } as any);
    });
    return off;
  }, []);

  // V93: Marvis 式——未登录也进主工作台（游客可浏览），登录走居中遮罩弹窗

  return (
    <div className="flex flex-col h-screen overflow-hidden" style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
      {/* V65: 桌面端无边框窗口的自定义标题栏（web 打开为 null） */}
      <DesktopTitlebar />
      {/* V241: 铃铛移入 ChatArea 头部（内联），不再全局 fixed——修右栏打开时压住右栏头部按钮的重叠 */}
      <LoginModal />
      <WelcomeModal />
      <div className="flex flex-1 min-h-0 overflow-hidden">
      {!setOpen && !adminOpen && <Sidebar />}
      <div className="flex flex-col flex-1 min-w-0 h-full overflow-hidden">
        {/* V269 离线模式横幅：后端未连接时的全局告知条。核心承诺：登录态不动、
            本地缓存与云端记录照常可看；发消息/文件/知识库需后端在线。id 供
            Electron 心跳注入去重（主进程见到它就不再叠自己的黄条）。 */}
        {backendOnline === false && (
          <div id="hmm-offline-banner" className="flex-shrink-0 flex items-center gap-2.5 px-4 py-2"
               style={{ background: "#FEF3C7", borderBottom: "1px solid #FDE68A" }}>
            <WifiOff size={13} style={{ color: "#92400E", flexShrink: 0 }} />
            <span className="text-[12px] font-medium truncate" style={{ color: "#92400E" }}>
              后端未连接 · 离线模式——历史与云端记录可查看；发送消息、文件与知识库需后端在线
            </span>
            <button onClick={() => { probeBackend().then(ok => { if (!ok) showToast("后端仍未连接，请确认服务已启动", "warning"); }); }}
              className="ml-auto flex-shrink-0 px-2.5 py-1 rounded-lg text-[11px] font-semibold hover:opacity-85 transition-opacity"
              style={{ background: "#92400E", color: "#fff" }}>
              重试连接
            </button>
          </div>
        )}
        {/* v12: Server loading status banner（离线时让位给离线横幅，不再无限"连接服务器…"） */}
        {!serverStatus.ready && backendOnline !== false && (
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
        {setOpen ? <SettingsModal /> : adminOpen ? <AdminPanel /> : desktopView ? <DesktopPanel /> : null}
        <div className={`flex flex-1 min-w-0 h-full overflow-hidden ${chatVisible ? "" : "hidden"}`} aria-hidden={!chatVisible}>
          <div className={`${artifactPanel || rightPanelOpen ? "flex-1 min-w-0" : "flex-1"} h-full overflow-hidden`}>
            <ChatArea />
          </div>
          {(artifactPanel || rightPanelOpen) && (
            <>
              {/* V240 可拖拽分隔条：默认完全透明（分隔线由右栏 borderLeft 独担，与左栏对称、干净一条线），
                  仅悬停时浮现 accent 提示可拖——消除"两条线拼接"的不自然感 */}
              <div
                onMouseDown={startResize}
                className="hidden lg:block flex-shrink-0 cursor-col-resize group"
                style={{ width: "3px", position: "relative", zIndex: 5 }}
                title="拖动调整宽度"
              >
                {/* V242: 更细——默认透明，仅 hover 时中间浮现一条 2px accent 细线（对齐用户要的绿框细度） */}
                <div className="h-full w-[2px] mx-auto transition-colors group-hover:bg-[var(--accent)]"
                     style={{ background: "transparent" }} />
              </div>
              <div className="flex-shrink-0 hidden lg:flex h-full" style={{ width: `${panelWidth}px` }}>
                <WorkspaceInspector />
              </div>
            </>
          )}
        </div>
      </div>
      {upgradeOpen && <UpgradeModal />}
      {profileOpen && <ProfileModal />}
      {helpOpen && <HelpModal />}
      {releaseNotesOpen && <ReleaseNotesModal />}
      {bugReportOpen && <BugReportModal />}
      <DesktopPromptModal />
      <CloseConfirmModal />
      </div>
    </div>
  );
}

export default function App() {
  return <ErrorBoundary><ToastProvider><AppInner /></ToastProvider></ErrorBoundary>;
}
