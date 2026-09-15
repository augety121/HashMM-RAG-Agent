// lib/memoryFilter.ts — 记忆中心：搜索/类别筛选/排序/统计纯逻辑（V103.90，frontend-next 真 UI）。
// 给记忆中心加大厂级深度（按关键词/类别过滤、按置信度/最近使用排序、汇总统计）。
// 基于分组结构（Record<category, Mem[]>）操作，纯函数便于 tsc 编译后单测。

export interface Mem { id: string; key: string; value: string; confidence: number; last_used?: number | null; }
export type MemGroups = Record<string, Mem[]>;
export type MemSort = "confidence" | "recent" | "default";

/** 按关键词（标签或内容，大小写不敏感）+ 类别过滤；空组移除。 */
export function filterGroups(groups: MemGroups, opts: { query?: string; category?: string } = {}): MemGroups {
  const g = groups && typeof groups === "object" ? groups : {};
  const q = String(opts.query || "").trim().toLowerCase();
  const cat = opts.category || "all";
  const out: MemGroups = {};
  for (const [c, items] of Object.entries(g)) {
    if (cat !== "all" && c !== cat) continue;
    const kept = (Array.isArray(items) ? items : []).filter((m) => {
      if (!q) return true;
      return `${m.key || ""} ${m.value || ""}`.toLowerCase().includes(q);
    });
    if (kept.length) out[c] = kept;
  }
  return out;
}

/** 组内排序（不改原数组）。confidence：高→低；recent：最近使用新→旧；default：原序。 */
export function sortGroupItems(groups: MemGroups, by: MemSort): MemGroups {
  const g = groups && typeof groups === "object" ? groups : {};
  const out: MemGroups = {};
  for (const [c, items] of Object.entries(g)) {
    const arr = (Array.isArray(items) ? items : []).slice();
    if (by === "confidence") {
      arr.sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0));
    } else if (by === "recent") {
      arr.sort((a, b) => (b.last_used || 0) - (a.last_used || 0));
    }
    out[c] = arr;
  }
  return out;
}

export interface MemStats { total: number; avgConfidence: number; categories: number; }

/** 汇总：总条数、平均置信度(%)、类别数。 */
export function memoryStats(groups: MemGroups): MemStats {
  const g = groups && typeof groups === "object" ? groups : {};
  let total = 0, confSum = 0;
  const cats = Object.keys(g);
  for (const items of Object.values(g)) {
    for (const m of (Array.isArray(items) ? items : [])) { total++; confSum += (m.confidence ?? 0.8); }
  }
  return { total, avgConfidence: total ? Math.round((confSum / total) * 100) : 0, categories: cats.length };
}

/** 类别名列表（按条数降序，便于做筛选 tab）。 */
export function memoryCategories(groups: MemGroups): string[] {
  const g = groups && typeof groups === "object" ? groups : {};
  return Object.entries(g)
    .sort((a, b) => (b[1]?.length || 0) - (a[1]?.length || 0))
    .map(([c]) => c);
}

/** 组内条数合计（用于显示筛选后的总数）。 */
export function countItems(groups: MemGroups): number {
  const g = groups && typeof groups === "object" ? groups : {};
  return Object.values(g).reduce((s, items) => s + (Array.isArray(items) ? items.length : 0), 0);
}
