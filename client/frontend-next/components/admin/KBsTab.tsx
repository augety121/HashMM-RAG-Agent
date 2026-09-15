"use client";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import type { KB } from "@/lib/types";
import * as api from "@/lib/api";
import { withToken, authHeaders } from "@/lib/api";
import { Plus, Trash2, Database, Download, Upload, Loader2, Search } from "lucide-react";

export function KBsTab() {
  const [kbs, setKBs] = useState<KB[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [importing, setImporting] = useState(false);
  const [migrateMsg, setMigrateMsg] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  // V103.90 搜索
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const arr = q ? kbs.filter(k => `${k.name || ""} ${k.description || ""}`.toLowerCase().includes(q)) : kbs.slice();
    return arr.sort((a, b) => (a.name || "").localeCompare(b.name || "", "zh"));
  }, [kbs, query]);

  const load = useCallback(async () => { try { setKBs(await api.listKBs()); } catch (_e) { /* empty */ } }, []);
  useEffect(() => { load(); }, [load]);

  // V91: 数据迁移 —— 导出/导入完整知识库包（语料/索引/图谱/技能/库快照）
  function exportKB() {
    const a = document.createElement("a");
    a.href = withToken("/api/admin/kb/export");
    a.download = "";
    document.body.appendChild(a); a.click(); a.remove();
    setMigrateMsg("已开始下载迁移包（包含语料、索引、图谱、技能与库快照）");
  }

  async function importKB(f: File) {
    if (!confirm("导入将替换当前知识库数据（原数据自动备份到 data/_backup-时间戳）。继续？")) return;
    setImporting(true); setMigrateMsg("");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const res = await fetch("/api/admin/kb/import", { method: "POST", headers: authHeaders(), body: fd });
      const j = await res.json();
      if (!res.ok) throw new Error(j?.detail || j?.error || res.statusText);
      setMigrateMsg(j.message || "导入完成，请重启后端生效");
      load();
    } catch (e) {
      setMigrateMsg("导入失败：" + ((e as Error)?.message || e));
    }
    setImporting(false);
    if (fileRef.current) fileRef.current.value = "";
  }

  async function addKB() {
    if (!name.trim()) return;
    try { await api.createKB({ name, description: desc }); setName(""); setDesc(""); setShowAdd(false); load(); } catch (_e) { /* empty */ }
  }

  async function delKB(id: string, n: string) {
    if (!confirm(`删除知识库 "${n}"？`)) return;
    try { await api.deleteKB(id); load(); } catch (_e) { /* empty */ }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
        <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>知识库管理 <span className="font-normal text-[11px]" style={{ color: "var(--text-tertiary)" }}>共 {kbs.length} 个</span></h4>
        <div className="flex items-center gap-2">
          {kbs.length > 3 && (
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索知识库…"
                className="text-[12px] pl-8 pr-3 py-1.5 rounded-lg outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
          )}
          <button onClick={() => setShowAdd(!showAdd)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white" style={{ background: "var(--accent)" }}><Plus size={14} /> 新建知识库</button>
        </div>
      </div>

      {/* V91: 数据迁移（autodl 与本地一键搬家） */}
      <div className="rounded-xl px-4 py-3 mb-4 flex items-center gap-3 flex-wrap" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
        <div className="flex-1 min-w-[200px]">
          <div className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>数据迁移</div>
          <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>导出完整知识库包（语料/索引/图谱/技能/库快照），在另一套 HashMM（如桌面本地模式）导入即可整体搬家</div>
        </div>
        <button onClick={exportKB} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium" style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
          <Download size={13} /> 导出
        </button>
        <button onClick={() => fileRef.current?.click()} disabled={importing} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white disabled:opacity-60" style={{ background: "var(--accent)" }}>
          {importing ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />} 导入
        </button>
        <input ref={fileRef} type="file" accept=".zip" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) importKB(f); }} />
        {migrateMsg && <div className="w-full text-[11px]" style={{ color: "var(--text-secondary)" }}>{migrateMsg}</div>}
      </div>
      {showAdd && (
        <div className="mb-4 p-4 rounded-xl space-y-3 anim-fade-up" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <input placeholder="知识库名称 *" value={name} onChange={e => setName(e.target.value)} className="w-full h-9 px-3 rounded-lg text-xs outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <textarea placeholder="描述（可选）" value={desc} onChange={e => setDesc(e.target.value)} rows={2} className="w-full px-3 py-2 rounded-lg text-xs outline-none resize-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <div className="flex gap-2">
            <button onClick={addKB} className="px-4 py-1.5 rounded-lg text-xs font-medium text-white" style={{ background: "var(--accent)" }}>创建</button>
            <button onClick={() => setShowAdd(false)} className="px-4 py-1.5 rounded-lg text-xs font-medium" style={{ color: "var(--text-secondary)", background: "var(--bg-tertiary)" }}>取消</button>
          </div>
        </div>
      )}
      <div className="space-y-2">
        {shown.map(kb => (
          <div key={kb.id} className="flex items-center gap-3 p-4 rounded-xl" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <Database size={18} style={{ color: "var(--accent)" }} className="flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{kb.name}</div>
              <div className="text-[11px] truncate" style={{ color: "var(--text-tertiary)" }}>{kb.description || "无描述"} · 创建者: {kb.created_by}</div>
            </div>
            <button onClick={() => delKB(kb.id, kb.name)} className="p-1.5 rounded-lg text-red-400 transition-colors hover:bg-red-50 dark:hover:bg-red-950/20 flex-shrink-0"><Trash2 size={15} /></button>
          </div>
        ))}
        {shown.length === 0 && <div className="text-center py-12 text-sm" style={{ color: "var(--text-tertiary)" }}>{kbs.length ? "没有符合条件的知识库" : "暂无知识库"}</div>}
      </div>
    </div>
  );
}
