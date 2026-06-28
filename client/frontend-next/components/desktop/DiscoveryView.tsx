"use client";
/** components/desktop/DiscoveryView.tsx — 主动发现 · 一键处理（V103.52 改用统一设计系统 PanelKit）。
 *
 * loop 五动作的「发现 → 交付」：Agent 主动扫描找出该做的活，每条都能一键交给后端去做
 * （建社区检索 / 创建每日简报 / 跳到知识库导入），不再是"告诉你但做不了"的摆设。
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { Search, RefreshCw, ChevronRight, CheckCircle2, XCircle, Loader2, Play } from "lucide-react";
import { runDiscovery, rebuildCommunities, createScheduledTask } from "@/lib/api";
import { useStore } from "@/lib/store";
import { PanelShell, PageHeader, Card, Button, Badge, StateView } from "./ui/PanelKit";
import { filterFindings, priorityCounts, sortByPriority, actionableCount, type Finding as FindingRec, type PriorityFilter } from "@/lib/discoveryFilter";

type Finding = { kind?: string; priority?: string; title?: string; detail?: string; action?: string; action_kind?: string };
type ActState = { status: "running" | "done" | "error"; msg?: string };

function prioTone(p: string): { tone: "neutral" | "accent" | "warning"; label: string } {
  if (p === "high") return { tone: "warning", label: "高" };
  if (p === "medium") return { tone: "accent", label: "中" };
  return { tone: "neutral", label: "低" };
}

export function DiscoveryView() {
  const [data, setData] = useState<any>(undefined);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [acts, setActs] = useState<Record<string, ActState>>({});
  const [prio, setPrio] = useState<PriorityFilter>("all");
  const set = useStore((s) => s.set);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const d = await runDiscovery().catch(() => null);
      if (d === null) { setDenied(true); setData({}); }
      else { setDenied(false); setData(d || {}); setActs({}); }
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const keyOf = (f: Finding) => `${f.title || ""}|${f.action_kind || ""}`;
  const act = async (f: Finding) => {
    const kind = f.action_kind || "";
    if (kind === "goto_kb") { set({ adminOpen: true, adminTab: "kbs" as never }); return; }
    const k = keyOf(f);
    setActs(prev => ({ ...prev, [k]: { status: "running" } }));
    try {
      if (kind === "build_communities") {
        const r = await rebuildCommunities();
        if (r && r.ok) { const n = r.stats?.communities ?? r.stats?.community_count ?? "?"; setActs(prev => ({ ...prev, [k]: { status: "done", msg: `已构建 ${n} 个社区，已重新加载图谱` } })); }
        else setActs(prev => ({ ...prev, [k]: { status: "error", msg: (r && r.message) || "构建失败" } }));
      } else if (kind === "create_digest") {
        const r = await createScheduledTask({ action: "corpus_digest", name: "每日语料简报", schedule_kind: "daily", daily_at: "09:00" });
        if (r && r.ok) setActs(prev => ({ ...prev, [k]: { status: "done", msg: "已创建每日简报（每天 09:00），可在「定时任务」查看" } }));
        else setActs(prev => ({ ...prev, [k]: { status: "error", msg: "创建失败" } }));
      } else setActs(prev => ({ ...prev, [k]: { status: "error", msg: "暂不支持的动作" } }));
    } catch { setActs(prev => ({ ...prev, [k]: { status: "error", msg: "操作失败，请重试" } })); }
  };

  const d = data || {};
  const findings: Finding[] = Array.isArray(d.findings) ? d.findings : [];
  const actionable = (k?: string) => k === "build_communities" || k === "create_digest" || k === "goto_kb";
  // V103.90 优先级筛选 + 分级统计
  const counts = useMemo(() => priorityCounts(findings as FindingRec[]), [findings]);
  const nActionable = useMemo(() => actionableCount(findings as FindingRec[]), [findings]);
  const shown = useMemo(() => sortByPriority(filterFindings(findings as FindingRec[], prio)) as Finding[], [findings, prio]);

  return (
    <PanelShell>
      <PageHeader icon={Search} title="主动发现 · 一键处理"
        subtitle="Agent 主动找出「该做的活」，每条都能一键交给它去做 —— 发现 → 处理，闭环"
        actions={<Button variant="primary" icon={RefreshCw} busy={busy} size="sm" onClick={load}>{busy ? "扫描中" : "重新扫描"}</Button>} />

      {denied && <StateView kind="empty" icon={Search} message="此面板需要管理员权限，或后端未连接。" />}

      {!denied && data !== undefined && (<>
        {d.generated_at && <div className="text-[11px] mb-3" style={{ color: "var(--text-tertiary)" }}>扫描于 {d.generated_at} · 发现 {findings.length} 项{nActionable > 0 ? ` · ${nActionable} 项可一键处理` : ""}</div>}

        {/* V103.90 优先级筛选 */}
        {findings.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap mb-3">
            {([["all", "全部", counts.total], ["high", "高", counts.high], ["medium", "中", counts.medium], ["low", "低", counts.low]] as const).map(([v, label, n]) => (
              <button key={v} onClick={() => setPrio(v)} className="px-3 py-1.5 rounded-lg text-[12px] transition-colors"
                style={{ background: prio === v ? "var(--accent-light)" : "var(--bg-secondary)", color: prio === v ? "var(--accent)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>
                {label} <span className="font-mono">{n}</span>
              </button>
            ))}
          </div>
        )}
        {findings.length > 0 && shown.length === 0 && <div className="text-center py-10 text-[13px]" style={{ color: "var(--text-tertiary)" }}>该优先级下暂无事项</div>}

        {findings.length === 0 && !busy && (
          <div className="flex flex-col items-center justify-center py-16 gap-2">
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: "color-mix(in srgb, var(--success) 12%, transparent)" }}>
              <CheckCircle2 size={26} style={{ color: "var(--success)" }} />
            </div>
            <div className="text-[13px]" style={{ color: "var(--text-secondary)" }}>暂无需要主动处理的事项</div>
            <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>系统状态良好，Agent 没发现待办</div>
          </div>
        )}

        {shown.length > 0 && (
          <div className="flex flex-col gap-2.5">
            {shown.map((f, i) => {
              const ps = prioTone(f.priority || "low");
              const a = acts[keyOf(f)];
              return (
                <Card key={i} padding="p-4">
                  <div className="flex items-center gap-2 mb-1.5">
                    <Badge tone={ps.tone}>{ps.label}优先</Badge>
                    <span className="text-[13.5px] font-semibold" style={{ color: "var(--text-primary)" }}>{f.title}</span>
                  </div>
                  <div className="text-[12px] leading-relaxed mb-3" style={{ color: "var(--text-secondary)" }}>{f.detail}</div>

                  {f.action && actionable(f.action_kind) && (
                    <div className="flex items-center gap-2 flex-wrap">
                      <Button variant={a?.status === "done" ? "secondary" : "primary"} size="sm"
                        busy={a?.status === "running"} onClick={() => act(f)}
                        icon={a?.status === "running" ? undefined : (f.action_kind === "goto_kb" ? ChevronRight : Play)}>
                        {f.action}
                      </Button>
                      {f.action_kind === "build_communities" && a?.status === "running" && <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>社区构建较重，可能需要几分钟，请勿关闭</span>}
                      {a?.status === "done" && <span className="inline-flex items-center gap-1 text-[11.5px]" style={{ color: "var(--success)" }}><CheckCircle2 size={13} /> {a.msg}</span>}
                      {a?.status === "error" && <span className="inline-flex items-center gap-1 text-[11.5px]" style={{ color: "var(--error)" }}><XCircle size={13} /> {a.msg}</span>}
                    </div>
                  )}
                  {f.action && !actionable(f.action_kind) && <Badge tone="neutral">建议：{f.action}</Badge>}
                </Card>
              );
            })}
          </div>
        )}
      </>)}
    </PanelShell>
  );
}
