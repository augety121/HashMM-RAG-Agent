"use client";
/** components/desktop/AuditView.tsx — 权限审计（V103.90 深做：筛选/搜索/统计/展开详情/CSV 导出）。
 *
 * 后端 tool_governance 的「工具调用审计流 + deny-first 治理」入口（管理员可见）。
 * V103.90 升级到大厂审计台深度：按风险/状态/关键词过滤、汇总指标（成功率·平均耗时）、
 * 逐条展开看完整入参与上下文、一键导出 CSV。
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { ShieldCheck, RefreshCw, CheckCircle2, XCircle, Lock, Download, Search, ChevronRight } from "lucide-react";
import { toolAudit, governanceStatus } from "@/lib/api";
import { PanelShell, PageHeader, Card, StatCard, CardGrid, Button, Badge, StateView, SectionTitle, inputClass, inputStyle } from "./ui/PanelKit";
import { filterAuditEntries, auditStats, auditToCsv, type AuditEntry, type RiskFilter, type StatusFilter } from "@/lib/auditFilter";

export function AuditView() {
  const [gov, setGov] = useState<Record<string, unknown> | undefined>(undefined);
  const [entries, setEntries] = useState<AuditEntry[] | undefined>(undefined);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  // V103.90 筛选 + 展开
  const [risk, setRisk] = useState<RiskFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [g, a] = await Promise.all([governanceStatus().catch(() => null), toolAudit(200).catch(() => null)]);
      if (g === null && a === null) { setDenied(true); setGov({}); setEntries([]); }
      else { setDenied(false); setGov((g as Record<string, unknown>) || {}); setEntries(a && Array.isArray(a.entries) ? a.entries : []); }
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const g = gov || {};
  const counters = (g.governance as Record<string, number>) || {};
  const fmtTime = (ts?: number) => (typeof ts === "number" ? new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false }) : "");
  function fmtArgs(args: unknown): string {
    if (args == null) return "（无入参）";
    try { return JSON.stringify(args, null, 2); } catch (_e) { return String(args); }
  }

  const filtered = useMemo(() => filterAuditEntries(entries || [], { risk, status, query }), [entries, risk, status, query]);
  const stats = useMemo(() => auditStats(filtered), [filtered]);

  function exportCsv() {
    try {
      const blob = new Blob([auditToCsv(filtered)], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `audit-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch { /* 导出失败静默 */ }
  }

  const Seg = ({ value, onSel, opts }: { value: string; onSel: (v: string) => void; opts: { v: string; label: string }[] }) => (
    <div className="inline-flex rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
      {opts.map((o, i) => (
        <button key={o.v} onClick={() => onSel(o.v)}
          className="px-2.5 py-1 text-[11px] transition-colors"
          style={{ background: value === o.v ? "var(--accent-light)" : "transparent", color: value === o.v ? "var(--accent)" : "var(--text-tertiary)", borderLeft: i ? "1px solid var(--border)" : "none" }}>
          {o.label}
        </button>
      ))}
    </div>
  );

  return (
    <PanelShell>
      <PageHeader icon={ShieldCheck} title="权限审计"
        subtitle="工具调用审计流 + deny-first 治理 —— 合规可追溯「谁 · 调了什么 · 是否高危 · 是否成功」"
        actions={<Button icon={RefreshCw} busy={busy} size="sm" onClick={load}>刷新</Button>} />

      {denied && <StateView kind="empty" icon={Lock} message="此面板需要管理员权限，或后端未连接。" />}

      {!denied && (<>
        <div className="flex items-center gap-2 flex-wrap mb-4">
          <Badge tone={g.audit_enabled === true ? "success" : "neutral"}>审计 {g.audit_enabled === true ? "开" : "关"}</Badge>
          <Badge tone={g.approval_enabled === true ? "success" : "neutral"}>人工审批 {g.approval_enabled === true ? "开" : "关"}</Badge>
        </div>
        <CardGrid min={150}>
          <StatCard label="已批准" value={counters.approval_granted ?? 0} tone="success" />
          <StatCard label="已拒绝" value={counters.approval_denied ?? 0} tone="error" />
          <StatCard label="高危调用" value={counters.high_risk ?? 0} tone="warning" />
          <StatCard label="成功率" value={stats.successRate} unit="%" hint={`${stats.ok}/${stats.total} 成功`} />
          <StatCard label="平均耗时" value={stats.avgLatency} unit="ms" hint="当前筛选范围" />
        </CardGrid>

        <SectionTitle right={<Badge tone="neutral" mono>{filtered.length}/{entries?.length ?? 0}</Badge>}>最近工具调用</SectionTitle>

        {/* V103.90 筛选条 */}
        <div className="flex items-center gap-2 flex-wrap mb-3">
          <div className="relative flex-1 min-w-[160px]">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索工具名 / 执行者 / 租户…"
              className={inputClass} style={{ ...inputStyle, paddingLeft: 30 }} />
          </div>
          <Seg value={risk} onSel={(v) => setRisk(v as RiskFilter)} opts={[{ v: "all", label: "全部风险" }, { v: "high", label: "高危" }, { v: "normal", label: "普通" }]} />
          <Seg value={status} onSel={(v) => setStatus(v as StatusFilter)} opts={[{ v: "all", label: "全部状态" }, { v: "ok", label: "成功" }, { v: "failed", label: "失败" }]} />
          <Button icon={Download} size="sm" onClick={exportCsv} disabled={!filtered.length}>导出 CSV</Button>
        </div>

        {entries === undefined && <StateView kind="loading" />}
        {entries !== undefined && filtered.length === 0 && <StateView kind="empty" icon={ShieldCheck} message={entries.length ? "没有符合筛选条件的记录。" : "暂无审计记录（审计未开启或还没有工具调用）。"} />}
        {filtered.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {filtered.map((e, i) => {
              const open = expanded === i;
              const riskColor = e.risk === "high" ? "var(--error)" : "var(--text-secondary)";
              const okColor = e.ok === false ? "var(--error)" : "var(--success)";
              const okText = e.ok === false ? "失败" : "成功";
              const riskText = e.risk || "normal";
              const actorText = e.actor || "system";
              return (
                <Card key={i} padding="px-3 py-2.5" interactive onClick={() => setExpanded(open ? null : i)}>
                  <div className="flex items-center gap-2.5">
                    <ChevronRight size={13} className="flex-shrink-0 transition-transform" style={{ color: "var(--text-tertiary)", transform: open ? "rotate(90deg)" : "none" }} />
                    {e.ok === false ? <XCircle size={14} style={{ color: "var(--error)" }} className="flex-shrink-0" /> : <CheckCircle2 size={14} style={{ color: "var(--success)" }} className="flex-shrink-0" />}
                    <span className="text-[12.5px] font-mono font-medium" style={{ color: "var(--text-primary)" }}>{e.tool || "?"}</span>
                    {e.risk === "high" && <Badge tone="error">高危</Badge>}
                    <span className="text-[11px] truncate" style={{ color: "var(--text-tertiary)" }}>{e.actor || "system"}</span>
                    <span className="ml-auto text-[10px] font-mono flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>{typeof e.latency_ms === "number" ? e.latency_ms + "ms" : ""}</span>
                    <span className="text-[10px] flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>{fmtTime(e.ts)}</span>
                  </div>
                  {open && (
                    <div className="mt-2.5 pt-2.5 grid grid-cols-1 gap-1.5" style={{ borderTop: "1px solid var(--border)" }}>
                      <div className="flex gap-4 flex-wrap text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                        <span>{"执行者："}<span style={{ color: "var(--text-secondary)" }}>{actorText}</span></span>
                        {e.tenant ? <span>{"租户："}<span style={{ color: "var(--text-secondary)" }}>{e.tenant}</span></span> : null}
                        <span>{"风险："}<span style={{ color: riskColor }}>{riskText}</span></span>
                        <span>{"结果："}<span style={{ color: okColor }}>{okText}</span></span>
                      </div>
                      <div className="text-[10px] mb-0.5" style={{ color: "var(--text-tertiary)" }}>入参</div>
                      <pre className="text-[11px] font-mono p-2.5 rounded-lg overflow-x-auto whitespace-pre-wrap" style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)", maxHeight: 220 }}>
                        {fmtArgs(e.args)}
                      </pre>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </>)}
    </PanelShell>
  );
}
