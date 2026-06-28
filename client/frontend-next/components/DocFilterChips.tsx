"use client";
import { useState, useEffect, useCallback } from "react";
import { X, FileText, Filter } from "lucide-react";

interface Doc {
  filename: string;
  num_chunks: number;
}

interface Props {
  selected: string[];
  onChange: (filenames: string[]) => void;
}

/**
 * DocFilterChips — displays indexed documents as selectable chips.
 *
 * When one or more documents are selected, the chat retrieval will
 * be filtered to only search within those documents. When none are
 * selected, all documents are searched (default behavior).
 *
 * Usage in ChatArea:
 *   <DocFilterChips selected={docFilter} onChange={setDocFilter} />
 *   // Then pass docFilter to the stream request
 */
export function DocFilterChips({ selected, onChange }: Props) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const loadDocs = useCallback(async () => {
    if (docs.length > 0) return; // Already loaded
    setLoading(true);
    try {
      const token = typeof localStorage !== "undefined" ? localStorage.getItem("hmm_token") : null;
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch("/api/admin/metrics", { headers });
      if (res.ok) {
        const data = await res.json();
        const docList = data.retrieval?.documents || [];
        setDocs(docList);
      }
    } catch {}
    setLoading(false);
  }, [docs.length]);

  function toggle(filename: string) {
    if (selected.includes(filename)) {
      onChange(selected.filter(f => f !== filename));
    } else {
      onChange([...selected, filename]);
    }
  }

  function clear() {
    onChange([]);
  }

  // Don't render if no docs
  if (docs.length === 0 && selected.length === 0) {
    return (
      <button
        onClick={() => { loadDocs(); setOpen(true); }}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] transition-colors hover:bg-[var(--bg-tertiary)]"
        style={{ color: "var(--text-tertiary)" }}
        title="选择检索范围"
      >
        <Filter size={11} /> 文档过滤
      </button>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {/* Selected chips */}
      {selected.map(fn => (
        <span key={fn} className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-md text-[10px] font-medium"
          style={{ background: "var(--accent-light)", color: "var(--accent)", border: "1px solid var(--accent)" }}>
          <FileText size={10} />
          <span className="truncate max-w-[120px]">{fn}</span>
          <button onClick={() => toggle(fn)} className="p-0.5 rounded hover:bg-[var(--accent-mid)]">
            <X size={9} />
          </button>
        </span>
      ))}

      {/* Add button */}
      <div className="relative">
        <button
          onClick={() => { loadDocs(); setOpen(!open); }}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] transition-colors hover:bg-[var(--bg-tertiary)]"
          style={{ color: selected.length > 0 ? "var(--accent)" : "var(--text-tertiary)" }}
        >
          <Filter size={11} />
          {selected.length > 0 ? `${selected.length} 篇` : "文档过滤"}
        </button>

        {open && (
          <>
            <div className="fixed inset-0 z-[49]" onClick={() => setOpen(false)} />
            <div className="absolute bottom-full left-0 mb-2 z-[50] w-[280px] rounded-xl overflow-hidden anim-fade-up"
              style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "0 -4px 20px rgba(0,0,0,0.12)" }}>
              <div className="px-3 py-2 flex items-center justify-between" style={{ borderBottom: "1px solid var(--border)" }}>
                <span className="text-[11px] font-semibold" style={{ color: "var(--text-secondary)" }}>选择检索范围</span>
                {selected.length > 0 && (
                  <button onClick={clear} className="text-[10px]" style={{ color: "var(--accent)" }}>清除</button>
                )}
              </div>
              {loading ? (
                <div className="px-3 py-4 text-center text-[11px]" style={{ color: "var(--text-tertiary)" }}>加载中...</div>
              ) : docs.length === 0 ? (
                <div className="px-3 py-4 text-center text-[11px]" style={{ color: "var(--text-tertiary)" }}>暂无已索引文档</div>
              ) : (
                <div className="max-h-[200px] overflow-y-auto">
                  {docs.map(d => {
                    const isSelected = selected.includes(d.filename);
                    return (
                      <button key={d.filename} onClick={() => toggle(d.filename)}
                        className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-[11px] transition-colors hover:bg-[var(--bg-secondary)]">
                        <div className={`w-3.5 h-3.5 rounded border flex items-center justify-center flex-shrink-0 ${isSelected ? "border-[var(--accent)]" : ""}`}
                          style={{ background: isSelected ? "var(--accent)" : "transparent", borderColor: isSelected ? "var(--accent)" : "var(--border)" }}>
                          {isSelected && <span className="text-white text-[8px]">✓</span>}
                        </div>
                        <FileText size={12} style={{ color: "var(--text-tertiary)" }} className="flex-shrink-0" />
                        <span className="truncate flex-1" style={{ color: "var(--text-primary)" }}>{d.filename}</span>
                        <span className="text-[9px] font-mono flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>{d.num_chunks}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Clear all */}
      {selected.length > 0 && (
        <button onClick={clear} className="text-[10px] px-1.5 py-0.5 rounded hover:bg-[var(--bg-tertiary)]"
          style={{ color: "var(--text-tertiary)" }}>
          清除
        </button>
      )}
    </div>
  );
}
