"use client";
import { showToast } from "@/lib/toast";
import { useState, useEffect, useRef } from "react";
import { Search, FileText, Download, Settings, PlusCircle, BarChart2 } from "lucide-react";

interface Command {
  id: string;
  icon: React.ElementType;
  label: string;
  shortcut?: string;
  action: () => void;
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onNewChat: () => void;
  convId?: string;
}

export function CommandPalette({ isOpen, onClose, onNewChat, convId }: Props) {
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const commands: Command[] = [
    { id: "new", icon: PlusCircle, label: "新建对话", shortcut: "Ctrl+N", action: () => { onNewChat(); onClose(); } },
    { id: "export-md", icon: Download, label: "导出为 Markdown", action: () => {
      if (convId) window.open(`/api/conversations/${convId}/export/markdown`);
      onClose();
    }},
    { id: "export-html", icon: Download, label: "导出为 HTML", action: () => {
      if (convId) window.open(`/api/conversations/${convId}/export/html`);
      onClose();
    }},
    { id: "export-jupyter", icon: Download, label: "导出为 Jupyter Notebook", action: () => {
      if (convId) window.open(`/api/conversations/${convId}/export/jupyter`);
      onClose();
    }},
    { id: "usage", icon: BarChart2, label: "查看用量统计", action: () => {
      fetch("/api/usage").then(r => r.json()).then(d => {
        alert(`今日: ${d.today?.tokens_in || 0} 输入 + ${d.today?.tokens_out || 0} 输出 ≈ ¥${d.today?.cost_cny || 0}\n本月: ${d.month?.tokens_in || 0} 输入 + ${d.month?.tokens_out || 0} 输出 ≈ ¥${d.month?.cost_cny || 0}`);
      });
      onClose();
    }},
    { id: "health", icon: Settings, label: "服务状态", action: () => {
      fetch("/api/health").then(r => r.json()).then(d => {
        alert(`状态: ${d.status}\n版本: ${d.version}\n数据库: ${d.database}\n工具: ${d.tools} 个`);
      });
      onClose();
    }},
  ];

  const filtered = query
    ? commands.filter(c => c.label.toLowerCase().includes(query.toLowerCase()))
    : commands;

  useEffect(() => {
    if (isOpen) {
      setQuery("");
      setSearchResults([]);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  // v24: Full-text search when query looks like a search term
  useEffect(() => {
    if (query.length >= 2 && !commands.some(c => c.label.includes(query))) {
      const timer = setTimeout(() => {
        fetch(`/api/search/semantic?q=${encodeURIComponent(query)}`)
          .then(r => r.json())
          .then(d => setSearchResults(d.results || []))
          .catch(() => {});
      }, 300);
      return () => clearTimeout(timer);
    } else {
      setSearchResults([]);
    }
  }, [query]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div
        className="relative w-full max-w-lg rounded-xl overflow-hidden shadow-2xl"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
          <Search size={16} style={{ color: "var(--text-tertiary)" }} />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="输入命令或搜索..."
            className="flex-1 bg-transparent outline-none text-[14px]"
            style={{ color: "var(--text-primary)" }}
          />
          <kbd className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-secondary)", color: "var(--text-tertiary)" }}>ESC</kbd>
        </div>
        <div className="max-h-[300px] overflow-y-auto py-1">
          {filtered.map(cmd => (
            <button
              key={cmd.id}
              onClick={cmd.action}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-[var(--bg-secondary)] transition-colors text-[13px]"
              style={{ color: "var(--text-primary)" }}
            >
              <cmd.icon size={16} style={{ color: "var(--accent)", flexShrink: 0 }} />
              <span className="flex-1">{cmd.label}</span>
              {cmd.shortcut && (
                <kbd className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-secondary)", color: "var(--text-tertiary)" }}>
                  {cmd.shortcut}
                </kbd>
              )}
            </button>
          ))}
          {searchResults.length > 0 && (
            <div className="px-3 py-1.5 text-[10px] font-medium" style={{ color: "var(--text-tertiary)" }}>
              搜索结果 ({searchResults.length})
            </div>
          )}
          {searchResults.map((r: { session_id: string; title: string; snippet: string }, i: number) => (
            <button key={`sr_${i}`}
              onClick={() => { window.location.href = `/chat/${r.session_id}`; onClose(); }}
              className="w-full flex flex-col gap-0.5 px-4 py-2 text-left hover:bg-[var(--bg-secondary)] transition-colors">
              <span className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>{r.title || "无标题"}</span>
              <span className="text-[11px] truncate" style={{ color: "var(--text-tertiary)" }}>{r.snippet}</span>
            </button>
          ))}
          {filtered.length === 0 && searchResults.length === 0 && query.length > 0 && (
            <div className="px-4 py-6 text-center text-[13px]" style={{ color: "var(--text-tertiary)" }}>
              没有匹配的命令或对话
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
