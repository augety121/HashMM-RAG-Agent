"use client";
import { useState, useEffect, useMemo } from "react";
import HashMascot from "./HashMascot";
import { useStore, syncConversations, persistSessions, isSidebarVisibleSession } from "@/lib/store";
import { useT } from "@/lib/useT";
import { createWorkProject, deleteSession as apiDeleteSession, searchUserWork, updateConversation, migrateConversations, getRunners, type RunnerStatus, archiveConversation, type UserWorkSearchItem, listWorkProjects, type WorkProject } from "@/lib/api";
import { PanelLeftClose, PanelLeftOpen, Plus, Trash2, MessageSquare, Star, Search, ListTodo, Pencil, Archive, BookOpen, FolderKanban, MonitorSmartphone, Blocks, ChevronRight, CalendarClock, Bot } from "lucide-react";
import { getProjectDesktop, isDesktop } from "@/lib/desktop";
import { desktopParentView } from "@/lib/desktopNavigation";
import { UserMenu } from "./UserMenu";
import { canSeeProductSurface, type ProductAudience } from "@/lib/productSurface";
import {
  readAccountProjects,
  writeAccountProjects,
  readActiveProject,
  readProjectSources,
  writeActiveProject,
  writeProjectSources,
} from "@/lib/accountWorkspaceCache";
import { showToast } from "@/lib/toast";
// V295 会话项日期标签：几天内显示相对（今天/昨天/周几），超过 7 天显示完整年-月-日。
// 直接回应诉求："几天内显示星期几，超过 7 天显示完整日期(年月日)"。桌面端与 App 同一套。
function fmtItemDate(created: number): string {
  if (!created || !isFinite(created)) return "";
  const d = new Date(created);
  const now = new Date();
  const DAY = 86400000;
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const diffDays = Math.floor((startOfToday - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / DAY);
  if (diffDays <= 0) return "今天";
  if (diffDays === 1) return "昨天";
  if (diffDays < 7) return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][d.getDay()];
  // 超过 7 天：完整年月日（同年省略年，跨年带年——更省空间且不歧义）
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return d.getFullYear() === now.getFullYear() ? `${mm}-${dd}` : `${d.getFullYear()}-${mm}-${dd}`;
}

function sessionActivity(session: { created: number; content_activity_at?: number }): number {
  return session.content_activity_at || session.created;
}

function groupByDate<T extends { id: string; title: string; created: number; content_activity_at?: number; pinned?: boolean }>(sessions: T[]) {
  const now = Date.now(); const DAY = 86400000;
  const groups: { label: string; items: T[] }[] = [];
  const pinned = sessions.filter(s => s.pinned);
  const unpinned = sessions.filter(s => !s.pinned);
  if (pinned.length) groups.push({ label: "已固定", items: pinned });
  const b: Record<string, T[]> = { "今天": [], "昨天": [], "近7天": [], "近30天": [], "更早": [] };
  for (const s of unpinned) {
    const d = now - sessionActivity(s);
    if (d < DAY) b["今天"].push(s); else if (d < 2*DAY) b["昨天"].push(s);
    else if (d < 7*DAY) b["近7天"].push(s); else if (d < 30*DAY) b["近30天"].push(s);
    else b["更早"].push(s);
  }
  for (const [label, items] of Object.entries(b)) { if (items.length) groups.push({ label, items }); }
  return groups;
}

function NavItem({ active, icon: Icon, label, desc, onClick, dot }: { active: boolean; icon: React.ComponentType<{ size?: number; className?: string }>; label: string; desc: string; onClick: () => void; dot?: "online" | "offline" }) {
  const t = useT();
  return (
    <button onClick={onClick} aria-label={t(label)} title={t(desc)} aria-current={active ? "page" : undefined}
      className={`relative w-full flex items-center gap-2.5 pl-3 pr-2.5 py-[7px] rounded-lg text-[12.5px] transition-colors duration-100 text-left ${active ? "" : "hover:bg-[var(--bg-tertiary)]"}`}
      style={active ? { background: "var(--accent-light)" } : undefined}>
      {active && <span className="absolute left-[3px] top-1/2 -translate-y-1/2 w-[3px] h-[15px] rounded-full" style={{ background: "var(--accent)" }} />}
      <span style={{ color: active ? "var(--accent)" : "var(--text-tertiary)", display: "inline-flex" }}><Icon size={15} className="flex-shrink-0" /></span>
      <span className="flex-1 truncate" style={{ color: active ? "var(--accent)" : "var(--text-secondary)", fontWeight: active ? 600 : 400 }}>{t(label)}</span>
      {dot && <span className="w-[6px] h-[6px] rounded-full flex-shrink-0" title={dot === "online" ? "runner 在线" : "runner 掉线"}
        style={{ background: dot === "online" ? "var(--success, #22a06b)" : "var(--warning, #e5a50a)" }} />}
    </button>
  );
}

/** V210 runner 心跳全局角标：轻量端点轮询。online=在线点；offline=见过但心跳超时（掉线要一眼看到）；none=没见过不打扰。
 *  V218: 定时器改 setTimeout 链 + 失败指数退避（15s→…→30min 封顶，成功即复位）——
 *  后端旧代码缺 /api/dispatch 时不再每 15s 刷一条 404。 */
function useRunnerDot(): "online" | "offline" | undefined {
  const [dot, setDot] = useState<"online" | "offline" | undefined>(undefined);
  const authToken = useStore(s => s.token);
  useEffect(() => {
    if (!authToken) {
      setDot(undefined);
      return;
    }
    let stop = false;
    let delay = 15000;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const r = await getRunners();
        if (stop) return;
        const list: RunnerStatus[] = r.runners || [];
        if (list.length === 0) setDot(undefined);
        else setDot(list.some(x => x.online) ? "online" : "offline");
        delay = 15000;   // 成功 → 复位常规节奏
      } catch {
        if (stop) return;
        setDot(undefined);
        delay = Math.min(delay * 2, 1800000);   // 失败（含 404 旧后端）→ 退避，封顶 30 分钟
      }
      if (!stop) timer = setTimeout(tick, delay);
    };
    tick();
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [authToken]);
  return dot;
}

type NavDef = { v: string; icon: React.ComponentType<{ size?: number; className?: string }>; label: string; desc: string; desktopOnly?: boolean; audience?: ProductAudience };

// V622：一级入口只保留用户需要“持续回来”的工作对象。画布、协作和个人
// 资料不是孤立应用：它们从 Chat 的工作方式按钮进入，结果回到原会话；
// 侧栏只承担用户会持续回访的工作对象。自动任务是 HashMM 的主动服务入口，
// 但执行结果仍回写原 Chat，不把任务变成另一套割裂的消息系统。
const PRIMARY_NAV: NavDef[] = [
  { v: "work-active", icon: ListTodo, label: "今天", desc: "继续工作并处理待确认事项" },
  { v: "scheduled", icon: CalendarClock, label: "自动任务", desc: "让 HashMM 按计划工作并把结果送回原对话" },
  { v: "hub-knowledge", icon: BookOpen, label: "资料库", desc: "管理工作使用的资料与记忆" },
  { v: "agents", icon: Bot, label: "智能体", desc: "从完整角色库选择专家，或组建可审查的协作团队" },
  { v: "plugins", icon: Blocks, label: "插件", desc: "把经过审查的外部能力用于 Chat" },
  { v: "remote", icon: MonitorSmartphone, label: "设备接力", desc: "在手机与电脑之间继续工作，或连接自己的其他设备", desktopOnly: true },
];

export function Sidebar() {
  const t = useT();
  const [desktopMode, setDesktopMode] = useState(false);
  useEffect(() => { setDesktopMode(isDesktop()); }, []);
  const { sessions, sid, sbOpen } = useStore();
  const streamingConvId = useStore(s => s.streamingConvId);
  // V295 后台生成中的会话集合：侧栏据此给这些会话点亮"生成中"脉冲点——多会话并发时一目了然。
  // V299 丝滑优化：只订阅"正在生成的会话 id 集合"的**稳定字符串签名**，而不是整个 liveStreams 映射。
  // 后台流每 1.5s 落一次 partial（映射对象每次都换新引用），若直接订阅映射，整条侧栏会跟着每次
  // partial 重渲染 → 滚动/点击发涩。订阅签名后，只有"哪些会话在生成"这个集合变化时才重渲染。
  const liveKey = useStore(s => Object.entries(s.liveStreams || {})
    .filter(([, ls]) => ls && (ls as { streaming?: boolean }).streaming)
    .map(([id]) => id).sort().join(","));
  const liveIds = useMemo(() => new Set(liveKey ? liveKey.split(",") : []), [liveKey]);
  const set = useStore(s => s.set);
  const user = useStore(st => st.user);
  const delSession = useStore(s => s.deleteSession);
  const togglePin = useStore(s => s.togglePin);
  const archiveSession = useStore(s => s.archiveSession);
  async function doArchive(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    const snapshot = useStore.getState().sessions;
    const previousSid = useStore.getState().sid;
    archiveSession(id, true);   // 本地乐观：立即从主列表移除
    try {
      await archiveConversation(id, true);   // 后端持久化（含云端同步）
    } catch {
      useStore.setState({ sessions: snapshot, sid: previousSid });
      persistSessions();
      showToast("归档失败，已恢复原状态", "error");
    }
  }
  const renameSession = useStore(s => s.renameSession);
  const desktopView = useStore(s => s.desktopView);
  const workDetailParent = useStore(s => s.workDetailParent);
  const runnerDot = useRunnerDot();   // V210 runner 心跳全局角标
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [deepResults, setDeepResults] = useState<UserWorkSearchItem[]>([]);
  const [deepSearching, setDeepSearching] = useState(false);
  const [projects, setProjects] = useState<WorkProject[]>(() => readAccountProjects(useStore.getState().user));
  const [activeProjectId, setActiveProjectId] = useState(() => readActiveProject(useStore.getState().user));
  const [projectComposerOpen, setProjectComposerOpen] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectGoal, setProjectGoal] = useState("");
  const [projectSources, setProjectSources] = useState<string[]>([]);
  const [projectCreating, setProjectCreating] = useState(false);
  const [projectCreateError, setProjectCreateError] = useState("");
  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({});
  // V103.90 会话重命名（hook 必须在任何早返回之前，否则收起侧栏时 hook 数量变化 → React #300）
  const [editId, setEditId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [recentLimit, setRecentLimit] = useState(80);

  useEffect(() => { setRecentLimit(80); }, [search, user?.id, user?.username]);

  useEffect(() => {
    setProjects(readAccountProjects(user));
    setActiveProjectId(readActiveProject(user));
    if (!user || !useStore.getState().token) return;
    let disposed = false;
    listWorkProjects().then(items => {
      if (disposed) return;
      setProjects(items);
      writeAccountProjects(user, items);
    }).catch(() => {
      // Offline startup intentionally keeps the last owner-bound project list.
    });
    const selected = (event: Event) => {
      const id = String((event as CustomEvent<{ projectId?: string }>).detail?.projectId || "");
      setActiveProjectId(id);
    };
    window.addEventListener("hmm-project-selected", selected);
    return () => {
      disposed = true;
      window.removeEventListener("hmm-project-selected", selected);
    };
  }, [user?.id, user?.username]);

  // V217: 收起 ≠ 消失。桌面宽度下收成 52px 图标栏（对标 Claude 桌面端）：常用入口一图标直达、
  // 悬停有名称、当前视图高亮；手机宽度(<768)图标栏隐藏，沿用 ChatArea 头部的展开按钮。
  if (!sbOpen) {
    return <RailSidebar desktopMode={desktopMode} runnerDot={runnerDot}
      userInitial={(user?.display_name || user?.username || "客").slice(0, 1)} />;
  }

  const sorted = [...sessions].filter(s => !s.archived && isSidebarVisibleSession(s))
    .sort((a, b) => sessionActivity(b) - sessionActivity(a));   // V1100: content clock only
  const filtered = search
    ? sorted.filter(s => (s.title || "").toLowerCase().includes(search.toLowerCase()))
    : sorted;
  // A durable chat has one navigation home. Project chats stay only beneath
  // their project; the global Recent timeline contains unassigned chats.
  const unassignedRecent = filtered.filter(item => !String(item.project_id || "").trim());
  const recentItems = unassignedRecent.slice(0, recentLimit);
  const groups = groupByDate(recentItems);

  async function handleDelete(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    const snapshot = useStore.getState().sessions;
    const previousSid = useStore.getState().sid;
    const previousTabs = useStore.getState().openTabs;
    delSession(id);
    try {
      await apiDeleteSession(id);
    } catch {
      useStore.setState({ sessions: snapshot, sid: previousSid, openTabs: previousTabs });
      persistSessions();
      showToast("删除失败，已恢复原对话", "error");
    }
  }
  // V103.90 会话重命名（状态已在早返回之前声明）
  function startRename(e: React.MouseEvent, id: string, title: string) { e.stopPropagation(); setEditId(id); setEditText(title); }
  function saveRename() {
    if (!editId) return;
    const id = editId, title = editText.trim();
    setEditId(null);
    if (title) { renameSession(id, title); updateConversation(id, { title }).catch(() => {}); }
  }

  function openProjectComposer() {
    setProjectName("");
    setProjectGoal("");
    setProjectSources([]);
    setProjectCreateError("");
    setProjectComposerOpen(true);
  }

  async function chooseProjectSources() {
    const bridge = getProjectDesktop();
    if (!bridge) {
      setProjectCreateError("当前环境不能选择本机文件夹。你仍可先创建项目，并在 Chat 中使用资料库。");
      return;
    }
    const picked = await bridge.pickSourceFolders();
    if (picked.ok && !picked.canceled) {
      setProjectSources(Array.from(new Set(picked.paths || [])).slice(0, 8));
      setProjectCreateError("");
    } else if (!picked.canceled) {
      setProjectCreateError(picked.error || "没有选中可用文件夹。");
    }
  }

  async function createProjectFromSidebar() {
    const name = projectName.trim();
    if (!name) {
      setProjectCreateError("请输入项目名称。");
      return;
    }
    setProjectCreating(true);
    setProjectCreateError("");
    try {
      const result = await createWorkProject({
        name,
        goal: projectGoal.trim(),
        description: projectGoal.trim(),
        permission_mode: "ask",
      });
      const next = [result.project, ...projects.filter(item => item.id !== result.project.id)];
      setProjects(next);
      writeAccountProjects(user, next);
      writeActiveProject(user, result.project.id);
      writeProjectSources(user, result.project.id, projectSources);
      setActiveProjectId(result.project.id);
      if (projectSources[0]) void getProjectDesktop()?.activateSource(projectSources[0]);
      setProjectComposerOpen(false);
      useStore.getState().newChat();
      set({ desktopView: null, adminOpen: false, setOpen: false });
      try { history.pushState({}, "", "/"); } catch { /* no-op */ }
    } catch (error) {
      setProjectCreateError(error instanceof Error ? error.message : "项目创建失败，请检查登录和服务连接。");
    } finally {
      setProjectCreating(false);
    }
  }

  function startProjectChat(project: WorkProject) {
    writeActiveProject(user, project.id);
    setActiveProjectId(project.id);
    const source = readProjectSources(user, project.id)[0];
    if (source) void getProjectDesktop()?.activateSource(source);
    useStore.getState().newChat();
    set({ desktopView: null, adminOpen: false, setOpen: false });
    try { history.pushState({}, "", "/"); } catch { /* no-op */ }
  }

  return (
    <aside className="w-[260px] flex-shrink-0 flex flex-col h-full min-h-0 anim-slide-in sidebar-container" style={{ background: "var(--bg-secondary)", borderRight: "1px solid var(--border)" }}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3">
        <div className="flex items-center gap-2">
          <HashMascot size={32} />
          <span className="font-semibold text-[13px]" style={{ color: "var(--text-primary)" }}>HashMM</span>
        </div>
        <button onClick={() => { set({ sbOpen: false }); try { localStorage.setItem("hmm_sb", "0"); } catch { /* */ } }} aria-label="收起侧栏" className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"><PanelLeftClose size={16} style={{ color: "var(--text-tertiary)" }} /></button>
      </div>

      {/* V253：New Chat + 导航 + 会话列表 共用一个滚动区——此前导航块在滚动区外，
          分区全展开时内容撑破侧栏，底部用户栏被顶出可视区。 */}
      <div className="flex-1 min-h-0 overflow-y-auto">
      {/* New Chat + Search shortcut */}
      <div className="px-3 pb-2">
        <button onClick={() => {
          writeActiveProject(user, "");
          setActiveProjectId("");
          useStore.getState().newChat();
          try { history.pushState({}, "", "/"); } catch (_e) {}
        }}
          className="w-full flex items-center gap-2 px-3 py-2.5 rounded-xl text-[13px] font-medium transition-colors duration-100 hover:bg-[var(--bg-tertiary)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <Plus size={15} /> {t("新对话")}
        </button>
        <div className="mt-3 flex flex-col gap-0.5">
          {PRIMARY_NAV
            .filter(it => desktopMode || !it.desktopOnly)
            .filter(it => canSeeProductSurface(it.audience || "user", user?.role === "admin" ? "admin" : "user"))
            .map(({ v, icon: Icon, label, desc }) => (
            <NavItem key={v} active={desktopView === v || (desktopView === "work-detail" ? workDetailParent === v : desktopParentView(desktopView) === v)} icon={Icon} label={label} desc={desc}
              dot={v === "remote" ? runnerDot : undefined}
              onClick={() => set({ desktopView: v as never, adminOpen: false, setOpen: false })} />
          ))}
        </div>
      </div>

      {/* v12 + V97: 对话搜索（标题即时过滤 + 回车深搜历史内容） */}
      {(
        <div className="px-3 pb-2">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
            <input value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={async (e) => {
                if (e.key === "Enter" && search.trim()) {
                  setDeepSearching(true);
                  try { const r = await searchUserWork(search.trim()); setDeepResults(r.items || []); }
                  catch { setDeepResults([]); }
                  setDeepSearching(false);
                } else if (e.key === "Escape") { setSearch(""); setDeepResults([]); }
              }}
              placeholder="搜索对话、项目和工作..."
              className="w-full pl-8 pr-3 py-1.5 rounded-lg text-[12px] outline-none transition-colors"
              style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          </div>
          {/* 深搜结果：命中历史消息片段，点击跳到对应会话 */}
          {(deepSearching || deepResults.length > 0) && (
            <div className="mt-2 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
              <div className="px-2.5 py-1.5 text-[10px] flex items-center justify-between" style={{ color: "var(--text-tertiary)", borderBottom: "1px solid var(--border)" }}>
                <span>{deepSearching ? "正在搜索…" : `找到 ${deepResults.length} 项`}</span>
                {deepResults.length > 0 && <button onClick={() => { setDeepResults([]); setSearch(""); }} className="hover:opacity-70">清除</button>}
              </div>
              <div className="max-h-[240px] overflow-y-auto">
                {deepResults.map((r, i) => (
                  <button key={`${r.kind}:${r.id}:${i}`}
                    onClick={() => {
                      if (r.kind === "conversation") {
                        const matched = sessions.find(item => item.id === r.id);
                        const projectId = matched?.project_id || "";
                        writeActiveProject(user, projectId);
                        setActiveProjectId(projectId);
                        set({ sid: r.id, desktopView: null });
                        try { history.pushState({}, "", `/chat/${r.id}`); } catch (_e) {}
                      } else if (r.kind === "project") {
                        writeActiveProject(user, r.id);
                        setActiveProjectId(r.id);
                        const source = readProjectSources(user, r.id)[0];
                        if (source) void getProjectDesktop()?.activateSource(source);
                        const latest = sessions
                          .filter(item => item.project_id === r.id && !item.archived)
                          .sort((a, b) => sessionActivity(b) - sessionActivity(a))[0];
                        if (latest) {
                          set({ sid: latest.id, desktopView: null, adminOpen: false, setOpen: false });
                          try { history.pushState({}, "", `/chat/${latest.id}`); } catch { /* no-op */ }
                        } else {
                          useStore.getState().newChat();
                        }
                      } else {
                        set({ desktopView: "work-detail", workDetailId: r.id, workDetailParent: "work-active" });
                      }
                      setDeepResults([]);
                    }}
                    className="w-full text-left px-2.5 py-2 transition-colors hover:bg-[var(--bg-tertiary)]"
                    style={{ borderBottom: "1px solid var(--border)" }}>
                    <div className="flex items-center gap-2">
                      <div className="text-[11.5px] font-medium truncate flex-1" style={{ color: "var(--text-primary)" }}>{r.title}</div>
                      <span className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>
                        {r.kind === "project" ? "项目" : r.kind === "work" ? "工作" : "对话"}
                      </span>
                    </div>
                    <div className="text-[10.5px] mt-0.5 line-clamp-2" style={{ color: "var(--text-tertiary)" }}>{r.snippet}</div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Codex-style durable work structure: projects own related chats, while
          Recent remains a quick resume list. Both read from the account-bound
          local cache first and reconcile with owner-checked server data. */}
      <div className="px-2 pb-1">
        <div className="sidebar-section-heading flex items-center justify-between px-2 py-1.5">
          <button
            onClick={openProjectComposer}
            className="text-[12px] font-semibold hover:opacity-75"
            style={{ color: "var(--text-tertiary)" }}
          >
            项目
          </button>
          <button
            onClick={openProjectComposer}
            className="rounded-md p-1 hover:bg-[var(--bg-tertiary)]"
            title="新建或管理项目"
            aria-label="新建或管理项目"
          >
            <Plus size={12} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>
        {projects.map(project => {
          const related = filtered
            .filter(item => item.project_id === project.id)
            .sort((a, b) => sessionActivity(b) - sessionActivity(a));
          const visibleRelated = expandedProjects[project.id] ? related : related.slice(0, 6);
          const active = activeProjectId === project.id;
          return (
            <div key={project.id} className="mb-0.5">
              <div
                className="group/project flex h-9 w-full items-center rounded-lg transition-colors duration-100 hover:bg-[var(--bg-tertiary)]"
                style={active ? { background: "var(--bg-tertiary)" } : undefined}
              >
                <button
                  onClick={() => {
                    writeActiveProject(user, project.id);
                    setActiveProjectId(project.id);
                    const source = readProjectSources(user, project.id)[0];
                    if (source) void getProjectDesktop()?.activateSource(source);
                    if (related[0]) {
                      set({ sid: related[0].id, desktopView: null, adminOpen: false, setOpen: false });
                      try { history.pushState({}, "", `/chat/${related[0].id}`); } catch { /* no-op */ }
                    } else {
                      startProjectChat(project);
                    }
                  }}
                  className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-1.5 text-left"
                  title={`打开项目 ${project.name}`}
                >
                  <FolderKanban size={13} style={{ color: active ? "var(--accent)" : "var(--text-tertiary)" }} />
                  <span className="min-w-0 flex-1 truncate text-[12px] font-medium" style={{ color: "var(--text-secondary)" }}>
                    {project.name}
                  </span>
                  <ChevronRight size={11} style={{ color: "var(--text-tertiary)" }} />
                </button>
                <button
                  onClick={event => {
                    event.stopPropagation();
                    startProjectChat(project);
                  }}
                  className="mr-1 rounded-md p-1 opacity-70 hover:bg-[var(--bg-secondary)] hover:opacity-100 focus:opacity-100"
                  title={`在 ${project.name} 中新建对话`}
                  aria-label={`在 ${project.name} 中新建对话`}
                >
                  <Plus size={12} style={{ color: "var(--text-tertiary)" }} />
                </button>
              </div>
              {visibleRelated.map(item => (
                <div
                  key={item.id}
                  onClick={() => {
                    if (editId === item.id) return;
                    writeActiveProject(user, project.id);
                    setActiveProjectId(project.id);
                    set({ sid: item.id, desktopView: null, adminOpen: false, setOpen: false });
                    try { history.pushState({}, "", `/chat/${item.id}`); } catch { /* no-op */ }
                  }}
                  onMouseEnter={() => setHoverId(item.id)}
                  onMouseLeave={() => setHoverId(null)}
                  className="group/chat ml-5 flex h-8 w-[calc(100%-1.25rem)] min-w-0 cursor-pointer items-center gap-2 rounded-md px-2 text-left transition-colors duration-100 hover:bg-[var(--bg-tertiary)]"
                  style={item.id === sid ? { background: "var(--accent-light)", color: "var(--accent)" } : undefined}
                >
                  {editId === item.id ? (
                    <input
                      autoFocus
                      value={editText}
                      onClick={event => event.stopPropagation()}
                      onChange={event => setEditText(event.target.value)}
                      onKeyDown={event => {
                        if (event.key === "Enter") { event.preventDefault(); saveRename(); }
                        if (event.key === "Escape") { event.preventDefault(); setEditId(null); }
                      }}
                      onBlur={saveRename}
                      className="min-w-0 flex-1 bg-transparent text-[11.5px] outline-none"
                      style={{ color: "var(--text-primary)", borderBottom: "1px solid var(--accent)" }}
                    />
                  ) : (
                    <span
                      className="min-w-0 flex-1 truncate text-[11.5px]"
                      onDoubleClick={event => startRename(event, item.id, item.title || "")}
                      style={{ color: item.id === sid ? "var(--accent)" : "var(--text-secondary)" }}
                    >
                      {item.title || "对话"}
                    </span>
                  )}
                  {editId !== item.id && (
                    <div className="flex w-[58px] flex-shrink-0 items-center justify-end gap-0.5 opacity-0 transition-opacity duration-100 group-hover/chat:opacity-100 group-focus-within/chat:opacity-100">
                      <button onClick={event => startRename(event, item.id, item.title || "")} className="rounded-md p-1 hover:bg-[var(--bg-secondary)]" title="重命名">
                        <Pencil size={11} style={{ color: "var(--text-tertiary)" }} />
                      </button>
                      <button onClick={event => doArchive(item.id, event)} className="rounded-md p-1 hover:bg-[var(--bg-secondary)]" title="归档">
                        <Archive size={11} style={{ color: "var(--text-tertiary)" }} />
                      </button>
                      <button onClick={event => handleDelete(item.id, event)} className="rounded-md p-1 hover:bg-red-100 dark:hover:bg-red-950/30" title="删除">
                        <Trash2 size={11} className="text-red-400" />
                      </button>
                    </div>
                  )}
                </div>
              ))}
              {related.length > 6 && (
                <button
                  type="button"
                  onClick={() => setExpandedProjects(current => ({
                    ...current,
                    [project.id]: !current[project.id],
                  }))}
                  className="ml-7 rounded-md px-2 py-1 text-[10.5px] hover:bg-[var(--bg-tertiary)]"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  {expandedProjects[project.id] ? "收起" : `查看全部（${related.length}）`}
                </button>
              )}
            </div>
          );
        })}
        {!projects.length && (
          <button
            onClick={openProjectComposer}
            className="mx-1 flex w-[calc(100%-0.5rem)] items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-[var(--bg-tertiary)]"
          >
            <FolderKanban size={13} style={{ color: "var(--text-tertiary)" }} />
            <span className="text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>创建第一个项目</span>
          </button>
        )}
      </div>

      {/* Conversation list */}
      <div className="px-2 py-1">
        <div className="sidebar-section-heading px-2 py-1.5 text-[12px] font-semibold" style={{ color: "var(--text-tertiary)" }}>最近</div>
        {groups.map(g => (
          <div key={g.label} className="mb-1.5">
            <div className="sidebar-date-heading px-2 py-1" style={{ color: "var(--text-tertiary)" }}>{g.label}</div>
            {g.items.map(s => (
              <div key={s.id} onClick={() => {
                if (editId === s.id) return;
                const projectId = String(s.project_id || "");
                writeActiveProject(user, projectId);
                setActiveProjectId(projectId);
                set({ sid: s.id, desktopView: null, adminOpen: false, setOpen: false });
                try { history.pushState({}, "", `/chat/${s.id}`); } catch (_e) {}
              }} onMouseEnter={() => setHoverId(s.id)} onMouseLeave={() => setHoverId(null)}
                className="relative mb-0.5 flex h-9 cursor-pointer items-center rounded-lg px-2.5 transition-colors duration-100 group"
                style={{ background: s.id === sid ? "var(--accent-light)" : (hoverId === s.id ? "var(--bg-tertiary)" : "transparent"), color: s.id === sid ? "var(--accent)" : "var(--text-secondary)" }}>
                {s.id === sid && <span className="absolute left-[2px] top-1/2 -translate-y-1/2 w-[3px] h-[16px] rounded-full" style={{ background: "var(--accent)" }} />}
                <MessageSquare size={13} className="mr-2 flex-shrink-0 opacity-40" />
                {editId === s.id ? (
                  <input autoFocus value={editText} onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setEditText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); saveRename(); } else if (e.key === "Escape") { e.preventDefault(); setEditId(null); } }}
                    onBlur={saveRename}
                    className="text-[12.5px] flex-1 min-w-0 bg-transparent outline-none border-b" style={{ color: "var(--text-primary)", borderColor: "var(--accent)" }} />
                ) : (
                  <span className="text-[12.5px] truncate flex-1" onDoubleClick={(e) => startRename(e, s.id, s.title || "")} title="双击重命名">{s.title || t("新对话")}</span>
                )}
                {(streamingConvId === s.id || liveIds.has(s.id)) && editId !== s.id && (
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0 mr-1 animate-pulse" style={{ background: "var(--accent)" }} title="正在生成…（后台运行，点进去可看实时进度）" />
                )}
                {editId !== s.id && (
                  <div className="relative h-7 w-[82px] flex-shrink-0">
                    <span className="absolute inset-0 flex items-center justify-end gap-1 transition-opacity duration-100 group-hover:opacity-0 group-focus-within:opacity-0">
                      <span className="text-[10.5px] tabular-nums" style={{ color: "var(--text-tertiary)", opacity: 0.75 }} title={new Date(sessionActivity(s)).toLocaleString()}>{fmtItemDate(sessionActivity(s))}</span>
                      {s.pinned && <Star size={10} className="pin-star fill-current flex-shrink-0" />}
                    </span>
                    <span className="pointer-events-none absolute inset-0 flex items-center justify-end gap-0.5 opacity-0 transition-opacity duration-100 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
                      <button onClick={(e) => startRename(e, s.id, s.title || "")} className="rounded-md p-1 transition-colors hover:bg-[var(--bg-secondary)]" title="重命名">
                        <Pencil size={12} style={{ color: "var(--text-tertiary)" }} />
                      </button>
                      <button onClick={(e) => { e.stopPropagation(); togglePin(s.id); }} className="rounded-md p-1 transition-colors hover:bg-[var(--bg-secondary)]" title={s.pinned ? "取消固定" : "固定"}>
                        <Star size={12} className={s.pinned ? "pin-star fill-current" : ""} style={{ color: s.pinned ? "#d97706" : "var(--text-tertiary)" }} />
                      </button>
                      <button onClick={(e) => doArchive(s.id, e)} className="rounded-md p-1 transition-colors hover:bg-[var(--bg-secondary)]" title="归档（移入归档区，可还原）">
                        <Archive size={12} style={{ color: "var(--text-tertiary)" }} />
                      </button>
                      <button onClick={(e) => handleDelete(s.id, e)} className="rounded-md p-1 transition-colors hover:bg-red-100 dark:hover:bg-red-950/30" title="删除">
                        <Trash2 size={12} className="text-red-400" />
                      </button>
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        ))}
        {unassignedRecent.length === 0 && (
          <div className="text-center py-8 text-[11px] flex flex-col items-center gap-2" style={{ color: "var(--text-tertiary)" }}>
            <span>{t("暂无普通对话")}</span>
            <MigrateOldChats />
          </div>
        )}
        {recentItems.length < unassignedRecent.length && (
          <button onClick={() => setRecentLimit(limit => limit + 80)}
            className="mx-2 my-2 w-[calc(100%-1rem)] rounded-lg py-2 text-[11px] hover:bg-[var(--bg-tertiary)]"
            style={{ color: "var(--text-tertiary)" }}>
            加载更早对话（剩余 {unassignedRecent.length - recentItems.length}）
          </button>
        )}
      </div>
      </div>

      {/* Footer（钉底，不随导航展开被顶走） */}
      <div className="p-2 flex-shrink-0" style={{ borderTop: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <UserMenu />
      </div>

      {projectComposerOpen && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center p-6" style={{ background: "rgba(15,23,42,.32)", backdropFilter: "blur(2px)" }}
          onMouseDown={event => { if (event.currentTarget === event.target && !projectCreating) setProjectComposerOpen(false); }}>
          <section className="w-full max-w-[500px] rounded-2xl p-5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
            role="dialog" aria-modal="true" aria-labelledby="project-composer-title">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 id="project-composer-title" className="text-[16px] font-semibold" style={{ color: "var(--text-primary)" }}>新建项目</h2>
                <p className="mt-1 text-[11px] leading-5" style={{ color: "var(--text-tertiary)" }}>项目把相关对话和本机资料保持在一起；创建后仍在 Chat 中工作。</p>
              </div>
              <button onClick={() => setProjectComposerOpen(false)} disabled={projectCreating} className="rounded-lg px-2 py-1 text-[16px] hover:bg-[var(--bg-tertiary)]" aria-label="关闭"
                style={{ color: "var(--text-tertiary)" }}>×</button>
            </div>
            <div className="mt-5 space-y-4">
              <label className="block">
                <span className="mb-1.5 block text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>项目名称</span>
                <input autoFocus value={projectName} onChange={event => setProjectName(event.target.value)} maxLength={120}
                  placeholder="例如：完成年度市场研究"
                  className="w-full rounded-xl px-3 py-2.5 text-[13px] outline-none focus:ring-2 focus:ring-[var(--accent-light)]"
                  style={{ color: "var(--text-primary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }} />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>希望完成什么</span>
                <textarea value={projectGoal} onChange={event => setProjectGoal(event.target.value)} maxLength={1200} rows={3}
                  placeholder="写清目标即可；交付物和验收标准可以在 Chat 中继续补充。"
                  className="w-full resize-none rounded-xl px-3 py-2.5 text-[13px] leading-5 outline-none focus:ring-2 focus:ring-[var(--accent-light)]"
                  style={{ color: "var(--text-primary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }} />
              </label>
              {desktopMode && (
                <div>
                  <span className="mb-1.5 block text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>本机资料文件夹（可选）</span>
                  <button onClick={() => void chooseProjectSources()} className="w-full rounded-xl px-3 py-3 text-left hover:bg-[var(--bg-tertiary)]"
                    style={{ border: "1px dashed var(--border)", color: "var(--text-secondary)" }}>
                    <span className="flex items-center gap-2 text-[12px] font-medium"><FolderKanban size={14} /> {projectSources.length ? `已选择 ${projectSources.length} 个文件夹` : "选择文件夹"}</span>
                    <span className="mt-1 block truncate text-[10px]" style={{ color: "var(--text-tertiary)" }}>{projectSources[0] || "路径只保存在当前账号的这台电脑，不上传服务器。"}</span>
                  </button>
                </div>
              )}
            </div>
            {projectCreateError && <div className="mt-3 rounded-lg px-3 py-2 text-[11px]" style={{ color: "#b42318", background: "rgba(180,35,24,.06)" }}>{projectCreateError}</div>}
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setProjectComposerOpen(false)} disabled={projectCreating} className="rounded-xl px-4 py-2 text-[12px] hover:bg-[var(--bg-tertiary)]"
                style={{ color: "var(--text-secondary)" }}>取消</button>
              <button onClick={() => void createProjectFromSidebar()} disabled={projectCreating || !projectName.trim()} className="rounded-xl px-4 py-2 text-[12px] font-medium text-white disabled:opacity-45"
                style={{ background: "var(--accent)" }}>{projectCreating ? "创建中…" : "创建并在 Chat 中继续"}</button>
            </div>
          </section>
        </div>
      )}

    </aside>
  );
}

/** V217: 收起态图标栏（52px）。设计对标 Claude 桌面端左栏收起形态：
 *  上：展开、新对话(圆形+)、对话记录；中：核心视图一图标直达（当前项 accent 高亮、
 *  runner 状态角标沿用展开态语义）；下：账号首字圆标（点击展开侧栏进完整菜单）。
 *  只在 md(≥768px) 显示——手机上 App 自动收起侧栏时保持全宽聊天，展开走 ChatArea 头部按钮。
 *  显式展开写 hmm_sb=1 持久化；点「对话记录/账号」也展开（列表/菜单需要宽度，诚实交互）。 */
function RailSidebar({ desktopMode, runnerDot, userInitial }: {
  desktopMode: boolean; runnerDot?: "online" | "offline"; userInitial: string;
}) {
  const user = useStore(state => state.user);
  const t = useT();
  const set = useStore(s => s.set);
  const desktopView = useStore(s => s.desktopView);
  const workDetailParent = useStore(s => s.workDetailParent);
  const role = useStore(s => s.user?.role === "admin" ? "admin" as const : "user" as const);
  const expand = () => { set({ sbOpen: true }); try { localStorage.setItem("hmm_sb", "1"); } catch { /* */ } };

  const items: Array<{ key: string; icon: React.ComponentType<{ size?: number; style?: React.CSSProperties }>; label: string; active: boolean; onClick: () => void; dot?: "online" | "offline" }> =
    PRIMARY_NAV
      .filter(it => desktopMode || !it.desktopOnly)
      .filter(it => canSeeProductSurface(it.audience || "user", role))
      .map(it => ({
      key: it.v,
      icon: it.icon as React.ComponentType<{ size?: number; style?: React.CSSProperties }>,
      label: it.label,
      active: desktopView === it.v || (desktopView === "work-detail" ? workDetailParent === it.v : desktopParentView(desktopView) === it.v),
      onClick: () => set({ desktopView: it.v as never, adminOpen: false, setOpen: false }),
      dot: it.v === "remote" ? runnerDot : undefined,
    }));

  return (
    <aside className="w-[52px] flex-shrink-0 hidden md:flex flex-col items-center py-2 gap-1 sidebar-container"
      style={{ background: "var(--bg-secondary)", borderRight: "1px solid var(--border)" }}>
      <button onClick={expand} aria-label={t("展开侧栏")} title={t("展开侧栏")}
        className="p-2 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]">
        <PanelLeftOpen size={16} style={{ color: "var(--text-tertiary)" }} />
      </button>
      <button onClick={() => {
        writeActiveProject(user, "");
        useStore.getState().newChat();
      }} aria-label={t("新对话")} title={t("新对话")}
        className="mt-1 w-[30px] h-[30px] rounded-full flex items-center justify-center transition-colors hover:bg-[var(--bg-tertiary)]"
        style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
        <Plus size={15} />
      </button>
      <button onClick={expand} aria-label={t("对话记录")} title={t("对话记录（展开侧栏）")}
        className="p-2 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]">
        <MessageSquare size={15} style={{ color: "var(--text-tertiary)" }} />
      </button>
      <div className="w-[24px] my-1" style={{ borderTop: "1px solid var(--border)" }} />
      {items.map(({ key, icon: Icon, label, active, onClick, dot }) => (
        <button key={key} onClick={onClick} aria-label={t(label)} title={t(label)} aria-current={active ? "page" : undefined}
          className="relative p-2 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"
          style={active ? { background: "var(--accent-light)" } : undefined}>
          <Icon size={15} style={{ color: active ? "var(--accent)" : "var(--text-tertiary)" }} />
          {dot && <span className="absolute top-[5px] right-[5px] w-[6px] h-[6px] rounded-full"
            style={{ background: dot === "online" ? "var(--success, #22a06b)" : "var(--warning, #e5a50a)" }} />}
        </button>
      ))}
      <div className="flex-1" />
      <button onClick={expand} aria-label={t("账号")} title={t("账号与设置（展开侧栏）")}
        className="mb-1 w-[28px] h-[28px] rounded-full flex items-center justify-center text-[11px] font-semibold transition-transform hover:scale-105"
        style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
        {userInitial}
      </button>
    </aside>
  );
}

/** 空列表时显示：把旧身份（匿名/本地账号）下的历史对话一键过户到当前 Supabase 账号。 */
function MigrateOldChats() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  async function run() {
    setBusy(true); setMsg("");
    try {
      const r = await migrateConversations("anonymous") as { moved?: number };
      const n = r?.moved ?? 0;
      if (n > 0) {
        const tok = typeof localStorage !== "undefined" ? localStorage.getItem("hmm_token") : null;
        if (tok) await syncConversations(tok);
        setMsg(`已导入 ${n} 条`);
      } else {
        setMsg("没有可导入的旧对话");
      }
    } catch {
      setMsg("导入失败，请稍后再试");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-col items-center gap-1.5">
      <button onClick={run} disabled={busy}
        className="px-3 py-1.5 rounded-lg text-[11px] transition-colors disabled:opacity-50"
        style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
        {busy ? "导入中…" : "导入旧对话到当前账号"}
      </button>
      {msg && <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{msg}</span>}
    </div>
  );
}
