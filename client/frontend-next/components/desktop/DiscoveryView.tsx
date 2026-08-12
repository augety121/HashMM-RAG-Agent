"use client";
/** components/desktop/DiscoveryView.tsx — 主动发现（V203 重构）。
 *
 * "Agent 自己找活干"的收件箱。布局对标大厂告警/巡检中心三段式：
 * 扫描状态条 → 分级概览 → 按优先级分组的事项流（左色轨标级别）。
 * 无权限/未连接的空态给**可点的出路**，不再是一句冷冰冰的提示。
 * 功能不变：build_communities / create_digest / goto_kb 三类一键处理。
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { RefreshCw, ChevronRight, CheckCircle2, XCircle, Play, Plug, ShieldCheck, Radar, Zap, AlertTriangle } from "lucide-react";
import { runDiscovery, rebuildCommunities, createScheduledTask } from "@/lib/api";
import { useStore } from "@/lib/store";
import { PanelShell, PageHeader, Card, Button, Badge, StateView } from "./ui/PanelKit";
import { filterFindings, priorityCounts, sortByPriority, actionableCount, type Finding as FindingRec, type PriorityFilter } from "@/lib/discoveryFilter";

type Finding = { kind?: string; priority?: string; title?: string; detail?: string; action?: string; action_kind?: string };
type ActState = { status: "running" | "done" | "error"; msg?: string };

const PRIO_META: Record<string, { label: string; color: string; desc: string }> = {
  high: { label: "高优先", color: "var(--error)", desc: "建议尽快处理" },
  medium: { label: "中优先", color: "var(--warning)", desc: "近期安排" },
  low: { label: "低优先", color: "var(--text-tertiary)", desc: "有空再看" },
};

export function DiscoveryView() {
  const [data, setData] = useState<any>(undefined);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [acts, setActs] = useState<Record<string, ActState>>({});
  const [prio, setPrio] = useState<PriorityFilter>("all");
  const set = useStore((s) => s.set);
  const user = useStore((s) => s.user);

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
  const counts = useMemo(() => priorityCounts(findings as FindingRec[]), [findings]);
  const nActionable = useMemo(() => actionableCount(findings as FindingRec[]), [findings]);
  const shown = useMemo(() => sortByPriority(filterFindings(findings as FindingRec[], prio)) as Finding[], [findings, prio]);
  // 按优先级分段渲染（选了具体级别时只有一段）
  const sections = useMemo(() => {
    const by: Record<string, Finding[]> = { high: [], medium: [], low: [] };
    for (const f of shown) (by[(f.priority || "low") as keyof typeof by] ||= []).push(f);
    return (["high", "medium", "low"] as const).filter(k => by[k].length > 0).map(k => ({ k, items: by[k] }));
  }, [shown]);
  const isAdmin = user?.role === "admin";

  return (
    <PanelShell>
      <PageHeader icon={Radar} title="主动发现"
        subtitle="Agent 主动巡检，找出「该做的活」—— 每条都能一键交给它处理，发现即闭环"
        actions={<Button variant="primary" icon={RefreshCw} busy={busy} size="sm" onClick={load}>{busy ? "扫描中" : "重新扫描"}</Button>} />

      {/* ── 无权限 / 未连接：给出路，不甩脸 ── */}
      {denied && (
        <StateView kind="empty" icon={isAdmin ? Plug : ShieldCheck}
          title={isAdmin ? "后端未连接" : "需要管理员权限"}
          message={isAdmin
            ? "主动发现由后端巡检产生。请先在「后端连接」里连上你的 HashMM 后端，再回来重新扫描。"
            : "主动发现会检查全局知识库与任务状态，属于管理动作。请使用管理员账号登录后再试。"}
          action={isAdmin ? (
            <Button variant="primary" icon={Plug} size="sm" onClick={() => set({ adminOpen: true, adminTab: "backend" as never, desktopView: null })}>去连接后端</Button>
          ) : (
            <Button variant="secondary" icon={RefreshCw} size="sm" onClick={load}>重试</Button>
          )} />
      )}

      {!denied && data !== undefined && (<>
        {/* ── 扫描状态条 + 分级概览 ── */}
        {findings.length > 0 && (
          <Card padding="px-4 py-3" className="mb-4">
            <div className="flex items-center gap-3 flex-wrap">
              <span className="inline-flex items-center gap-1.5 text-[12px]" style={{ color: "var(--text-secondary)" }}>
                <Zap size={13} style={{ color: "var(--accent)" }} />
                发现 <b className="font-mono" style={{ color: "var(--text-primary)" }}>{findings.length}</b> 项
                {nActionable > 0 && <>· 可一键处理 <b className="font-mono" style={{ color: "var(--accent)" }}>{nActionable}</b> 项</>}
              </span>
              {d.generated_at && <span className="text-[10.5px] font-mono" style={{ color: "var(--text-tertiary)" }}>扫描于 {d.generated_at}</span>}
              <span className="flex-1" />
              <div className="inline-flex rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
                {([["all", "全部", counts.total], ["high", "高", counts.high], ["medium", "中", counts.medium], ["low", "低", counts.low]] as const).map(([v, label, n], i) => (
                  <button key={v} onClick={() => setPrio(v)} className="px-2.5 py-1 text-[11px] transition-colors"
                    style={{ background: prio === v ? "var(--accent-light)" : "transparent", color: prio === v ? "var(--accent)" : "var(--text-tertiary)", borderLeft: i ? "1px solid var(--border)" : "none" }}>
                    {label} <span className="font-mono">{n}</span>
                  </button>
                ))}
              </div>
            </div>
          </Card>
        )}

        {/* ── 全部处理完的好状态 ── */}
        {findings.length === 0 && !busy && (
          <div className="flex flex-col items-center justify-center py-20 gap-2.5">
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: "color-mix(in srgb, var(--success) 12%, transparent)" }}>
              <CheckCircle2 size={26} style={{ color: "var(--success)" }} />
            </div>
            <div className="text-[13.5px] font-medium" style={{ color: "var(--text-primary)" }}>一切正常</div>
            <div className="text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>Agent 巡检没有发现需要处理的事项，系统状态良好</div>
          </div>
        )}

        {findings.length > 0 && shown.length === 0 && (
          <div className="text-center py-12 text-[12.5px]" style={{ color: "var(--text-tertiary)" }}>该优先级下暂无事项</div>
        )}

        {/* ── 按优先级分段的事项流 ── */}
        {sections.map(({ k, items }) => {
          const meta = PRIO_META[k];
          return (
            <div key={k} className="mb-5">
              <div className="flex items-center gap-2 mb-2.5">
                <span className="w-2 h-2 rounded-full" style={{ background: meta.color }} />
                <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>{meta.label}</span>
                <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{meta.desc}</span>
                <Badge tone="neutral" mono>{items.length}</Badge>
              </div>
              <div className="flex flex-col gap-2.5">
                {items.map((f, i) => {
                  const a = acts[keyOf(f)];
                  return (
                    <Card key={i} padding="p-0">
                      <div className="flex">
                        <div className="w-[3px] rounded-full flex-shrink-0 my-3 ml-3" style={{ background: meta.color, opacity: 0.7 }} />
                        <div className="flex-1 min-w-0 px-4 py-3.5">
                          <div className="text-[13.5px] font-semibold" style={{ color: "var(--text-primary)" }}>{f.title}</div>
                          <div className="text-[12px] leading-relaxed mt-1" style={{ color: "var(--text-secondary)" }}>{f.detail}</div>

                          {f.action && actionable(f.action_kind) && (
                            <div className="flex items-center gap-2 flex-wrap mt-3">
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
                          {f.action && !actionable(f.action_kind) && (
                            <div className="inline-flex items-center gap-1.5 mt-3 text-[11px] px-2.5 py-1 rounded-lg"
                              style={{ background: "var(--surface-2)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                              <AlertTriangle size={11} style={{ color: "var(--text-tertiary)" }} /> 建议：{f.action}
                            </div>
                          )}
                        </div>
                      </div>
                    </Card>
                  );
                })}
              </div>
            </div>
          );
        })}
      </>)}
    </PanelShell>
  );
}
