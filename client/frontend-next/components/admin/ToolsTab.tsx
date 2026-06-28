"use client";
import { useState, useEffect, useCallback, useMemo } from "react";
import * as api from "@/lib/api";
import { Wrench, ToggleLeft, ToggleRight, Hash, Search } from "lucide-react";
import { CustomToolsPanel } from "./CustomToolsPanel";
import { MCPServersPanel } from "./MCPServersPanel";
import { filterTools, sortTools, toolSummary, toolsInCategory, type ToolState, type ToolSort } from "@/lib/toolFilter";

interface ToolItem {
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  use_count: number;
}

export function ToolsTab() {
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [stats, setStats] = useState<{ total: number; enabled: number } | null>(null);
  // V103.90 搜索 / 状态筛选 / 排序
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<ToolState>("all");
  const [sortBy, setSortBy] = useState<ToolSort>("category");
  const summary = useMemo(() => toolSummary(tools), [tools]);
  const shown = useMemo(() => sortTools(filterTools(tools, { query, state: stateFilter }), sortBy), [tools, query, stateFilter, sortBy]);

  const load = useCallback(async () => {
    try {
      const r = await api.listTools();
      setTools(r.tools || []);
    } catch {}
    try {
      const s = await api.getToolStats();
      setStats(s);
    } catch {}
  }, []);

  useEffect(() => { load(); }, [load]);

  async function handleToggle(name: string, current: boolean) {
    try {
      await api.toggleTool(name, !current);
      setTools(prev => prev.map(t => t.name === name ? { ...t, enabled: !current } : t));
    } catch {}
  }

  // V103.90 按类别批量开关
  async function toggleCategory(cat: string, enable: boolean) {
    const names = toolsInCategory(tools, cat);
    await Promise.all(names.map(n => api.toggleTool(n, enable).catch(() => {})));
    setTools(prev => prev.map(t => t.category === cat ? { ...t, enabled: enable } : t));
  }

  const categories = Array.from(new Set(shown.map(t => t.category)));

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>工具管理</h4>
          <p className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
            Agent 使用的工具列表 · 可以启用/禁用单个工具
          </p>
        </div>
        {stats && (
          <div className="flex items-center gap-3 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
            <span><Wrench size={12} className="inline mr-1" />{summary.total} 个</span>
            <span className="text-green-500">{summary.enabled} 启用</span>
            <span>{summary.disabled} 禁用</span>
            <span className="font-mono">{summary.totalUses.toLocaleString()} 次调用</span>
            {summary.topTool && <span className="hidden sm:inline">最常用 <span className="font-mono" style={{ color: "var(--text-secondary)" }}>{summary.topTool}</span></span>}
          </div>
        )}
      </div>

      {/* V103.90 搜索 / 状态筛选 / 排序 */}
      {tools.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap mb-4">
          <div className="relative flex-1 min-w-[160px]">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索工具名 / 描述…"
              className="w-full text-[12px] pl-8 pr-3 py-1.5 rounded-lg outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          </div>
          <div className="inline-flex rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
            {([["all", "全部"], ["enabled", "启用"], ["disabled", "禁用"]] as const).map(([v, label], i) => (
              <button key={v} onClick={() => setStateFilter(v)} className="px-2.5 py-1 text-[11px] transition-colors"
                style={{ background: stateFilter === v ? "var(--accent-light)" : "transparent", color: stateFilter === v ? "var(--accent)" : "var(--text-tertiary)", borderLeft: i ? "1px solid var(--border)" : "none" }}>{label}</button>
            ))}
          </div>
          <select value={sortBy} onChange={e => setSortBy(e.target.value as ToolSort)}
            className="h-8 px-2 rounded-lg text-[12px] outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            <option value="category">按类别</option><option value="usage">按用量</option><option value="name">按名字</option>
          </select>
        </div>
      )}
      {tools.length > 0 && shown.length === 0 && <div className="text-center py-10 text-[13px]" style={{ color: "var(--text-tertiary)" }}>没有符合条件的工具</div>}

      {categories.map(cat => {
        const catTools = shown.filter(t => t.category === cat);
        const allOn = catTools.every(t => t.enabled);
        return (
        <div key={cat} className="mb-4">
          <div className="flex items-center justify-between px-1 py-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
              {cat === "builtin" ? "内置工具" : cat === "plugin" ? "插件" : cat === "custom" ? "自定义" : cat}
              <span className="ml-1.5 font-mono normal-case">{catTools.length}</span>
            </span>
            <button onClick={() => toggleCategory(cat || "", !allOn)} className="text-[10.5px] px-2 py-0.5 rounded-md transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
              {allOn ? "全部禁用" : "全部启用"}
            </button>
          </div>
          <div className="space-y-1">
            {catTools.map(t => (
              <div key={t.name} className="flex items-center gap-3 px-4 py-3 rounded-xl transition-colors"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", opacity: t.enabled ? 1 : 0.6 }}>
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                  style={{ background: t.enabled ? "var(--accent-light)" : "var(--bg-tertiary)" }}>
                  <Wrench size={14} style={{ color: t.enabled ? "var(--accent)" : "var(--text-tertiary)" }} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] font-semibold font-mono" style={{ color: "var(--text-primary)" }}>{t.name}</div>
                  <div className="text-[10px] mt-0.5 line-clamp-2" style={{ color: "var(--text-tertiary)" }}>
                    {t.description.slice(0, 100)}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {t.use_count > 0 && (
                    <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                      <Hash size={10} className="inline" />{t.use_count}
                    </span>
                  )}
                  <button onClick={() => handleToggle(t.name, t.enabled)}
                    className="transition-colors" title={t.enabled ? "禁用" : "启用"}>
                    {t.enabled
                      ? <ToggleRight size={22} style={{ color: "var(--accent)" }} />
                      : <ToggleLeft size={22} style={{ color: "var(--text-tertiary)" }} />
                    }
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
        );
      })}

      {tools.length === 0 && (
        <div className="text-center py-12">
          <Wrench size={36} style={{ color: "var(--text-tertiary)", opacity: 0.3 }} className="mx-auto mb-3" />
          <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>暂无已注册的工具</p>
          <p className="text-[11px] mt-1" style={{ color: "var(--text-tertiary)" }}>
            工具会在服务启动时自动注册
          </p>
        </div>
      )}

      {/* v14 Phase 3: user-configurable external API tools */}
      <CustomToolsPanel />

      {/* v14 Phase 4: MCP server connections */}
      <MCPServersPanel />
    </div>
  );
}
