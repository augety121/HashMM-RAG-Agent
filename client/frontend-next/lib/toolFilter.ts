// lib/toolFilter.ts — 工具管理：搜索/状态过滤/排序/用量汇总纯逻辑（V103.90，frontend-next 真 UI）。
// 给后台工具管理加大厂级深度（按关键词/启用状态过滤、按名字/用量/类别排序、用量总览）。
// 纯函数，便于 tsc 编译后单测。

export interface ToolRec { name: string; description?: string; category?: string; enabled?: boolean; use_count?: number; }

export type ToolState = "all" | "enabled" | "disabled";
export type ToolSort = "name" | "usage" | "category";

/** 按关键词（名字或描述，大小写不敏感）+ 启用状态过滤。 */
export function filterTools(tools: ToolRec[], opts: { query?: string; state?: ToolState } = {}): ToolRec[] {
  const arr = Array.isArray(tools) ? tools : [];
  const q = String(opts.query || "").trim().toLowerCase();
  const state = opts.state || "all";
  return arr.filter((t) => {
    if (state === "enabled" && !t.enabled) return false;
    if (state === "disabled" && t.enabled) return false;
    if (q) {
      const hay = `${t.name || ""} ${t.description || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

/** 排序（不改原数组）。name：字母序；usage：用量降序；category：类别再名字。 */
export function sortTools(tools: ToolRec[], by: ToolSort): ToolRec[] {
  const arr = (Array.isArray(tools) ? tools : []).slice();
  if (by === "name") {
    arr.sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  } else if (by === "usage") {
    arr.sort((a, b) => (b.use_count || 0) - (a.use_count || 0));
  } else if (by === "category") {
    arr.sort((a, b) => (a.category || "").localeCompare(b.category || "") || (a.name || "").localeCompare(b.name || ""));
  }
  return arr;
}

export interface ToolSummary { total: number; enabled: number; disabled: number; totalUses: number; topTool: string | null; }

/** 用量总览：总数、启用/禁用、累计调用、用量最高的工具名。 */
export function toolSummary(tools: ToolRec[]): ToolSummary {
  const arr = Array.isArray(tools) ? tools : [];
  let enabled = 0, totalUses = 0, topUses = -1;
  let topTool: string | null = null;
  for (const t of arr) {
    if (t.enabled) enabled++;
    const u = t.use_count || 0;
    totalUses += u;
    if (u > topUses) { topUses = u; topTool = t.name || null; }
  }
  return { total: arr.length, enabled, disabled: arr.length - enabled, totalUses, topTool: topUses > 0 ? topTool : null };
}

/** 某类别下的工具名列表（用于按类别批量开关）。 */
export function toolsInCategory(tools: ToolRec[], category: string): string[] {
  return (Array.isArray(tools) ? tools : []).filter((t) => (t.category || "") === category).map((t) => t.name);
}
