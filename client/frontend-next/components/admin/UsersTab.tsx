"use client";
import { useState, useEffect, useCallback, useMemo } from "react";
import type { User } from "@/lib/types";
import * as api from "@/lib/api";
import { listAllProfiles } from "@/lib/supabase";
import { Plus, Trash2, Loader2, LogOut, Search } from "lucide-react";
import { Badge } from "./shared";
import { filterUsers, sortUsers, userRoleCounts, type RoleFilter, type UserSort } from "@/lib/userFilter";

// 每个用户行的附加信息（邮箱 / 是否仅存在于 Supabase / 最后登录）
interface UserMeta { email?: string; supabaseOnly?: boolean; lastSignIn?: string | null; }

export function UsersTab() {
  const [users, setUsers] = useState<User[]>([]);
  const [meta, setMeta] = useState<Record<string, UserMeta>>({});
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ username: "", password: "", display_name: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // V103.90 搜索 / 角色筛选 / 排序
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all");
  const [sortBy, setSortBy] = useState<UserSort>("role");
  const counts = useMemo(() => userRoleCounts(users), [users]);
  const shown = useMemo(() => sortUsers(filterUsers(users, { query, role: roleFilter }), sortBy), [users, query, roleFilter, sortBy]);

  // 合并「后端本地用户」与「Supabase 全部用户」：Supabase 是统一身份源，全部列出；
  // 后端里也存在的用户保留增删改/下线操作，仅存在 Supabase 的标注为「Supabase 账号」。
  const load = useCallback(async () => {
    let backend: User[] = [];
    try { backend = await api.listUsers(); } catch (_e) { /* 后端不可达 */ }
    let sbRows: Awaited<ReturnType<typeof listAllProfiles>> = null;
    try { const tok = typeof window !== "undefined" ? localStorage.getItem("hmm_token") : null; if (tok) sbRows = await listAllProfiles(tok); } catch (_e) { /* 非管理员/未部署 RPC */ }

    if (!sbRows) { setUsers(backend); setMeta({}); return; }

    const matchBackend = (email: string, username: string): User | undefined =>
      backend.find(b => b.username === email || b.username === username || b.username === (email.split("@")[0] || ""));

    const merged: User[] = [];
    const m: Record<string, UserMeta> = {};
    for (const r of sbRows) {
      const email = r.email || "";
      const uname = r.username || (email.split("@")[0] || email);
      const be = matchBackend(email, uname);
      const id = be?.id || `sb:${r.id}`;
      merged.push({
        id,
        username: uname,
        display_name: r.display_name || uname,
        role: be?.role || (r.is_admin ? "admin" : "user"),
        created_at: r.created_at ? Math.floor(new Date(r.created_at).getTime() / 1000) : undefined,
      });
      m[id] = { email, supabaseOnly: !be, lastSignIn: r.last_sign_in_at };
    }
    // 后端里有、但 Supabase 没有的（少见：纯后端账号）也补进来
    for (const b of backend) {
      if (!merged.some(u => u.id === b.id)) { merged.push(b); m[b.id] = { supabaseOnly: false }; }
    }
    setUsers(merged); setMeta(m);
  }, []);
  useEffect(() => { load(); }, [load]);

  async function addUser() {
    if (!form.username || !form.password) { setError("用户名和密码必填"); return; }
    setBusy(true); setError("");
    try {
      await api.createUser(form.username, form.password, form.display_name);
      setForm({ username: "", password: "", display_name: "" }); setShowAdd(false); load();
    } catch (e: unknown) { setError((e as Error).message || "创建失败"); }
    setBusy(false);
  }

  async function changeRole(id: string, role: string) {
    try { await api.updateUser(id, { role } as Partial<User>); load(); } catch (_e) { /* empty */ }
  }

  async function delUser(id: string, name: string) {
    if (!confirm(`确定删除用户 "${name}"？`)) return;
    try { await api.deleteUser(id); load(); } catch (e: unknown) { alert((e as Error).message); }
  }

  async function forceLogout(id: string, name: string) {
    if (!confirm(`强制下线用户 "${name}"？其所有设备的会话将立即失效（需重新登录）。`)) return;
    try { await api.forceLogoutUser(id); alert(`已强制下线 "${name}"`); } catch (e: unknown) { alert((e as Error).message); }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>用户管理</h4>
        <button onClick={() => setShowAdd(!showAdd)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white"
          style={{ background: "var(--accent)" }}>
          <Plus size={14} /> 添加用户
        </button>
      </div>

      {showAdd && (
        <div className="mb-4 p-4 rounded-xl space-y-2.5 anim-fade-up" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <input placeholder="用户名 *" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })}
            className="w-full h-9 px-3 rounded-lg text-xs outline-none"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <input placeholder="密码 *" type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })}
            className="w-full h-9 px-3 rounded-lg text-xs outline-none"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <input placeholder="显示名（可选）" value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })}
            className="w-full h-9 px-3 rounded-lg text-xs outline-none"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          {error && <div className="text-xs text-red-500 px-1">{error}</div>}
          <div className="flex gap-2">
            <button onClick={addUser} disabled={busy} className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium text-white disabled:opacity-50" style={{ background: "var(--accent)" }}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} 创建
            </button>
            <button onClick={() => setShowAdd(false)} className="px-4 py-1.5 rounded-lg text-xs font-medium" style={{ color: "var(--text-secondary)", background: "var(--bg-tertiary)" }}>取消</button>
          </div>
        </div>
      )}

      {/* V103.90 角色统计 */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <button onClick={() => setRoleFilter("all")} className="px-3 py-1.5 rounded-lg text-[12px] transition-colors" style={{ background: roleFilter === "all" ? "var(--accent-light)" : "var(--bg-secondary)", color: roleFilter === "all" ? "var(--accent)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>全部 <span className="font-mono">{counts.total}</span></button>
        <button onClick={() => setRoleFilter("admin")} className="px-3 py-1.5 rounded-lg text-[12px] transition-colors" style={{ background: roleFilter === "admin" ? "var(--accent-light)" : "var(--bg-secondary)", color: roleFilter === "admin" ? "var(--accent)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>管理员 <span className="font-mono">{counts.admin}</span></button>
        <button onClick={() => setRoleFilter("user")} className="px-3 py-1.5 rounded-lg text-[12px] transition-colors" style={{ background: roleFilter === "user" ? "var(--accent-light)" : "var(--bg-secondary)", color: roleFilter === "user" ? "var(--accent)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>用户 <span className="font-mono">{counts.user}</span></button>
        <button onClick={() => setRoleFilter("viewer")} className="px-3 py-1.5 rounded-lg text-[12px] transition-colors" style={{ background: roleFilter === "viewer" ? "var(--accent-light)" : "var(--bg-secondary)", color: roleFilter === "viewer" ? "var(--accent)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>只读 <span className="font-mono">{counts.viewer}</span></button>
        <div className="relative flex-1 min-w-[140px]">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索用户名 / 显示名…"
            className="w-full text-[12px] pl-8 pr-3 py-1.5 rounded-lg outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        </div>
        <select value={sortBy} onChange={e => setSortBy(e.target.value as UserSort)}
          className="h-8 px-2 rounded-lg text-[12px] outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <option value="role">按角色</option><option value="name">按名字</option><option value="created">按时间</option>
        </select>
      </div>

      <div className="space-y-2">
        {shown.map(u => {
          const mi = meta[u.id] || {};
          const sbOnly = !!mi.supabaseOnly;
          const sub = mi.email || `@${u.username}`;
          const last = mi.lastSignIn ? `最近登录 ${new Date(mi.lastSignIn).toLocaleDateString("zh-CN")}` : "";
          return (
          <div key={u.id} className="flex items-center gap-3 p-4 rounded-xl" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="w-9 h-9 rounded-full flex items-center justify-center text-white text-xs font-semibold flex-shrink-0" style={{ background: "var(--accent)" }}>
              {(u.display_name || u.username || "?").slice(0, 1).toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{u.display_name || u.username}</span>
                <Badge color={u.role === "admin" ? "#ef4444" : u.role === "viewer" ? "#71717a" : "var(--accent)"}>{u.role}</Badge>
                {sbOnly && <Badge color="#6366f1">Supabase</Badge>}
              </div>
              <div className="text-[11px] truncate" style={{ color: "var(--text-tertiary)" }}>{sub}{u.created_at ? ` · 注册 ${new Date(u.created_at * 1000).toLocaleDateString("zh-CN")}` : ""}{last ? ` · ${last}` : ""}</div>
            </div>
            {sbOnly ? (
              <span className="text-[11px] px-2 py-1 rounded-lg" style={{ color: "var(--text-tertiary)", background: "var(--bg-tertiary)" }} title="该账号仅存在于 Supabase（未在后端建本地账号）。角色用 SQL 的 hashmm-set-admin 调整。">仅查看</span>
            ) : (
              <>
                <select value={u.role} onChange={e => changeRole(u.id, e.target.value)}
                  className="h-8 px-2 rounded-lg text-xs outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                  <option value="user">用户</option><option value="admin">管理员</option><option value="viewer">只读</option>
                </select>
                <button onClick={() => forceLogout(u.id, u.username)} title="强制下线（吊销该用户所有会话）"
                  className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
                  <LogOut size={15} />
                </button>
                <button onClick={() => delUser(u.id, u.username)} className="p-1.5 rounded-lg text-red-400 transition-colors hover:bg-red-50 dark:hover:bg-red-950/20">
                  <Trash2 size={15} />
                </button>
              </>
            )}
          </div>
          );
        })}
        {shown.length === 0 && <div className="text-center py-12 text-sm" style={{ color: "var(--text-tertiary)" }}>{users.length ? "没有符合条件的用户" : "暂无用户"}</div>}
      </div>
    </div>
  );
}
