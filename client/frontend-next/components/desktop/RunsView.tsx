"use client";
/** components/desktop/RunsView.tsx — Agent 运行轨迹（V103.52 改用统一设计系统 PanelKit）。
 *
 * 后端 run_record.py 的运行遥测：每次 Agent 运行的工具序列/状态/停止理由/耗时/用量。
 * 可直观看到预算闸（budget_exceeded）、墙钟截止（deadline）等停止理由。默认关（HASHMM_AGENT_TRACE=1 才落盘）。
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { ScrollText, RefreshCw, CheckCircle2, AlertTriangle, Clock, Play, ChevronRight, Search } from "lucide-react";
import { listMyRuns } from "@/lib/api";
import { useStore } from "@/lib/store";
import { PanelShell, PageHeader, Card, Button, Badge, StateView, StatCard, CardGrid, SectionTitle, inputClass, inputStyle } from "./ui/PanelKit";
import { filterRuns, runSummary, distinctStopReasons, runTokens, type RunStatusFilter, type RunRec } from "@/lib/runStats";

type Run = { ts?: string | number; query?: string; title?: string; status?: string; elapsed_s?: number; elapsed_ms?: number; iterations?: number; stop_reason?: string; events?: any[]; usage?: { total_tokens?: number; prompt_tokens?: number; completion_tokens?: number }; tokens?: number; conv_id?: string; conv_title?: string; run_id?: string; task_type?: string; execution_mode?: string; model?: string; failed_checks?: string[] };

function stopTone(reason: string): { tone: "neutral" | "success" | "warning" | "error"; label: string } {
  const r = reason || "";
  if (r.startsWith("budget_exceeded")) return { tone: "warning", label: "预算用尽" };
  if (r === "deadline") return { tone: "warning", label: "超时收尾" };
  if (r === "max_iterations") return { tone: "neutral", label: "达步数上限" };
  if (r === "done") return { tone: "success", label: "正常完成" };
  return { tone: "neutral", label: r || "—" };
}

export function RunsView() {
  const [data, setData] = useState<any>(undefined);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  // V103.90 筛选
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<RunStatusFilter>("all");
  const [stopReason, setStopReason] = useState("all");
  const set = useStore(st => st.set);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const d = await listMyRuns(50).catch(() => null);
      if (d === null) { setDenied(true); setData({}); }
      else { setDenied(false); setData(d || {}); }
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const d = data || {};
  const runs: Run[] = Array.isArray(d.runs) ? d.runs.map((r: Run) => ({
    ...r,
    query: r.query || r.title,
    elapsed_s: r.elapsed_s ?? (typeof r.elapsed_ms === "number" ? r.elapsed_ms / 1000 : undefined),
    usage: r.usage || (typeof r.tokens === "number" ? { total_tokens: r.tokens } : undefined),
  })) : [];
  const traceOn = d.source === "message_run_manifest" || d.enabled === true;
  // V103.90 筛选 + 汇总
  const stopReasons = useMemo(() => distinctStopReasons(runs as RunRec[]), [runs]);
  const filtered = useMemo(() => filterRuns(runs as RunRec[], { query, status: statusFilter, stopReason }) as Run[], [runs, query, statusFilter, stopReason]);
  const summary = useMemo(() => runSummary(filtered as RunRec[]), [filtered]);

  return (
    <PanelShell>
      <PageHeader icon={ScrollText} title="运行轨迹"
        subtitle="当前账号 Chat 的持久化运行清单 · 停止理由 · 模型 · 耗时 · 用量"
        actions={<Button icon={RefreshCw} busy={busy} size="sm" onClick={load}>刷新</Button>} />

      {denied && <StateView kind="empty" icon={ScrollText} message="当前登录凭据未通过校验，或后端尚未升级到持久化运行清单接口。" />}

      {!denied && data !== undefined && (<>
        <div className="flex items-center gap-2 flex-wrap mb-4">
          <Badge tone={traceOn ? "success" : "neutral"}>{d.source === "message_run_manifest" ? "Chat 清单" : "运行遥测"}</Badge>
          <Badge tone="neutral" mono>共 {runs.length} 条</Badge>
        </div>

        {/* V103.90 汇总指标 + 筛选 */}
        {runs.length > 0 && (<>
          <CardGrid min={150}>
            <StatCard label="运行数" value={summary.total} hint="当前筛选" />
            <StatCard label="成功率" value={summary.successRate} unit="%" hint={`${summary.ok}/${summary.total} 成功`} tone={summary.successRate >= 80 ? "success" : summary.successRate >= 50 ? "warning" : "error"} />
            <StatCard label="平均轮数" value={summary.avgIterations} />
            <StatCard label="总 token" value={summary.totalTokens.toLocaleString()} />
            <StatCard label="平均耗时" value={summary.avgElapsed} unit="s" />
          </CardGrid>
          <div className="flex items-center gap-2 flex-wrap my-3">
            <div className="relative flex-1 min-w-[160px]">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索查询文本…" className={inputClass} style={{ ...inputStyle, paddingLeft: 30 }} />
            </div>
            <div className="inline-flex rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
              {([["all", "全部"], ["ok", "成功"], ["failed", "失败"]] as const).map(([v, label], i) => (
                <button key={v} onClick={() => setStatusFilter(v)} className="px-2.5 py-1 text-[11px] transition-colors"
                  style={{ background: statusFilter === v ? "var(--accent-light)" : "transparent", color: statusFilter === v ? "var(--accent)" : "var(--text-tertiary)", borderLeft: i ? "1px solid var(--border)" : "none" }}>{label}</button>
              ))}
            </div>
            {stopReasons.length > 0 && (
              <select value={stopReason} onChange={e => setStopReason(e.target.value)} className="h-8 px-2 rounded-lg text-[12px] outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                <option value="all">全部停止理由</option>
                {stopReasons.map(sr => <option key={sr} value={sr}>{sr}</option>)}
              </select>
            )}
          </div>
        </>)}

        {runs.length === 0 && <StateView kind="empty" icon={ScrollText} title="还没有持久化运行记录" message="在 Chat 中完成一次问答或长任务后，Assistant 消息携带的 run manifest 会出现在这里；不会用演示数据填充。" />}
        {runs.length > 0 && filtered.length === 0 && <StateView kind="empty" icon={Search} message="没有符合筛选条件的运行记录。" />}

        {filtered.length > 0 && (
          <div className="flex flex-col gap-2">
            {filtered.map((r, i) => {
              const ss = stopTone(r.stop_reason || "");
              const tokens = r.usage?.total_tokens ?? ((r.usage?.prompt_tokens || 0) + (r.usage?.completion_tokens || 0));
              const failed = r.status && r.status !== "done";
              return (
                <Card key={i} padding="p-4">
                  <div className="flex items-start gap-2.5">
                    {failed ? <AlertTriangle size={15} style={{ color: "var(--error)", marginTop: 1 }} /> : <CheckCircle2 size={15} style={{ color: "var(--success)", marginTop: 1 }} />}
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{r.query || "（无查询文本）"}</div>
                      <div className="flex items-center gap-2 flex-wrap mt-1.5 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                        <Badge tone={ss.tone}>{ss.label}</Badge>
                        {typeof r.iterations === "number" && <span>{r.iterations} 轮</span>}
                        {typeof r.elapsed_s === "number" && <span className="inline-flex items-center gap-0.5"><Clock size={10} />{r.elapsed_s}s</span>}
                        {tokens > 0 && <span className="font-mono">{tokens.toLocaleString()} tok</span>}
                        {r.model && <span className="font-mono">{r.model}</span>}
                        {r.execution_mode && <span>{r.execution_mode}</span>}
                        {Array.isArray(r.events) && r.events.length > 0 && <span>{r.events.length} 步</span>}
                        {r.ts && <span className="font-mono">{r.ts}</span>}
                      </div>
                    </div>
                    <button onClick={() => {
                      if (r.conv_id) set({ desktopView: null as never, adminOpen: false, sid: r.conv_id });
                      else if (r.query) set({ desktopView: null as never, adminOpen: false, pendingPrompt: r.query });
                    }} disabled={!r.conv_id && !r.query} title={r.conv_id ? "回到产生这条运行的 Chat" : "把问题填回聊天框"}
                      className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] flex-shrink-0" style={{ color: "var(--text-secondary)" }}><Play size={11} /> {r.conv_id ? "打开 Chat" : "复跑"}</button>
                    {Array.isArray(r.events) && r.events.length > 0 && (
                      <button onClick={() => setExpanded(expanded === i ? null : i)} title="排障：展开看每一步" aria-label="展开运行步骤"
                        className="p-1 rounded transition-colors hover:bg-[var(--bg-tertiary)] flex-shrink-0">
                        <ChevronRight size={14} className="transition-transform" style={{ color: "var(--text-tertiary)", transform: expanded === i ? "rotate(90deg)" : "none" }} />
                      </button>
                    )}
                  </div>
                  {expanded === i && Array.isArray(r.events) && r.events.length > 0 && (
                    <div className="mt-2.5 pt-2.5 flex flex-col gap-1" style={{ borderTop: "1px dashed var(--border)" }}>
                      {r.events.map((ev: any, j: number) => {
                        const label = typeof ev === "string" ? ev : (ev.tool || ev.node || ev.type || "step");
                        const detail = ev && typeof ev === "object" && ev.detail ? ` — ${ev.detail}` : "";
                        const ms = ev && typeof ev === "object" && typeof ev.elapsed_ms === "number" ? ` (${ev.elapsed_ms}ms)` : "";
                        return (
                          <div key={j} className="text-[11px] font-mono flex items-start gap-2" style={{ color: "var(--text-secondary)" }}>
                            <span style={{ color: "var(--text-tertiary)" }}>{j + 1}.</span><span className="flex-1 break-words">{label}{detail}{ms}</span>
                          </div>
                        );
                      })}
                      {r.stop_reason && <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>停止理由：{r.stop_reason}</div>}
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
