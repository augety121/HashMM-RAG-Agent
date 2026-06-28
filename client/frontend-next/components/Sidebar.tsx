"use client";
import { useState, useEffect } from "react";
import HashMascot from "./HashMascot";
import { useStore, syncConversations } from "@/lib/store";
import { useT } from "@/lib/useT";
import { deleteSession as apiDeleteSession, searchSessions, updateConversation, migrateConversations } from "@/lib/api";
import { PanelLeftClose, Plus, Trash2, MessageSquare, Star, Search, Database, FileText, Terminal as TerminalIcon, BarChart3, Plug, LayoutPanelLeft, MonitorSmartphone, Brain, Sparkles, Activity, ShieldCheck, Clock, Network, ScrollText, ChevronRight, Pencil } from "lucide-react";
import { isDesktop } from "@/lib/desktop";
import { UserMenu } from "./UserMenu";
function groupByDate(sessions: { id: string; title: string; created: number; pinned?: boolean }[]) {
  const now = Date.now(); const DAY = 86400000;
  const groups: { label: string; items: typeof sessions }[] = [];
  const pinned = sessions.filter(s => s.pinned);
  const unpinned = sessions.filter(s => !s.pinned);
  if (pinned.length) groups.push({ label: "已固定", items: pinned });
  const b: Record<string, typeof sessions> = { "今天": [], "昨天": [], "近7天": [], "近30天": [], "更早": [] };
  for (const s of unpinned) {
    const d = now - s.created;
    if (d < DAY) b["今天"].push(s); else if (d < 2*DAY) b["昨天"].push(s);
    else if (d < 7*DAY) b["近7天"].push(s); else if (d < 30*DAY) b["近30天"].push(s);
    else b["更早"].push(s);
  }
  for (const [label, items] of Object.entries(b)) { if (items.length) groups.push({ label, items }); }
  return groups;
}

function NavItem({ active, icon: Icon, label, desc, onClick }: { active: boolean; icon: React.ComponentType<{ size?: number; className?: string }>; label: string; desc: string; onClick: () => void }) {
  const t = useT();
  return (
    <button onClick={onClick} aria-label={t(label)} title={t(desc)} aria-current={active ? "page" : undefined}
      className={`relative w-full flex items-center gap-2.5 pl-3 pr-2.5 py-[7px] rounded-lg text-[12.5px] transition-all text-left ${active ? "" : "hover:bg-[var(--bg-tertiary)]"}`}
      style={active ? { background: "var(--accent-light)" } : undefined}>
      {active && <span className="absolute left-[3px] top-1/2 -translate-y-1/2 w-[3px] h-[15px] rounded-full" style={{ background: "var(--accent)" }} />}
      <span style={{ color: active ? "var(--accent)" : "var(--text-tertiary)", display: "inline-flex" }}><Icon size={15} className="flex-shrink-0" /></span>
      <span className="flex-1 truncate" style={{ color: active ? "var(--accent)" : "var(--text-secondary)", fontWeight: active ? 600 : 400 }}>{t(label)}</span>
    </button>
  );
}

// V103.51: 可折叠分区标题。把「系统」这类长清单收进可展开的 disclosure，
// 默认折叠 → 聊天记录(产品重点)拿回竖直空间。对标 Linear / Claude Code 的侧栏分组。
// 折叠状态持久化到 localStorage，下次打开记得用户的选择。
function NavSection({ title, count, open, onToggle, children }:
  { title: string; count?: number; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  const t = useT();
  return (
    <div className="mt-1">
      <button onClick={onToggle} aria-expanded={open}
        className="w-full flex items-center gap-1 px-2 pt-2 pb-0.5 text-[10px] font-medium rounded-md transition-colors hover:bg-[var(--bg-tertiary)]"
        style={{ color: "var(--text-tertiary)" }}>
        <ChevronRight size={11} className="transition-transform flex-shrink-0"
          style={{ transform: open ? "rotate(90deg)" : "none" }} />
        <span className="uppercase tracking-wide">{t(title)}</span>
        {!open && typeof count === "number" && count > 0 && (
          <span className="ml-1 px-1 rounded-full text-[9px]" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>{count}</span>
        )}
      </button>
      {open && <div className="flex flex-col gap-0.5 mt-1 mb-1.5 p-1.5 rounded-xl" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>{children}</div>}
    </div>
  );
}

function loadNavOpen(key: string, fallback: boolean): boolean {
  try {
    if (typeof window === "undefined") return fallback;
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "1";
  } catch { return fallback; }
}
function saveNavOpen(key: string, val: boolean) {
  try { if (typeof window !== "undefined") localStorage.setItem(key, val ? "1" : "0"); } catch { /* ignore */ }
}

type NavDef = { v: string; icon: React.ComponentType<{ size?: number; className?: string }>; label: string; desc: string; desktopOnly?: boolean };
// 系统能力按主题分组（对标 Linear / Claude Code 的分区侧栏）：把原来一长条 11 项拆成
// 几个可扫读的小组，常用的「智能」默认展开，其余默认收起、命中时自动展开。
const SYS_GROUPS: { id: string; title: string; def: boolean; items: NavDef[] }[] = [
  { id: "intel", title: "智能", def: true, items: [
    { v: "memory", icon: Brain, label: "记忆中心", desc: "跨会话偏好" },
    { v: "evolution", icon: Sparkles, label: "自我进化", desc: "技能 · 经验" },
    { v: "discovery", icon: Search, label: "主动发现", desc: "自找活 · 提前想" },
    { v: "routing", icon: Network, label: "模型路由", desc: "角色 · 本地/云端" },
  ] },
  { id: "ops", title: "运营", def: false, items: [
    { v: "usage", icon: BarChart3, label: "用量", desc: "Token · 成本" },
    { v: "quality", icon: Activity, label: "质量看板", desc: "延迟 · 成本 · 质量" },
    { v: "runs", icon: ScrollText, label: "运行轨迹", desc: "遥测 · 回放 · 排障" },
    { v: "scheduled", icon: Clock, label: "定时任务", desc: "主动服务 · 计划" },
  ] },
  { id: "gov", title: "治理", def: false, items: [
    { v: "audit", icon: ShieldCheck, label: "权限审计", desc: "工具 · 治理 · 合规" },
  ] },
  { id: "device", title: "设备", def: false, items: [
    { v: "backend", icon: Plug, label: "后端连接", desc: "本地 / 远程", desktopOnly: true },
    { v: "remote", icon: MonitorSmartphone, label: "远程", desc: "远程桌面 · 配对码", desktopOnly: true },
  ] },
];

export function Sidebar() {
  const t = useT();
  const [desktopMode, setDesktopMode] = useState(false);
  useEffect(() => { setDesktopMode(isDesktop()); }, []);
  const { sessions, sid, sbOpen } = useStore();
  const streamingConvId = useStore(s => s.streamingConvId);
  const set = useStore(s => s.set);
  const user = useStore(st => st.user);
  const delSession = useStore(s => s.deleteSession);
  const togglePin = useStore(s => s.togglePin);
  const renameSession = useStore(s => s.renameSession);
  const desktopView = useStore(s => s.desktopView);
  const adminOpen = useStore(s => s.adminOpen);
  const adminTab = useStore(s => s.adminTab);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [deepResults, setDeepResults] = useState<Array<{ conv_id: string; title: string; snippet: string; role: string; created_at: number }>>([]);
  const [deepSearching, setDeepSearching] = useState(false);
  // V103.51: 折叠分区。知识默认展开(常用)、系统默认折叠(13 项太占地，把竖直空间还给聊天记录)。
  const [openKnowledge, setOpenKnowledge] = useState(true);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  useEffect(() => {
    setOpenKnowledge(loadNavOpen("hmm_nav_knowledge", true));
    const init: Record<string, boolean> = {};
    for (const g of SYS_GROUPS) init[g.id] = loadNavOpen("hmm_nav_g_" + g.id, g.def);
    setOpenGroups(init);
  }, []);
  const toggleKnowledge = () => setOpenKnowledge(v => { const n = !v; saveNavOpen("hmm_nav_knowledge", n); return n; });
  const toggleGroup = (id: string, def: boolean) => setOpenGroups(prev => {
    const cur = prev[id] ?? def; const n = !cur; saveNavOpen("hmm_nav_g_" + id, n);
    return { ...prev, [id]: n };
  });
  // V103.90 会话重命名（hook 必须在任何早返回之前，否则收起侧栏时 hook 数量变化 → React #300）
  const [editId, setEditId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  if (!sbOpen) return null;

  const sorted = [...sessions].sort((a, b) => b.created - a.created);
  const filtered = search
    ? sorted.filter(s => (s.title || "").toLowerCase().includes(search.toLowerCase()))
    : sorted;
  const groups = groupByDate(filtered);

  async function handleDelete(id: string, e: React.MouseEvent) { e.stopPropagation(); delSession(id); try { await apiDeleteSession(id); } catch (_e) {} }
  // V103.90 会话重命名（状态已在早返回之前声明）
  function startRename(e: React.MouseEvent, id: string, title: string) { e.stopPropagation(); setEditId(id); setEditText(title); }
  function saveRename() {
    if (!editId) return;
    const id = editId, title = editText.trim();
    setEditId(null);
    if (title) { renameSession(id, title); updateConversation(id, { title }).catch(() => {}); }
  }

  return (
    <aside className="w-[260px] flex-shrink-0 flex flex-col anim-slide-in sidebar-container" style={{ background: "var(--bg-secondary)", borderRight: "1px solid var(--border)" }}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3">
        <div className="flex items-center gap-2">
          <HashMascot size={28} />
          <span className="font-semibold text-[13px]" style={{ color: "var(--text-primary)" }}>HashMM</span>
        </div>
        <button onClick={() => set({ sbOpen: false })} aria-label="收起侧栏" className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]"><PanelLeftClose size={16} style={{ color: "var(--text-tertiary)" }} /></button>
      </div>

      {/* New Chat + Search shortcut */}
      <div className="px-3 pb-2">
        <button onClick={() => { useStore.getState().newChat(); try { history.pushState({}, "", "/"); } catch (_e) {} }}
          className="w-full flex items-center gap-2 px-3 py-2.5 rounded-xl text-[13px] font-medium transition-all hover:bg-[var(--bg-tertiary)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <Plus size={15} /> {t("新对话")}
        </button>
        {/* V64: 工作台/终端需本机文件与命令行访问，仅桌面渲染（浏览器做不了）。 */}
        {desktopMode && (
          <div className="mt-3 flex flex-col gap-0.5">
            {[
              { v: "workbench", icon: LayoutPanelLeft, label: "工作台", desc: "文件 · 预览 · 终端" },
              { v: "terminal", icon: TerminalIcon, label: "终端", desc: "本机命令行" },
            ].map(({ v, icon: Icon, label, desc }) => (
              <NavItem key={v} active={desktopView === v} icon={Icon} label={label} desc={desc} onClick={() => set({ desktopView: v as never })} />
            ))}
          </div>
        )}

        {/* V103.84: 知识 + 系统为后端能力，web 与桌面同布局都显示（DesktopPanel/AdminPanel
            同源 API，无桌面桥依赖）。仅「后端连接 / 远程」属本地基础设施，限桌面。 */}
        <div className="mt-1 flex flex-col gap-0.5">
          {/* 知识：可折叠，默认展开 */}
          {user?.role === "admin" && (
            <NavSection title="知识" count={2} open={openKnowledge} onToggle={toggleKnowledge}>
              {[
                { tab: "kbs", icon: Database, label: "知识库", desc: "语料 · 迁移" },
                { tab: "templates", icon: FileText, label: "技能与模板", desc: "提示词资产" },
              ].map(({ tab, icon: Icon, label, desc }) => (
                <NavItem key={tab} active={adminOpen && adminTab === tab} icon={Icon} label={label} desc={desc} onClick={() => set({ adminOpen: true, adminTab: tab as never })} />
              ))}
            </NavSection>
          )}

          {/* 系统能力按主题分组：智能 / 运营 / 治理 / 设备。非桌面隐藏「设备」类本地基础设施项；
              当前激活项所在组自动展开。每组折叠状态各自持久化。 */}
          {SYS_GROUPS.map(g => {
            const items = g.items.filter(it => desktopMode || !it.desktopOnly);
            if (items.length === 0) return null;
            const activeHere = items.some(it => it.v === desktopView);
            const open = (openGroups[g.id] ?? g.def) || activeHere;
            return (
              <NavSection key={g.id} title={g.title} count={items.length} open={open} onToggle={() => toggleGroup(g.id, g.def)}>
                {items.map(({ v, icon: Icon, label, desc }) => (
                  <NavItem key={v} active={desktopView === v} icon={Icon} label={label} desc={desc} onClick={() => set({ desktopView: v as never })} />
                ))}
              </NavSection>
            );
          })}
        </div>
      </div>

      {/* v12 + V97: 对话搜索（标题即时过滤 + 回车深搜历史内容） */}
      {(sessions.length > 5 || deepResults.length > 0 || search) && (
        <div className="px-3 pb-2">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
            <input value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={async (e) => {
                if (e.key === "Enter" && search.trim()) {
                  setDeepSearching(true);
                  try { const r = await searchSessions(search.trim()); setDeepResults(r.results || []); }
                  catch { setDeepResults([]); }
                  setDeepSearching(false);
                } else if (e.key === "Escape") { setSearch(""); setDeepResults([]); }
              }}
              placeholder="搜索对话（回车搜历史内容）..."
              className="w-full pl-8 pr-3 py-1.5 rounded-lg text-[12px] outline-none transition-colors"
              style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          </div>
          {/* 深搜结果：命中历史消息片段，点击跳到对应会话 */}
          {(deepSearching || deepResults.length > 0) && (
            <div className="mt-2 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
              <div className="px-2.5 py-1.5 text-[10px] flex items-center justify-between" style={{ color: "var(--text-tertiary)", borderBottom: "1px solid var(--border)" }}>
                <span>{deepSearching ? "搜索历史中…" : `历史命中 ${deepResults.length}`}</span>
                {deepResults.length > 0 && <button onClick={() => { setDeepResults([]); setSearch(""); }} className="hover:opacity-70">清除</button>}
              </div>
              <div className="max-h-[240px] overflow-y-auto">
                {deepResults.map((r, i) => (
                  <button key={r.conv_id + i}
                    onClick={() => { set({ sid: r.conv_id }); try { history.pushState({}, "", `/?c=${r.conv_id}`); } catch (_e) {} setDeepResults([]); }}
                    className="w-full text-left px-2.5 py-2 transition-colors hover:bg-[var(--bg-tertiary)]"
                    style={{ borderBottom: "1px solid var(--border)" }}>
                    <div className="text-[11.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{r.title}</div>
                    <div className="text-[10.5px] mt-0.5 line-clamp-2" style={{ color: "var(--text-tertiary)" }}>{r.snippet}</div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto px-2 py-1">
        {groups.map(g => (
          <div key={g.label} className="mb-1.5">
            <div className="px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>{g.label}</div>
            {g.items.map(s => (
              <div key={s.id} onClick={() => { if (editId === s.id) return; set({ sid: s.id }); try { history.pushState({}, "", `/chat/${s.id}`); } catch (_e) {} }} onMouseEnter={() => setHoverId(s.id)} onMouseLeave={() => setHoverId(null)}
                className="relative flex items-center px-2.5 py-2 rounded-lg cursor-pointer mb-0.5 transition-all group"
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
                {streamingConvId === s.id && editId !== s.id && (
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0 mr-1 animate-pulse" style={{ background: "var(--accent)" }} title="正在生成…（后台运行）" />
                )}
                {hoverId === s.id && editId !== s.id && (
                  <div className="flex items-center gap-0.5">
                    <button onClick={(e) => startRename(e, s.id, s.title || "")} className="p-1 rounded-md transition-colors hover:bg-[var(--bg-secondary)]" title="重命名">
                      <Pencil size={12} style={{ color: "var(--text-tertiary)" }} />
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); togglePin(s.id); }} className="p-1 rounded-md transition-colors hover:bg-[var(--bg-secondary)]" title={s.pinned ? "取消固定" : "固定"}>
                      <Star size={12} className={s.pinned ? "pin-star fill-current" : ""} style={{ color: s.pinned ? "#d97706" : "var(--text-tertiary)" }} />
                    </button>
                    <button onClick={(e) => handleDelete(s.id, e)} className="p-1 rounded-md transition-colors hover:bg-red-100 dark:hover:bg-red-950/30">
                      <Trash2 size={12} className="text-red-400" />
                    </button>
                  </div>
                )}
                {s.pinned && hoverId !== s.id && editId !== s.id && <Star size={10} className="pin-star fill-current flex-shrink-0" />}
              </div>
            ))}
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="text-center py-8 text-[11px] flex flex-col items-center gap-2" style={{ color: "var(--text-tertiary)" }}>
            <span>{t("暂无对话记录")}</span>
            <MigrateOldChats />
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="p-2" style={{ borderTop: "1px solid var(--border)" }}>
        <UserMenu />
      </div>
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
