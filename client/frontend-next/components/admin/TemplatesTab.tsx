"use client";
import { useState, useEffect, useCallback, useMemo } from "react";
import { Plus, Trash2, Copy, Edit2, Save, X, Zap, FileText, Code, BookOpen, Search, Pencil, MessageSquare, BarChart3, Sparkles } from "lucide-react";
import { sortTemplates, type TemplateRec, type TemplateSort } from "@/lib/templateSort";

interface Template {
  id: string;
  name: string;
  category: string;
  prompt: string;
  variables: string;
  author: string;
  use_count: number;
  created_at: number;
}

const CATEGORY_ICONS: Record<string, typeof Zap> = {
  general: Zap,
  code: Code,
  analysis: BookOpen,
  document: FileText,
  writing: Pencil,
  research: Search,
  support: MessageSquare,
  data: BarChart3,
  agent: Sparkles,
};

const CATEGORY_COLORS: Record<string, string> = {
  general: "#2563eb",
  code: "#059669",
  analysis: "#d97706",
  document: "#7c3aed",
  writing: "#db2777",
  research: "#0891b2",
  support: "#ea580c",
  data: "#4f46e5",
  agent: "#16a34a",
};

const CATEGORY_LABELS: Record<string, string> = {
  general: "通用",
  code: "代码",
  analysis: "分析",
  document: "文档",
  writing: "写作",
  research: "研究",
  support: "客服",
  data: "数据",
  agent: "智能体",
};

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const t = typeof localStorage !== "undefined" ? localStorage.getItem("hmm_token") : null;
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

export function TemplatesTab() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [catFilter, setCatFilter] = useState("");
  const [editing, setEditing] = useState<Template | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", category: "general", prompt: "", variables: "" });
  // V103.90 排序
  const [sortBy, setSortBy] = useState<TemplateSort>("default");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const url = catFilter ? `/api/admin/templates?category=${catFilter}` : "/api/admin/templates";
      const res = await fetch(url, { headers: authHeaders() });
      const data = await res.json();
      setTemplates(data.templates || []);
    } catch { setTemplates([]); }
    setLoading(false);
  }, [catFilter]);

  useEffect(() => { load(); }, [load]);

  async function handleCreate() {
    if (!form.name.trim() || !form.prompt.trim()) return;
    try {
      let vars: string[] = [];
      if (form.variables.trim()) {
        vars = form.variables.split(",").map(v => v.trim()).filter(Boolean);
      }
      await fetch("/api/admin/templates", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ name: form.name, category: form.category, prompt: form.prompt, variables: vars }),
      });
      setCreating(false);
      setForm({ name: "", category: "general", prompt: "", variables: "" });
      load();
    } catch {}
  }

  async function handleUpdate() {
    if (!editing || !form.name.trim() || !form.prompt.trim()) return;
    try {
      // Delete and recreate (simple approach — backend has no PATCH)
      await fetch(`/api/admin/templates/${editing.id}`, { method: "DELETE", headers: authHeaders() });
      let vars: string[] = [];
      if (form.variables.trim()) {
        vars = form.variables.split(",").map(v => v.trim()).filter(Boolean);
      }
      await fetch("/api/admin/templates", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ name: form.name, category: form.category, prompt: form.prompt, variables: vars }),
      });
      setEditing(null);
      setForm({ name: "", category: "general", prompt: "", variables: "" });
      load();
    } catch {}
  }

  async function handleDelete(id: string) {
    if (!confirm("确定删除此模板？")) return;
    try {
      await fetch(`/api/admin/templates/${id}`, { method: "DELETE", headers: authHeaders() });
      load();
    } catch {}
  }

  function startEdit(t: Template) {
    setEditing(t);
    setCreating(false);
    let vars = "";
    try { vars = JSON.parse(t.variables || "[]").join(", "); } catch {}
    setForm({ name: t.name, category: t.category, prompt: t.prompt, variables: vars });
  }

  function copyPrompt(t: Template) {
    navigator.clipboard.writeText(t.prompt).catch(() => {});
  }

  const filtered = templates.filter(t =>
    !filter || t.name.toLowerCase().includes(filter.toLowerCase()) || t.prompt.toLowerCase().includes(filter.toLowerCase())
  );
  const shown = useMemo(() => sortTemplates(filtered as TemplateRec[], sortBy) as Template[], [filtered, sortBy]);

  const categories = [...new Set(templates.map(t => t.category))];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>提示词模板</h3>
          <p className="text-[12px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{templates.length} 个模板</p>
        </div>
        <button onClick={() => { setCreating(true); setEditing(null); setForm({ name: "", category: "general", prompt: "", variables: "" }); }}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium text-white"
          style={{ background: "var(--accent)" }}>
          <Plus size={14} /> 新建模板
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
          <input value={filter} onChange={e => setFilter(e.target.value)}
            placeholder="搜索模板..."
            className="w-full h-8 pl-9 pr-3 rounded-lg text-[12px] outline-none"
            style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        </div>
        <select value={sortBy} onChange={e => setSortBy(e.target.value as TemplateSort)}
          className="h-8 px-2 rounded-lg text-[11px] outline-none flex-shrink-0" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <option value="default">默认</option><option value="use">按用量</option><option value="name">按名字</option><option value="recent">最新</option>
        </select>
        <div className="flex flex-wrap gap-1">
          <button onClick={() => setCatFilter("")}
            className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${!catFilter ? "text-white" : ""}`}
            style={{ background: !catFilter ? "var(--accent)" : "var(--bg-tertiary)", color: catFilter ? "var(--text-secondary)" : undefined }}>
            全部
          </button>
          {Object.keys(CATEGORY_LABELS).map(cat => (
            <button key={cat} onClick={() => setCatFilter(cat === catFilter ? "" : cat)}
              className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${catFilter === cat ? "text-white" : ""}`}
              style={{ background: catFilter === cat ? (CATEGORY_COLORS[cat] || "var(--accent)") : "var(--bg-tertiary)", color: catFilter === cat ? undefined : "var(--text-secondary)" }}>
              {CATEGORY_LABELS[cat] || cat}
            </button>
          ))}
        </div>
      </div>

      {/* Create / Edit form */}
      {(creating || editing) && (
        <div className="p-4 rounded-xl anim-fade-up" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <h4 className="text-[13px] font-semibold mb-3" style={{ color: "var(--text-primary)" }}>
            {editing ? "编辑模板" : "新建模板"}
          </h4>
          <div className="space-y-3">
            <div className="flex gap-2">
              <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="模板名称"
                className="flex-1 h-8 px-3 rounded-lg text-[12px] outline-none"
                style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
                className="h-8 px-2 rounded-lg text-[12px] outline-none"
                style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                <option value="general">通用</option>
                <option value="code">代码</option>
                <option value="analysis">分析</option>
                <option value="document">文档</option>
              </select>
            </div>
            <textarea value={form.prompt} onChange={e => setForm(f => ({ ...f, prompt: e.target.value }))}
              placeholder="提示词内容... 可用 {{变量名}} 标记可替换变量"
              rows={5}
              className="w-full px-3 py-2 rounded-lg text-[12px] outline-none resize-none"
              style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            <input value={form.variables} onChange={e => setForm(f => ({ ...f, variables: e.target.value }))}
              placeholder="变量列表（逗号分隔，可选）：topic, language, format"
              className="w-full h-8 px-3 rounded-lg text-[12px] outline-none"
              style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            <div className="flex justify-end gap-2">
              <button onClick={() => { setCreating(false); setEditing(null); }}
                className="px-3 py-1.5 rounded-lg text-[12px]" style={{ color: "var(--text-secondary)" }}>取消</button>
              <button onClick={editing ? handleUpdate : handleCreate}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-medium text-white"
                style={{ background: "var(--accent)" }}>
                <Save size={13} /> {editing ? "保存" : "创建"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Template list */}
      {loading ? (
        <div className="text-center py-8 text-[13px]" style={{ color: "var(--text-tertiary)" }}>加载中...</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-8 text-[13px]" style={{ color: "var(--text-tertiary)" }}>
          {templates.length === 0 ? "暂无模板，点击上方按钮创建" : "没有匹配的模板"}
        </div>
      ) : (
        <div className="space-y-2">
          {shown.map(t => {
            const Icon = CATEGORY_ICONS[t.category] || Zap;
            const color = CATEGORY_COLORS[t.category] || "var(--accent)";
            return (
              <div key={t.id} className="p-3 rounded-xl transition-colors hover:bg-[var(--bg-tertiary)] group"
                style={{ border: "1px solid var(--border)" }}>
                <div className="flex items-start gap-3">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5"
                    style={{ background: color + "15" }}>
                    <Icon size={16} style={{ color }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>{t.name}</span>
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-medium"
                        style={{ background: color + "15", color }}>{CATEGORY_LABELS[t.category] || t.category}</span>
                    </div>
                    <p className="text-[11px] mt-1 line-clamp-2" style={{ color: "var(--text-tertiary)" }}>
                      {t.prompt.slice(0, 120)}{t.prompt.length > 120 ? "..." : ""}
                    </p>
                    <div className="flex items-center gap-3 mt-1.5 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                      <span>使用 {t.use_count} 次</span>
                      <span>{t.author}</span>
                    </div>
                  </div>
                  <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => copyPrompt(t)} className="p-1.5 rounded hover:bg-[var(--bg-secondary)]" title="复制">
                      <Copy size={13} style={{ color: "var(--text-tertiary)" }} />
                    </button>
                    <button onClick={() => startEdit(t)} className="p-1.5 rounded hover:bg-[var(--bg-secondary)]" title="编辑">
                      <Edit2 size={13} style={{ color: "var(--text-tertiary)" }} />
                    </button>
                    <button onClick={() => handleDelete(t.id)} className="p-1.5 rounded hover:bg-[var(--bg-secondary)]" title="删除">
                      <Trash2 size={13} style={{ color: "#ef4444" }} />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
