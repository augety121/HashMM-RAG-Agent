// lib/runStats.ts — Agent 运行轨迹：筛选 / 汇总纯逻辑（V103.90，frontend-next 真 UI）。
// 给运行轨迹面板加大厂级深度（按状态/停止理由/关键词过滤、总览指标）。
// 纯函数，便于 tsc 编译后单测。

export interface RunUsage { total_tokens?: number; prompt_tokens?: number; completion_tokens?: number; }
export interface RunRec {
  ts?: string; query?: string; status?: string; elapsed_s?: number;
  iterations?: number; stop_reason?: string; events?: unknown[]; usage?: RunUsage;
}

export type RunStatusFilter = "all" | "ok" | "failed";

/** 单条运行的 token 数（优先 total，否则 prompt+completion）。 */
export function runTokens(r: RunRec): number {
  const u = r.usage || {};
  if (typeof u.total_tokens === "number") return u.total_tokens;
  return (u.prompt_tokens || 0) + (u.completion_tokens || 0);
}

/** 是否失败（status 存在且不是 done）。 */
export function runFailed(r: RunRec): boolean {
  return !!r.status && r.status !== "done";
}

/** 按状态/停止理由/关键词（查询文本，大小写不敏感）过滤。 */
export function filterRuns(runs: RunRec[], opts: { query?: string; status?: RunStatusFilter; stopReason?: string } = {}): RunRec[] {
  const arr = Array.isArray(runs) ? runs : [];
  const q = String(opts.query || "").trim().toLowerCase();
  const status = opts.status || "all";
  const sr = opts.stopReason || "all";
  return arr.filter((r) => {
    if (status === "ok" && runFailed(r)) return false;
    if (status === "failed" && !runFailed(r)) return false;
    if (sr !== "all" && (r.stop_reason || "") !== sr) return false;
    if (q && !String(r.query || "").toLowerCase().includes(q)) return false;
    return true;
  });
}

export interface RunSummary {
  total: number; ok: number; failed: number; successRate: number;
  avgIterations: number; totalTokens: number; avgElapsed: number;
}

/** 总览：总数、成功/失败、成功率(%)、平均轮数、总 token、平均耗时(s)。 */
export function runSummary(runs: RunRec[]): RunSummary {
  const arr = Array.isArray(runs) ? runs : [];
  const total = arr.length;
  let ok = 0, failed = 0, iterSum = 0, iterN = 0, tokSum = 0, elSum = 0, elN = 0;
  for (const r of arr) {
    if (runFailed(r)) failed++; else ok++;
    if (typeof r.iterations === "number") { iterSum += r.iterations; iterN++; }
    tokSum += runTokens(r);
    if (typeof r.elapsed_s === "number") { elSum += r.elapsed_s; elN++; }
  }
  return {
    total, ok, failed,
    successRate: total ? Math.round((ok / total) * 100) : 0,
    avgIterations: iterN ? Math.round((iterSum / iterN) * 10) / 10 : 0,
    totalTokens: tokSum,
    avgElapsed: elN ? Math.round((elSum / elN) * 10) / 10 : 0,
  };
}

/** 出现过的停止理由（去重排序），供筛选下拉。 */
export function distinctStopReasons(runs: RunRec[]): string[] {
  const set = new Set<string>();
  for (const r of (Array.isArray(runs) ? runs : [])) if (r.stop_reason) set.add(r.stop_reason);
  return [...set].sort();
}
