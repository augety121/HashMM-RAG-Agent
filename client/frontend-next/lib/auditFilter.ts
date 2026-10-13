// lib/auditFilter.ts — 工具调用审计：筛选 / 统计 / CSV 导出纯逻辑（V103.90，frontend-next 真 UI）。
// 给权限审计面板加大厂级深度（按风险/状态/关键词过滤、汇总指标、导出 CSV）。
// 纯函数、不碰 DOM/网络，便于 tsc 编译后单测。

export interface AuditEntry {
  ts?: number; actor?: string; tenant?: string; tool?: string;
  risk?: string; ok?: boolean; latency_ms?: number; args?: unknown;
}

export type RiskFilter = "all" | "high" | "normal";
export type StatusFilter = "all" | "ok" | "failed";

export interface AuditFilterOpts { risk?: RiskFilter; status?: StatusFilter; query?: string; }

/** 按风险/状态/关键词（工具名或执行者，大小写不敏感）过滤。 */
export function filterAuditEntries(entries: AuditEntry[], opts: AuditFilterOpts = {}): AuditEntry[] {
  const arr = Array.isArray(entries) ? entries : [];
  const risk = opts.risk || "all";
  const status = opts.status || "all";
  const q = String(opts.query || "").trim().toLowerCase();
  return arr.filter((e) => {
    if (risk === "high" && e.risk !== "high") return false;
    if (risk === "normal" && e.risk === "high") return false;
    if (status === "ok" && e.ok === false) return false;
    if (status === "failed" && e.ok !== false) return false;
    if (q) {
      const hay = `${e.tool || ""} ${e.actor || ""} ${e.tenant || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export interface AuditStats { total: number; ok: number; failed: number; highRisk: number; successRate: number; avgLatency: number; }

/** 汇总指标：总数、成功/失败、高危、成功率(%)、平均耗时(ms)。 */
export function auditStats(entries: AuditEntry[]): AuditStats {
  const arr = Array.isArray(entries) ? entries : [];
  const total = arr.length;
  let ok = 0, failed = 0, highRisk = 0, latSum = 0, latN = 0;
  for (const e of arr) {
    if (e.ok === false) failed++; else ok++;
    if (e.risk === "high") highRisk++;
    if (typeof e.latency_ms === "number") { latSum += e.latency_ms; latN++; }
  }
  return {
    total, ok, failed, highRisk,
    successRate: total ? Math.round((ok / total) * 100) : 0,
    avgLatency: latN ? Math.round(latSum / latN) : 0,
  };
}

function csvCell(v: unknown): string {
  let s = v == null ? "" : (typeof v === "object" ? JSON.stringify(v) : String(v));
  s = s.replace(/\r?\n/g, " ");
  if (/[",]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}

/** 导出 CSV（含表头）。时间转 ISO，便于在 Excel 打开。 */
export function auditToCsv(entries: AuditEntry[]): string {
  const arr = Array.isArray(entries) ? entries : [];
  const head = ["time", "actor", "tenant", "tool", "risk", "ok", "latency_ms", "args"];
  const rows = arr.map((e) => [
    e.ts ? new Date(e.ts * 1000).toISOString() : "",
    e.actor ?? "", e.tenant ?? "", e.tool ?? "", e.risk ?? "",
    e.ok === false ? "false" : "true",
    typeof e.latency_ms === "number" ? e.latency_ms : "",
    e.args ?? "",
  ].map(csvCell).join(","));
  return [head.join(","), ...rows].join("\n");
}

/** 提取出现过的工具名（去重排序），供筛选下拉。 */
export function distinctTools(entries: AuditEntry[]): string[] {
  const set = new Set<string>();
  for (const e of (Array.isArray(entries) ? entries : [])) if (e.tool) set.add(e.tool);
  return [...set].sort();
}
