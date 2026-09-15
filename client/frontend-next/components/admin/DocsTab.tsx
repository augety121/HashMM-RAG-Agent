"use client";
import React, { useState, useEffect, useCallback, useRef } from "react";
import { RefreshCw, Loader2, Upload, Trash2, Search, X, RotateCw,
         CheckCircle2, Clock, XCircle, ChevronDown, Zap, FolderOpen, Archive } from "lucide-react";
import { useToast } from "../Toast";

interface DocInfo {
  doc_id: string; filename: string; chunks: number;
  tables?: number; images?: number; quality?: number;
  parser?: string; file_size?: number; full_text_chars?: number;
  status?: string; created_at?: string | number; updated_at?: string | number; summary?: string;
  source_available?: boolean; folder?: string;
}

type Tab = "all" | "completed" | "analyzing" | "processing" | "queued" | "failed";

function Badge({ s }: { s: string }) {
  const m: Record<string, [string, string]> = {
    completed: ["已完成", "#059669"], processing: ["处理中", "#2563eb"],
    analyzing: ["分析中", "#d97706"], queued: ["等待中", "#6b7280"], failed: ["失败", "#ef4444"],
  };
  const [label, color] = m[s] || m.completed;
  const Icon = s === "failed" ? XCircle : s === "completed" ? CheckCircle2 : Clock;
  return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium"
    style={{ color, background: `${color}10` }}><Icon size={11}/>{label}</span>;
}

function fmtTime(ts?: string | number) {
  if (!ts) return "—";
  try {
    // Handle Unix timestamp (seconds) vs ISO string
    const d = typeof ts === "number" ? new Date(ts > 1e12 ? ts : ts * 1000) : new Date(ts);
    if (isNaN(d.getTime())) return "—";
    const p = (n: number) => String(n).padStart(2,"0");
    return `${d.getFullYear()}/${p(d.getMonth()+1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  } catch { return String(ts); }
}

export function DocsTab() {
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [total, setTotal] = useState({ chunks: 0, docs: 0 });
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<Tab>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(new Set(["pdfs"])); // Folders collapsed by default
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();

  const hdr = useCallback(() => {
    const h: Record<string,string> = {}; const t = localStorage.getItem("hmm_token");
    if (t) h["Authorization"] = `Bearer ${t}`; return h;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/admin/docs", { headers: hdr() });
      const r = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(r.detail || `服务返回 ${response.status}`);
      if (r.error) throw new Error(r.error);
      const d: DocInfo[] = (r.docs || []).map((item: DocInfo) => ({
        ...item,
        status: item.status || (item.chunks > 0 ? "completed" : "queued"),
      }));
      setDocs(d);
      setTotal({ chunks: r.total_chunks ?? 0, docs: r.total_docs ?? d.length });
      setLoadError("");
      setLastUpdatedAt(Date.now());
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "文档状态读取失败");
    } finally {
      setLoading(false);
    }
  }, [hdr]);
  useEffect(() => { load(); }, [load]);

  async function upload(files: FileList | File[]) {
    setUploading(true); setMsg(""); let ok = 0; const failures: string[] = []; const receipts: string[] = [];
    for (const f of Array.from(files)) {
      try {
        const fd = new FormData(); fd.append("file", f);
        const response = await fetch("/api/admin/docs/upload-and-parse", { method: "POST", headers: hdr(), body: fd });
        const r = await response.json().catch(() => ({}));
        if (!response.ok || !r.ok) {
          failures.push(`${f.name}：${r.detail || r.error || `服务返回 ${response.status}`}`);
        } else {
          ok++;
          if (r.upload?.sha256) receipts.push(`${f.name} ${String(r.upload.sha256).slice(0, 10)}`);
        }
      } catch (e: unknown) { failures.push(`${f.name}：${(e as Error).message || "网络错误"}`); }
    }
    setMsg(failures.length
      ? `失败 ${failures.length} 个；成功 ${ok} 个。${failures.slice(0, 2).join("；")}`
      : `${ok} 个文件解析完成${receipts.length ? ` · 内容回执 ${receipts.join("，")}` : ""}`);
    if (fileRef.current) fileRef.current.value = "";
    setUploading(false);
    if (ok > 0) await load();
  }

  async function delSelected() {
    if (!selected.size || !confirm(`删除 ${selected.size} 个文档？`)) return;
    const failed = new Set<string>(); let removed = 0;
    for (const id of selected) {
      try {
        const response = await fetch(`/api/admin/docs/${encodeURIComponent(id)}`, { method: "DELETE", headers: hdr() });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.ok) failed.add(id); else removed++;
      } catch { failed.add(id); }
    }
    setSelected(failed);
    toast(failed.size ? `已删除 ${removed} 个，${failed.size} 个未删除并保持选中` : `已删除 ${removed} 个文档`, failed.size ? "error" : "success");
    if (removed > 0) await load();
  }

  const counts: Record<string, number> = { all: docs.length };
  for (const d of docs) counts[d.status || "completed"] = (counts[d.status || "completed"] || 0) + 1;

  let list = docs.filter(d => {
    if (tab !== "all" && d.status !== tab) return false;
    return !search || d.filename.toLowerCase().includes(search.toLowerCase());
  }).sort((a, b) => {
    const ta = typeof a.updated_at === "number" ? a.updated_at : new Date(a.updated_at || 0).getTime() / 1000;
    const tb = typeof b.updated_at === "number" ? b.updated_at : new Date(b.updated_at || 0).getTime() / 1000;
    return tb - ta;
  });

  // v12: Group by folder
  const folderGroups: { folder: string; docs: typeof list }[] = [];
  const folderMap = new Map<string, typeof list>();
  for (const d of list) {
    const folder = d.folder || ".";
    if (!folderMap.has(folder)) folderMap.set(folder, []);
    folderMap.get(folder)!.push(d);
  }
  // Root folder first, then alphabetical
  const sortedFolders = [...folderMap.keys()].sort((a, b) => {
    if (a === ".") return -1; if (b === ".") return 1; return a.localeCompare(b);
  });
  for (const f of sortedFolders) folderGroups.push({ folder: f, docs: folderMap.get(f)! });

  const tabs: { key: Tab; label: string }[] = [
    { key: "all", label: `全部\n(${counts.all || 0})` },
    { key: "completed", label: `已完成\n(${counts.completed || 0})` },
    { key: "analyzing", label: `分析中\n(${counts.analyzing || 0})` },
    { key: "processing", label: `处理中\n(${counts.processing || 0})` },
    { key: "queued", label: `等待中\n(${counts.queued || 0})` },
    { key: "failed", label: `失败\n(${counts.failed || 0})` },
  ];

  return (
    <div onDragOver={e => { e.preventDefault(); setDragOver(true); }}
         onDragLeave={() => setDragOver(false)}
         onDrop={e => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files.length) upload(e.dataTransfer.files); }}>

      {/* ── Actions ── */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <div className="flex flex-wrap gap-2">
          <button onClick={load} disabled={loading} className="admin-btn"><RotateCw size={13} className={loading ? "animate-spin" : ""}/> {loading ? "核对中" : "刷新"}</button>
          <button onClick={async () => {
            if (!confirm("批量重新解析所有文档？这需要较长时间。")) return;
            setMsg("正在等待批量重新解析回执；收到结果前不会把任务标记为完成。");
            try {
              const resp = await fetch("/api/admin/docs/batch-reparse", { method: "POST", headers: hdr() });
              if (resp.ok) {
                const r = await resp.json().catch(() => ({} as { ok?: boolean; success?: number; total?: number; error?: string }));
                setMsg(r.ok ? `完成: ${r.success}/${r.total} 成功` : (r.error || "已提交"));
              } else {
                setMsg(`失败：未取得批量任务回执（服务返回 ${resp.status}），无法确认是否已执行。请稍后刷新核对状态。`);
              }
            } catch {
              setMsg("失败：未取得批量任务回执，无法确认是否已执行。请稍后刷新核对状态。");
            }
            load();
            setTimeout(() => load(), 30000);   // 30 秒后自动再刷新一次
          }} className="admin-btn"><Zap size={13}/> 批量重新解析</button>
        </div>
        <div className="flex flex-wrap gap-2">
          {selected.size > 0 && (
            <button onClick={delSelected} className="text-[12px] px-3 py-1.5 rounded-lg flex items-center gap-1.5 font-medium"
              style={{ color: "#ef4444", background: "rgba(239,68,68,0.06)", border: "1px solid rgba(239,68,68,0.15)" }}>
              <Trash2 size={12}/> 删除 ({selected.size})
            </button>
          )}
          <button onClick={async () => {
            try {
              const response = await fetch("/api/admin/docs/scan-files", { headers: hdr() });
              const r = await response.json().catch(() => ({}));
              if (!response.ok) throw new Error(r.detail || `服务返回 ${response.status}`);
              const parsed = r.files?.filter((f: {has_parsed: boolean}) => f.has_parsed).length || 0;
              toast(`发现 ${r.total} 个源文件（${parsed} 个已解析）`, "success");
            } catch { toast("扫描失败", "error"); }
          }} className="admin-btn"><Search size={13}/> 扫描源文件</button>
          <input ref={fileRef} type="file" className="hidden" multiple accept=".pdf,.docx,.doc,.xlsx,.xls,.pptx,.txt,.md,.csv,.html"
            onChange={e => e.target.files && upload(e.target.files)}/>
          <button onClick={() => fileRef.current?.click()} disabled={uploading} className="admin-btn-primary">
            {uploading ? <Loader2 size={13} className="animate-spin"/> : <Upload size={13}/>}
            {uploading ? "解析中..." : "上传"}
          </button>
        </div>
      </div>

      {loadError && (
        <div className="mb-4 px-4 py-3 rounded-xl flex items-start justify-between gap-3"
          style={{ background: "#d9770608", border: "1px solid #d9770633" }}>
          <div>
            <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>文档状态暂时无法验证</div>
            <div className="text-[11px] mt-1" style={{ color: "var(--text-secondary)" }}>{loadError}。已有列表为最近一次成功结果，不会用空列表覆盖。</div>
          </div>
          <button onClick={load} className="admin-btn">重试</button>
        </div>
      )}

      {/* ── Tabs + Search ── */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex gap-0.5 p-1 rounded-lg" style={{ background: "var(--bg-tertiary)" }}>
          {tabs.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className="px-4 py-2 rounded-md text-[12px] font-medium text-center whitespace-pre-line leading-tight transition-all"
              style={{
                color: tab === t.key ? "var(--accent)" : "var(--text-tertiary)",
                background: tab === t.key ? "var(--bg-primary)" : "transparent",
                boxShadow: tab === t.key ? "0 1px 3px rgba(0,0,0,0.06)" : "none",
              }}>{t.label}</button>
          ))}
          <button onClick={load} className="px-2 py-2 rounded-md" title="刷新">
            <RefreshCw size={12} style={{ color: "var(--text-tertiary)" }}/>
          </button>
        </div>
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }}/>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="文件名"
            className="pl-9 pr-3 py-2 rounded-lg text-[13px] w-[220px]"
            style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)", outline: "none" }}/>
        </div>
      </div>

      {/* ── Drag overlay ── */}
      {dragOver && (
        <div className="mb-5 py-14 rounded-xl border-2 border-dashed text-center text-[14px]"
          style={{ borderColor: "var(--accent)", background: "rgba(16,185,129,0.03)", color: "var(--accent)" }}>
          <Upload size={32} className="mx-auto mb-3 opacity-40"/> 松开即上传并解析
        </div>
      )}

      {msg && (
        <div className="mb-4 px-4 py-3 rounded-lg text-[13px] flex items-center justify-between"
          style={{ background: msg.includes("失败") ? "#fef2f2" : "#f0fdf4", color: msg.includes("失败") ? "#ef4444" : "#059669" }}>
          <span>{msg}</span><button onClick={() => setMsg("")}><X size={14}/></button>
        </div>
      )}

      {/* ── Table ── */}
      <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
        {/* Header — 只保留核心列 */}
        <table className="w-full">
          <thead>
            <tr className="text-[11px] font-semibold" style={{ background: "var(--bg-secondary)", color: "var(--text-tertiary)", borderBottom: "1px solid var(--border)" }}>
              <th className="w-10 py-3 text-center">
                <input type="checkbox" checked={selected.size === list.length && list.length > 0}
                  onChange={() => selected.size === list.length ? setSelected(new Set()) : setSelected(new Set(list.map(d => d.doc_id)))}
                  style={{ accentColor: "var(--accent)" }}/>
              </th>
              <th className="text-left py-3 pl-2">文件名</th>
              <th className="text-left py-3 pl-4 w-[200px]">摘要</th>
              <th className="text-center py-3 w-[70px]">状态</th>
              <th className="text-right py-3 pr-3 w-[80px]">长度</th>
              <th className="text-right py-3 pr-4 w-[70px]">分块</th>
              <th className="text-left py-3 pl-4 w-[170px]">创建时间</th>
              <th className="text-left py-3 pl-2 w-[170px]">更新时间 ↓</th>
              <th className="w-8"></th>
            </tr>
          </thead>
          <tbody>
            {list.length === 0 ? (
              <tr><td colSpan={9} className="text-center py-20 text-[13px]" style={{ color: "var(--text-tertiary)" }}>
                {docs.length === 0 ? "暂无文档 — 拖拽文件到此处或点击「上传」" : "未找到匹配的文档"}
              </td></tr>
            ) : (<>{folderGroups.flatMap(group => {
              const rows: React.ReactNode[] = [];
              const isCollapsed = group.folder !== "." && collapsedFolders.has(group.folder);
              if (group.folder !== ".") {
                rows.push(
                  <tr key={`folder-${group.folder}`} className="cursor-pointer"
                    onClick={() => {
                      const next = new Set(collapsedFolders);
                      next.has(group.folder) ? next.delete(group.folder) : next.add(group.folder);
                      setCollapsedFolders(next);
                    }}>
                    <td colSpan={9} className="py-2.5 pl-4 text-[12px] font-semibold select-none"
                      style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)", borderBottom: "1px solid var(--border)" }}>
                      <span style={{ display: "inline-block", transition: "transform 0.15s", transform: isCollapsed ? "rotate(-90deg)" : "rotate(0deg)" }}>▾</span>
                      {" "}<FolderOpen size={13} style={{ display: "inline", verticalAlign: "-2px", color: "#d97706" }} /> {group.folder}
                      <span className="font-normal ml-2" style={{ color: "var(--text-tertiary)" }}>({group.docs.length} 个文件)</span>
                    </td>
                  </tr>
                );
              }
              if (isCollapsed) return rows;
              for (const d of group.docs) {
                const sel = selected.has(d.doc_id);
                const exp = expanded === d.doc_id;
                rows.push(
                  <tr key={d.doc_id}
                      className="cursor-pointer transition-colors hover:bg-[var(--bg-secondary)]"
                      style={{ borderBottom: "1px solid var(--border)", background: sel ? "rgba(16,185,129,0.03)" : "transparent" }}
                      onClick={() => setExpanded(exp ? null : d.doc_id)}>
                    <td className="text-center py-4" onClick={e => e.stopPropagation()}>
                      <input type="checkbox" checked={sel} onChange={() => {
                        const s = new Set(selected); s.has(d.doc_id) ? s.delete(d.doc_id) : s.add(d.doc_id); setSelected(s);
                      }} style={{ accentColor: "var(--accent)" }}/>
                    </td>
                    <td className="py-4 pl-2">
                      <div className="font-medium text-[13px] leading-snug" style={{ color: "var(--text-primary)" }}>{d.filename}</div>
                      <div className="text-[10px] mt-0.5 opacity-50" style={{ color: "var(--text-tertiary)" }}>{d.doc_id}</div>
                    </td>
                    <td className="py-4 pl-4">
                      <span className="text-[12px] line-clamp-1" style={{ color: "var(--text-secondary)" }}>
                        {d.summary || d.doc_id.replace(/^doc-/, "").slice(0, 30)}
                      </span>
                    </td>
                    <td className="py-4 text-center"><Badge s={d.status || "completed"}/></td>
                    <td className="py-4 pr-3 text-right tabular-nums text-[13px]" style={{ color: "var(--text-secondary)" }}>
                      {d.full_text_chars ? d.full_text_chars.toLocaleString() : "—"}
                    </td>
                    <td className="py-4 pr-4 text-right tabular-nums text-[13px] font-semibold" style={{ color: "var(--accent)" }}>
                      {d.chunks || 0}
                    </td>
                    <td className="py-4 pl-4 text-[12px] tabular-nums" style={{ color: "var(--text-tertiary)" }}>{fmtTime(d.created_at)}</td>
                    <td className="py-4 pl-2 text-[12px] tabular-nums" style={{ color: "var(--text-tertiary)" }}>{fmtTime(d.updated_at)}</td>
                    <td className="py-4 text-center">
                      <ChevronDown size={14} style={{ color: "var(--text-tertiary)", transition: "transform 150ms", transform: exp ? "rotate(180deg)" : "none" }}/>
                    </td>
                  </tr>
                );
                if (exp) {
                  rows.push(
                    <tr key={d.doc_id + "-detail"} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td colSpan={9} className="px-14 py-5" style={{ background: "var(--bg-tertiary)" }}>
                        <div className="grid grid-cols-4 gap-x-8 gap-y-3 text-[12px]" style={{ color: "var(--text-secondary)" }}>
                          <div><span className="text-[10px] uppercase tracking-wider block mb-0.5" style={{ color: "var(--text-tertiary)" }}>解析器</span>{d.parser || "—"}</div>
                          <div><span className="text-[10px] uppercase tracking-wider block mb-0.5" style={{ color: "var(--text-tertiary)" }}>表格</span>{d.tables ?? 0} 张</div>
                          <div><span className="text-[10px] uppercase tracking-wider block mb-0.5" style={{ color: "var(--text-tertiary)" }}>图片</span>{d.images ?? 0} 张</div>
                          <div><span className="text-[10px] uppercase tracking-wider block mb-0.5" style={{ color: "var(--text-tertiary)" }}>质量</span>{d.quality ? `${(d.quality*100).toFixed(0)}%` : "—"}</div>
                        </div>
                        <div className="mt-3 text-[11px] font-mono px-2 py-1.5 rounded-lg" style={{ background: "var(--bg-secondary)", color: "var(--text-tertiary)" }}>
                          <span style={{ color: "var(--text-secondary)" }}>doc_id:</span> {d.doc_id}
                          <br/><span style={{ color: "var(--text-secondary)" }}>源文件:</span> {d.source_available ? "可用于重新解析" : "未保留可用源文件"}
                          {d.folder && d.folder !== "." && <><br/><span style={{ color: "var(--text-secondary)" }}>文件夹:</span> {d.folder}</>}
                        </div>
                        <div className="flex gap-3 mt-4">
                          <button onClick={e => { e.stopPropagation();
                            if (confirm(`重新解析 ${d.filename}？`)) {
                              fetch(`/api/admin/docs/${d.doc_id}/reparse`, { method: "POST", headers: hdr() })
                                .then(r => r.json()).then(res => {
                                  if (res.ok) { toast(res.message || "重新解析完成", "success"); load(); }
                                  else toast(res.error || "重新解析失败", "error");
                                }).catch(() => toast("请求失败", "error"));
                            }
                          }} className="text-[12px] px-3 py-1.5 rounded-lg flex items-center gap-1.5"
                            style={{ color: "var(--accent)", border: "1px solid var(--border)" }}>
                            <RotateCw size={11}/> 重新解析
                          </button>
                          <button onClick={e => { e.stopPropagation();
                            if (confirm(`把「${d.filename}」移入失效区（归档）？归档后从检索中排除，可在「失效区」里恢复。`)) {
                              fetch("/api/kb/validity/archive", { method: "POST", headers: { ...hdr(), "Content-Type": "application/json" }, body: JSON.stringify({ filename: d.filename }) })
                                .then(r => r.json()).then(res => {
                                  if (res.ok) { toast(`已把「${d.filename}」移入失效区`, "success"); load(); setExpanded(null); }
                                  else toast(res.error || "操作失败（需管理员）", "error");
                                }).catch(() => toast("请求失败", "error"));
                            }
                          }} className="text-[12px] px-3 py-1.5 rounded-lg flex items-center gap-1.5"
                            style={{ color: "#d97706", border: "1px solid rgba(217,119,6,0.25)" }}>
                            <Archive size={11}/> 移入失效区
                          </button>
                          <button onClick={e => { e.stopPropagation();
                            if (confirm(`删除 ${d.filename}？`)) {
                              fetch(`/api/admin/docs/${encodeURIComponent(d.doc_id)}`, { method: "DELETE", headers: hdr() })
                                .then(async response => {
                                  const result = await response.json().catch(() => ({}));
                                  if (!response.ok || !result.ok) throw new Error(result.detail || "服务器未确认删除");
                                  toast(`已删除「${d.filename}」`, "success");
                                  setExpanded(null);
                                  await load();
                                })
                                .catch(error => toast(error instanceof Error ? error.message : "删除失败", "error"));
                            }
                          }} className="text-[12px] px-3 py-1.5 rounded-lg flex items-center gap-1.5"
                            style={{ color: "#ef4444", border: "1px solid rgba(239,68,68,0.2)" }}>
                            <Trash2 size={11}/> 删除文档
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                }
              }
              return rows;
            })}</>)}
          </tbody>

        </table>
      </div>

      <div className="mt-4 px-1 text-[12px]" style={{ color: "var(--text-tertiary)" }}>
        {total.docs} 篇文档 · {total.chunks.toLocaleString()} 个切片
        {lastUpdatedAt ? ` · 已核对 ${new Date(lastUpdatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
      </div>
    </div>
  );
}
