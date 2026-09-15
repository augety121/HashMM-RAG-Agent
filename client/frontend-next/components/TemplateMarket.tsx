"use client";
import { useState, useEffect } from "react";
import { Zap, FileText, BookOpen, Code2, BarChart3, Search } from "lucide-react";

interface Template {
  id: string; name: string; category: string;
  prompt: string; variables: string; use_count: number;
}

const ICONS: Record<string, any> = {
  code: Code2, document: FileText, knowledge: BookOpen, general: Zap, data: BarChart3,
};
const COLORS: Record<string, string> = {
  code: "#d97706", document: "#7c3aed", knowledge: "#059669", general: "#2563eb", data: "#dc2626",
};

interface Props {
  onSelect: (prompt: string) => void;
  onClose: () => void;
}

export function TemplateMarket({ onSelect, onClose }: Props) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    fetch("/api/templates").then(r => r.json()).then(d => setTemplates(d.templates || []));
  }, []);

  const filtered = filter
    ? templates.filter(t => t.name.includes(filter) || t.category.includes(filter))
    : templates;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative w-full max-w-2xl max-h-[70vh] rounded-xl overflow-hidden shadow-2xl"
           style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
          <Search size={16} style={{ color: "var(--text-tertiary)" }} />
          <input value={filter} onChange={e => setFilter(e.target.value)}
                 placeholder="搜索模板..." className="flex-1 bg-transparent outline-none text-[14px]"
                 style={{ color: "var(--text-primary)" }} />
        </div>
        <div className="grid grid-cols-2 gap-3 p-4 overflow-y-auto max-h-[55vh]">
          {filtered.map(t => {
            const Icon = ICONS[t.category] || Zap;
            const color = COLORS[t.category] || "#2563eb";
            return (
              <button key={t.id} onClick={() => { onSelect(t.prompt); onClose(); }}
                className="flex items-start gap-3 p-3 rounded-lg text-left transition-all hover:shadow-md"
                style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                <Icon size={18} style={{ color, flexShrink: 0, marginTop: 2 }} />
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>{t.name}</div>
                  <div className="text-[11px] mt-1 truncate" style={{ color: "var(--text-tertiary)" }}>{t.prompt}</div>
                  <div className="text-[10px] mt-1" style={{ color }}>已使用 {t.use_count} 次</div>
                </div>
              </button>
            );
          })}
          {filtered.length === 0 && (
            <div className="col-span-2 py-8 text-center text-[13px]" style={{ color: "var(--text-tertiary)" }}>
              暂无模板。在设置中创建自定义模板。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
