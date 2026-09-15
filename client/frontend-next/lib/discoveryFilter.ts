// lib/discoveryFilter.ts — 主动发现：优先级筛选 / 分级统计 / 排序纯逻辑（V103.90，frontend-next 真 UI）。
// 给主动发现面板加大厂级深度（按优先级过滤、分级计数、高优先级置顶）。
// 纯函数，便于 tsc 编译后单测。

export interface Finding { kind?: string; priority?: string; title?: string; detail?: string; action?: string; action_kind?: string; }

export type PriorityFilter = "all" | "high" | "medium" | "low";

/** 归一优先级（缺省/未知按 low）。 */
export function normPriority(p?: string): "high" | "medium" | "low" {
  if (p === "high") return "high";
  if (p === "medium") return "medium";
  return "low";
}

/** 按优先级过滤。 */
export function filterFindings(findings: Finding[], priority: PriorityFilter = "all"): Finding[] {
  const arr = Array.isArray(findings) ? findings : [];
  if (priority === "all") return arr.slice();
  return arr.filter((f) => normPriority(f.priority) === priority);
}

export interface PriorityCounts { total: number; high: number; medium: number; low: number; }

/** 分级计数。 */
export function priorityCounts(findings: Finding[]): PriorityCounts {
  const arr = Array.isArray(findings) ? findings : [];
  let high = 0, medium = 0, low = 0;
  for (const f of arr) {
    const p = normPriority(f.priority);
    if (p === "high") high++;
    else if (p === "medium") medium++;
    else low++;
  }
  return { total: arr.length, high, medium, low };
}

const RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

/** 高优先级置顶（稳定排序，不改原数组）。 */
export function sortByPriority(findings: Finding[]): Finding[] {
  return (Array.isArray(findings) ? findings : [])
    .map((f, i) => [f, i] as [Finding, number])
    .sort((a, b) => (RANK[normPriority(a[0].priority)] - RANK[normPriority(b[0].priority)]) || (a[1] - b[1]))
    .map((x) => x[0]);
}

/** 是否有可一键执行的动作（与面板 actionable 口径一致）。 */
export function isActionable(kind?: string): boolean {
  return kind === "build_communities" || kind === "create_digest" || kind === "goto_kb";
}

/** 可一键处理的发现条数。 */
export function actionableCount(findings: Finding[]): number {
  return (Array.isArray(findings) ? findings : []).filter((f) => f.action && isActionable(f.action_kind)).length;
}
