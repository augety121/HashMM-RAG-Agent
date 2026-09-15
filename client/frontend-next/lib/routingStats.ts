// lib/routingStats.ts — 模型路由：分布统计 / 筛选 / 批量纯逻辑（V103.90，frontend-next 真 UI）。
// 给模型路由面板加大厂级深度（本地/云端/自动分布汇总、按配置过滤、批量设置）。
// 纯函数，便于 tsc 编译后单测。

export interface RouteTask { task: string; label: string; setting: string; effective: string; default: string; }
export type Setting = "local" | "cloud" | "auto";
export type RouteFilter = "all" | "local" | "cloud" | "auto";

/** 当前设置（取编辑态，缺省 auto）。 */
export function currentSetting(task: RouteTask, edits: Record<string, Setting>): Setting {
  return edits[task.task] || (task.setting as Setting) || "auto";
}

/** 实际生效后端（auto 落到 default，其余按设置）。 */
export function effectiveBackend(task: RouteTask, edits: Record<string, Setting>): "local" | "cloud" {
  const cur = currentSetting(task, edits);
  const eff = cur === "auto" ? task.default : cur;
  return eff === "local" ? "local" : "cloud";
}

export interface RoutingCounts { total: number; local: number; cloud: number; autoCount: number; }

/** 分布统计：实际走本地/云端的任务数，以及设为自动的任务数。 */
export function routingCounts(tasks: RouteTask[], edits: Record<string, Setting>): RoutingCounts {
  const arr = Array.isArray(tasks) ? tasks : [];
  let local = 0, cloud = 0, autoCount = 0;
  for (const t of arr) {
    if (currentSetting(t, edits) === "auto") autoCount++;
    if (effectiveBackend(t, edits) === "local") local++; else cloud++;
  }
  return { total: arr.length, local, cloud, autoCount };
}

/** 按设置过滤（all/local/cloud/auto，按当前编辑态判断）。 */
export function filterTasks(tasks: RouteTask[], edits: Record<string, Setting>, filter: RouteFilter): RouteTask[] {
  const arr = Array.isArray(tasks) ? tasks : [];
  if (filter === "all") return arr.slice();
  return arr.filter((t) => currentSetting(t, edits) === filter);
}

/** 批量设置：把所有任务设为同一值，返回新的 edits（不改原对象）。 */
export function setAll(tasks: RouteTask[], value: Setting): Record<string, Setting> {
  const out: Record<string, Setting> = {};
  for (const t of (Array.isArray(tasks) ? tasks : [])) out[t.task] = value;
  return out;
}

/** 相对默认是否有改动（用于「保存」按钮状态判断的辅助）。 */
export function hasOverride(tasks: RouteTask[], edits: Record<string, Setting>): boolean {
  return (Array.isArray(tasks) ? tasks : []).some((t) => {
    const cur = currentSetting(t, edits);
    return cur === "local" || cur === "cloud";
  });
}
