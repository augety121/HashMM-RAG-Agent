"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, GitBranch, GitFork, Loader2, Lock, Pin, PinOff, RefreshCw, Trash2 } from "lucide-react";
import { getLocal, type ManagedWorktree, type RepoInspection, type WorktreeListResult } from "@/lib/desktop";

function shortPath(value: string) {
  const bits = String(value || "").split(/[\\/]/).filter(Boolean);
  return bits.slice(-3).join("/") || value;
}

function createdAt(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { hour12: false });
}

export function WorktreePanel({ cwd, repo, onActivated, keepLimit = 15, autoCleanup = false }: {
  cwd?: string;
  repo: RepoInspection | null;
  onActivated?: (dir: string) => void;
  keepLimit?: number;
  autoCleanup?: boolean;
}) {
  const [data, setData] = useState<WorktreeListResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [label, setLabel] = useState("");
  const [baseRef, setBaseRef] = useState("HEAD");
  const [includeLocal, setIncludeLocal] = useState(false);
  const [copyIgnored, setCopyIgnored] = useState(true);
  const [permanent, setPermanentCreate] = useState(false);
  const [branchTarget, setBranchTarget] = useState("");
  const [branchName, setBranchName] = useState("");

  useEffect(() => {
    const branch = String(repo?.branch || "");
    if (branch && branch !== "(detached HEAD)" && !branch.startsWith("(")) setBaseRef(branch);
    else setBaseRef("HEAD");
  }, [repo?.branch, repo?.root]);

  const load = useCallback(async () => {
    const local = getLocal();
    if (!repo?.is_git) { setData(null); return; }
    if (!local?.gitWorktreeList) { setError("Worktree 接口不可用，请安装 V337+ 桌面端"); return; }
    setLoading(true); setError("");
    try {
      const result = await local.gitWorktreeList(cwd || undefined, { keep_limit: keepLimit, auto_cleanup: autoCleanup });
      setData(result);
      if (!result.ok) setError(result.error || "读取 worktree 失败");
    } catch (e) { setError((e as Error)?.message || "读取 worktree 失败"); }
    finally { setLoading(false); }
  }, [cwd, repo?.is_git, keepLimit, autoCleanup]);

  useEffect(() => { load(); }, [load]);

  const managedCount = useMemo(() => (data?.items || []).filter(item => item.managed).length, [data]);

  async function create() {
    const local = getLocal();
    if (!local?.gitWorktreeCreate || busy) return;
    setBusy("create"); setError(""); setNotice("");
    try {
      const result = await local.gitWorktreeCreate(cwd || undefined, {
        base_ref: baseRef.trim() || "HEAD", label: label.trim(), include_local_changes: includeLocal,
        copy_ignored: copyIgnored, permanent,
        keep_limit: keepLimit, auto_cleanup: autoCleanup,
      });
      if (!result.ok) throw new Error(result.error || "创建 worktree 失败");
      setNotice(`已创建 detached worktree：${shortPath(result.path || "")}。${result.local_changes_applied ? "当前 tracked 改动已作为未暂存改动复制；" : ""}${result.copied_files?.length ? `按 .worktreeinclude 复制 ${result.copied_files.length} 个忽略文件。` : ""}`);
      setShowCreate(false); setLabel("");
      await load();
    } catch (e) { setError((e as Error)?.message || "创建 worktree 失败"); }
    finally { setBusy(""); }
  }

  async function activate(item: ManagedWorktree) {
    const local = getLocal();
    if (!local?.gitWorktreeActivate || busy) return;
    setBusy(`activate:${item.path}`); setError(""); setNotice("");
    try {
      const result = await local.gitWorktreeActivate(cwd || undefined, item.path);
      if (!result.ok || !result.dir) throw new Error(result.error || "切换工作区失败");
      setNotice(`工作区已切换到 ${shortPath(result.dir)}`);
      onActivated?.(result.dir);
    } catch (e) { setError((e as Error)?.message || "切换工作区失败"); }
    finally { setBusy(""); }
  }

  async function createBranchHere(item: ManagedWorktree) {
    const local = getLocal();
    if (!local?.gitWorktreeBranch || !branchName.trim() || busy) return;
    setBusy(`branch:${item.path}`); setError(""); setNotice("");
    try {
      const result = await local.gitWorktreeBranch(cwd || undefined, item.path, branchName.trim());
      if (!result.ok) throw new Error(result.error || "创建分支失败");
      setNotice(`已在 worktree 创建分支 ${result.branch || branchName.trim()}。Git 不允许同一分支同时在其他 worktree 签出。`);
      setBranchTarget(""); setBranchName(""); await load();
    } catch (e) { setError((e as Error)?.message || "创建分支失败"); }
    finally { setBusy(""); }
  }

  async function togglePermanent(item: ManagedWorktree) {
    const local = getLocal();
    if (!local?.gitWorktreePermanent || busy) return;
    setBusy(`pin:${item.path}`); setError(""); setNotice("");
    try {
      const result = await local.gitWorktreePermanent(cwd || undefined, item.path, !item.permanent);
      if (!result.ok) throw new Error(result.error || "更新永久标记失败");
      setNotice(result.permanent ? "已标记为永久 worktree，不参与自动回收。" : "已取消永久标记。");
      await load();
    } catch (e) { setError((e as Error)?.message || "更新永久标记失败"); }
    finally { setBusy(""); }
  }

  async function remove(item: ManagedWorktree) {
    const local = getLocal();
    if (!local?.gitWorktreeRemove || !item.id || busy) return;
    if (!window.confirm(`只会删除这个已登记且干净的 HashMM worktree：\n${item.path}\n\n分支引用会保留；未提交改动、活动 worktree 或独有 detached 提交会被服务端拒绝。`)) return;
    setBusy(`remove:${item.path}`); setError(""); setNotice("");
    try {
      const result = await local.gitWorktreeRemove(cwd || undefined, item.path, item.id);
      if (!result.ok) throw new Error(result.error || "删除 worktree 失败");
      setNotice(`已回收 ${shortPath(item.path)}`); await load();
    } catch (e) { setError((e as Error)?.message || "删除 worktree 失败"); }
    finally { setBusy(""); }
  }

  if (!repo?.is_git) {
    return <div className="py-16 text-center text-[11.5px]" style={{ color: "var(--text-tertiary)" }}><GitFork size={20} className="mx-auto mb-2" />Worktree 只适用于 Git 仓库；当前目录仍可使用 AGENTS 与计划模式。</div>;
  }

  return <div className="pt-3">
    <div className="flex items-center gap-2">
      <div className="min-w-0 flex-1">
        <div className="text-[11.5px] font-semibold">隔离 Worktree</div>
        <div className="text-[9.5px] font-mono truncate" title={data?.managed_root} style={{ color: "var(--text-tertiary)" }}>{managedCount} 个 HashMM 托管 · {autoCleanup ? `自动保留最近 ${data?.keep_limit || keepLimit} 个` : "自动清理已关闭"} · {data?.managed_root || "读取中…"}</div>
      </div>
      <button onClick={() => setShowCreate(value => !value)} className="px-2.5 py-1 rounded-md text-[10.5px] font-medium text-white" style={{ background: "var(--accent)" }}>新建</button>
      <button onClick={load} disabled={loading} className="p-1.5 rounded-md" title="刷新 worktree"><RefreshCw size={12} className={loading ? "animate-spin" : ""} /></button>
    </div>

    {showCreate && <div className="mt-2 rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div className="grid grid-cols-2 gap-2">
        <label className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>起始分支/提交
          <input value={baseRef} onChange={e => setBaseRef(e.target.value)} className="mt-1 w-full px-2 py-1.5 rounded-md font-mono text-[10.5px] outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        </label>
        <label className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>可选标签
          <input value={label} onChange={e => setLabel(e.target.value)} placeholder="login-fix" className="mt-1 w-full px-2 py-1.5 rounded-md font-mono text-[10.5px] outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        </label>
      </div>
      <label className="mt-2 flex items-start gap-2 text-[10.5px] cursor-pointer"><input type="checkbox" checked={includeLocal} onChange={e => setIncludeLocal(e.target.checked)} className="mt-0.5" /><span>复制当前 tracked 改动（暂存状态不会保留，全部作为未暂存改动；起始点必须是当前 HEAD）</span></label>
      <label className="mt-1.5 flex items-start gap-2 text-[10.5px] cursor-pointer"><input type="checkbox" checked={copyIgnored} onChange={e => setCopyIgnored(e.target.checked)} className="mt-0.5" /><span>复制 `.worktreeinclude` 明确匹配的 ignored 普通文件，并自动复制 ignored `AGENTS.override.md`；可能包含 `.env` 等秘密</span></label>
      <label className="mt-1.5 flex items-start gap-2 text-[10.5px] cursor-pointer"><input type="checkbox" checked={permanent} onChange={e => setPermanentCreate(e.target.checked)} className="mt-0.5" /><span>永久 worktree（不参与自动回收）</span></label>
      {(repo.files || []).some(file => file.untracked) && <div className="mt-2 flex items-start gap-1.5 text-[10px]" style={{ color: "#b45309" }}><AlertTriangle size={11} className="mt-0.5" />普通未跟踪文件不会复制；只有 tracked Diff 和 `.worktreeinclude` 明确选中的 ignored 文件会进入新 worktree。</div>}
      <div className="mt-2 flex justify-end gap-2"><button onClick={() => setShowCreate(false)} className="px-2.5 py-1 text-[10.5px]">取消</button><button onClick={create} disabled={busy === "create"} className="px-3 py-1 rounded-md text-[10.5px] text-white disabled:opacity-50" style={{ background: "var(--accent)" }}>{busy === "create" ? "创建中…" : "创建 detached worktree"}</button></div>
    </div>}

    {error && <div className="mt-2 rounded-lg px-2.5 py-2 text-[10.5px]" style={{ color: "var(--danger,#dc2626)", background: "var(--bg-secondary)" }}>{error}</div>}
    {notice && <div className="mt-2 rounded-lg px-2.5 py-2 text-[10.5px]" style={{ color: "#15803d", background: "var(--bg-secondary)" }}>{notice}</div>}

    <div className="mt-2 grid gap-2">
      {(data?.items || []).map(item => {
        const key = item.id || item.path;
        const isBusy = busy.endsWith(item.path);
        const removeBlocked = item.dirty || !!item.ignored_risk_files || item.active || item.permanent || item.locked || item.unique_detached_commits;
        return <div key={item.path} className="rounded-xl p-2.5" style={{ background: "var(--bg-secondary)", border: `1px solid ${item.active ? "var(--accent)" : "var(--border)"}` }}>
          <div className="flex items-center gap-2">
            <GitBranch size={12} style={{ color: item.managed ? "var(--accent)" : "var(--text-tertiary)" }} />
            <span className="text-[11px] font-semibold truncate" title={item.path}>{item.current ? "当前 checkout" : shortPath(item.path)}</span>
            {item.active && <span className="px-1.5 rounded-full text-[9px] text-white" style={{ background: "var(--accent)" }}>活动</span>}
            {item.managed && <span className="px-1.5 rounded-full text-[9px]" style={{ color: "var(--accent)", border: "1px solid var(--border)" }}>托管</span>}
            {item.permanent && <Pin size={10} style={{ color: "#b45309" }} />}
            {item.locked && <Lock size={10} style={{ color: "#b45309" }} />}
          </div>
          <div className="mt-1 text-[9.5px] font-mono truncate" title={item.path} style={{ color: "var(--text-tertiary)" }}>{item.path}</div>
          <div className="mt-1 flex flex-wrap gap-x-2 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
            <span>{item.detached ? "detached HEAD" : item.branch || "无分支"}</span><span>{item.head?.slice(0, 8)}</span>
            <span style={{ color: item.dirty ? "#b45309" : "#15803d" }}>{item.dirty ? `${item.changed_files} 个改动` : "干净"}</span>
            {!!item.ignored_risk_files && <span style={{ color: "#b45309" }}>{item.ignored_risk_files} 个 ignored 文件无安全副本</span>}
            {item.unique_detached_commits && <span style={{ color: "#b45309" }}>含未锚定提交</span>}
            {item.created_at && <span>{createdAt(item.created_at)}</span>}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {!item.active && <button onClick={() => activate(item)} disabled={!!busy} className="px-2 py-1 rounded text-[10px] disabled:opacity-40" style={{ border: "1px solid var(--border)", color: "var(--accent)" }}>{isBusy ? <Loader2 size={10} className="animate-spin" /> : "切换到这里"}</button>}
            {item.managed && item.detached && <button onClick={() => { setBranchTarget(branchTarget === key ? "" : key); setBranchName(""); }} disabled={!!busy} className="px-2 py-1 rounded text-[10px]" style={{ border: "1px solid var(--border)" }}>在此创建分支</button>}
            {item.managed && <button onClick={() => togglePermanent(item)} disabled={!!busy} title={item.permanent ? "取消永久" : "标记永久"} className="px-2 py-1 rounded text-[10px] inline-flex items-center gap-1" style={{ border: "1px solid var(--border)" }}>{item.permanent ? <PinOff size={10} /> : <Pin size={10} />}{item.permanent ? "取消永久" : "永久"}</button>}
            {item.managed && <button onClick={() => remove(item)} disabled={!!busy || removeBlocked} title={removeBlocked ? "脏、活动、永久、锁定、含 ignored 风险文件或独有 detached 提交的 worktree 不可回收" : "回收干净 worktree"} className="px-2 py-1 rounded text-[10px] inline-flex items-center gap-1 disabled:opacity-35" style={{ border: "1px solid var(--border)", color: "var(--danger,#dc2626)" }}><Trash2 size={10} />回收</button>}
          </div>
          {branchTarget === key && <div className="mt-2 flex gap-1.5"><input value={branchName} onChange={e => setBranchName(e.target.value)} placeholder="feature/login-fix" className="flex-1 px-2 py-1 rounded-md font-mono text-[10px] outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)" }} /><button onClick={() => createBranchHere(item)} disabled={!branchName.trim() || !!busy} className="px-2.5 py-1 rounded-md text-[10px] text-white disabled:opacity-40" style={{ background: "var(--accent)" }}>创建</button></div>}
        </div>;
      })}
    </div>
    {!loading && data?.ok && !(data.items || []).length && <div className="py-12 text-center text-[11px]" style={{ color: "var(--text-tertiary)" }}>Git 没有返回可用 worktree。</div>}
    <div className="mt-3 flex items-start gap-1.5 text-[9.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}><AlertTriangle size={10} className="mt-0.5 flex-shrink-0" />{autoCleanup ? `自动回收只处理超过 ${keepLimit} 个、干净、未永久、非活动、没有独有提交的 untouched detached worktree。` : "自动回收已关闭；只能由你手动回收通过完整安全检查的托管 worktree。"} HashMM 不会用 `--force` 删除用户 worktree。</div>
  </div>;
}
