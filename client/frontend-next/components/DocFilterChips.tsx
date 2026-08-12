"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, FileText, Filter, Search, X } from "lucide-react";
import { listKnowledgeDocuments } from "@/lib/api";

interface Doc {
  filename: string;
  num_chunks: number;
}

interface Props {
  selected: string[];
  onChange: (filenames: string[]) => void;
}

export function DocFilterChips({ selected, onChange }: Props) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  const loadDocs = useCallback(async () => {
    if (loading || docs.length > 0) return;
    setLoading(true); setError("");
    try {
      const result = await listKnowledgeDocuments();
      const normalized = (result.documents || []).map(item => {
        const row = item as Record<string, unknown>;
        return {
          filename: String(row.filename || row.name || row.title || ""),
          num_chunks: Number(row.num_chunks || row.chunk_count || row.chunks || 0),
        };
      }).filter(item => item.filename);
      setDocs(normalized);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "资料列表加载失败");
    } finally {
      setLoading(false);
    }
  }, [docs.length, loading]);

  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open]);

  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    return text ? docs.filter(doc => doc.filename.toLowerCase().includes(text)) : docs;
  }, [docs, query]);

  const toggle = (filename: string) => {
    onChange(selected.includes(filename)
      ? selected.filter(item => item !== filename)
      : [...selected, filename]);
  };

  return (
    <div className="relative flex-shrink-0">
      <button
        onClick={() => { setOpen(value => !value); void loadDocs(); }}
        aria-haspopup="dialog" aria-expanded={open}
        className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-[10px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
        style={{
          color: selected.length > 0 ? "var(--accent)" : "var(--text-tertiary)",
          background: open || selected.length > 0 ? "var(--accent-light)" : "transparent",
        }}
        title={selected.length > 0 ? `只检索已选的 ${selected.length} 份资料` : "限定本轮 Chat 的资料范围"}
      >
        <Filter size={11} />
        <span className="composer-tool-label">{selected.length > 0 ? `资料 ${selected.length}` : "资料范围"}</span>
      </button>

      {open && (
        <>
          <button type="button" className="fixed inset-0 z-[79] cursor-default" aria-label="关闭资料范围" onClick={() => setOpen(false)} />
          <div role="dialog" aria-label="选择资料范围"
            className="absolute bottom-full right-0 mb-2 z-[80] w-[320px] max-w-[min(88vw,320px)] rounded-2xl overflow-hidden anim-fade-up"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
            <div className="px-3 pt-3 pb-2" style={{ borderBottom: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>资料范围</div>
                  <div className="text-[9.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>不选择时由 HashMM 在当前账号可见资料中自动检索</div>
                </div>
                {selected.length > 0 && <button onClick={() => onChange([])} className="text-[10px]" style={{ color: "var(--accent)" }}>全部资料</button>}
                <button onClick={() => setOpen(false)} className="p-1 rounded-md hover:bg-[var(--bg-tertiary)]" aria-label="关闭"><X size={13} /></button>
              </div>
              <label className="mt-2 h-8 px-2.5 rounded-lg flex items-center gap-2" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <Search size={12} style={{ color: "var(--text-tertiary)" }} />
                <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索资料"
                  className="min-w-0 flex-1 bg-transparent outline-none text-[10.5px]" style={{ color: "var(--text-primary)" }} />
              </label>
            </div>
            <div className="max-h-[260px] overflow-y-auto p-1.5">
              {loading ? <div className="px-3 py-7 text-center text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>正在读取资料库…</div>
                : error ? <div className="px-3 py-7 text-center text-[10.5px]" style={{ color: "var(--error)" }}>{error}</div>
                : visible.length === 0 ? <div className="px-3 py-7 text-center text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>没有匹配的资料</div>
                : visible.map(doc => {
                  const active = selected.includes(doc.filename);
                  return (
                    <button key={doc.filename} onClick={() => toggle(doc.filename)}
                      className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl text-left hover:bg-[var(--bg-secondary)]">
                      <span className="w-4 h-4 rounded-md flex items-center justify-center flex-shrink-0"
                        style={{ background: active ? "var(--accent)" : "transparent", border: `1px solid ${active ? "var(--accent)" : "var(--border)"}` }}>
                        {active && <Check size={10} color="white" />}
                      </span>
                      <FileText size={13} className="flex-shrink-0" style={{ color: "var(--text-tertiary)" }} />
                      <span className="min-w-0 flex-1 truncate text-[10.5px]" style={{ color: "var(--text-primary)" }}>{doc.filename}</span>
                      {doc.num_chunks > 0 && <span className="text-[9px] tabular-nums" style={{ color: "var(--text-tertiary)" }}>{doc.num_chunks} 段</span>}
                    </button>
                  );
                })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

