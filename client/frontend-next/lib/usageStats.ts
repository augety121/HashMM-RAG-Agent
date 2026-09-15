// lib/usageStats.ts — 用量面板：条形归一化 / 输入输出占比 / 合计纯逻辑（V103.90，frontend-next 真 UI）。
// 把扁平 token 数字做成可视化（周期对比条、输入/输出占比、跨 agent 合计）。纯函数，便于单测。

/** 把一组数值归一为条宽百分比（相对最大值）。非零值至少给 minPct 以便可见；全 0 返回全 0。 */
export function relBars(values: number[], minPct = 3): number[] {
  const arr = (Array.isArray(values) ? values : []).map((v) => (typeof v === "number" && v > 0 ? v : 0));
  const max = Math.max(0, ...arr);
  if (max <= 0) return arr.map(() => 0);
  return arr.map((v) => (v <= 0 ? 0 : Math.max(minPct, Math.round((v / max) * 100))));
}

export interface Split { aPct: number; bPct: number; total: number; }

/** 两数占比（归一到 100，四舍五入后保证和为 100）。总和为 0 时返回 0/0。 */
export function pctSplit(a: number, b: number): Split {
  const av = typeof a === "number" && a > 0 ? a : 0;
  const bv = typeof b === "number" && b > 0 ? b : 0;
  const total = av + bv;
  if (total <= 0) return { aPct: 0, bPct: 0, total: 0 };
  const aPct = Math.round((av / total) * 100);
  return { aPct, bPct: 100 - aPct, total };
}

/** 跨来源 token 合计（忽略非数值）。 */
export function sumTokens(...vals: Array<number | undefined | null>): number {
  return vals.reduce<number>((s, v) => s + (typeof v === "number" && v > 0 ? v : 0), 0);
}

/** 紧凑 token 格式：1234→1.2k，1234567→1.2M。 */
export function fmtCompact(n: number): string {
  const v = typeof n === "number" ? n : 0;
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(1).replace(/\.0$/, "") + "k";
  return String(v);
}
