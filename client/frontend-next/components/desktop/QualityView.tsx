"use client";
/** components/desktop/QualityView.tsx — 质量看板（V103.52 改用统一设计系统 PanelKit）。
 *
 * 数据来自后端可观测聚合 (/api/metrics/dashboard) + KG 规模 (/api/kg/stats)：延迟 P50/P95、
 * 每查询成本、检索质量、SLO、云/本地路由占比、错误率。无流量时多为「—」。
 * 「一键改善」让看板从只读变为可动手（建社区检索 / 重建索引）。
 * 视觉全部走 PanelKit（StatCard / PageHeader / Card / Badge / StateView），与其它面板统一。
 */
import { useEffect, useState, useCallback } from "react";
import { Activity, RefreshCw, CheckCircle2, AlertTriangle, XCircle, Wrench, Gauge, DollarSign, Target, Cloud, Bug, Network } from "lucide-react";
import { metricsDashboard, kgStats, rebuildCommunities, rebuildIndex } from "@/lib/api";
import { PanelShell, PageHeader, Card, CardHeader, StatCard, CardGrid, Button, Badge, StateView, SectionTitle } from "./ui/PanelKit";

const ms = (v: any) => (typeof v === "number" ? Math.round(v) : "—");
const usd = (v: any) => (typeof v === "number" ? `$${v < 0.01 ? v.toFixed(5) : v.toFixed(4)}` : "—");
const pct = (v: any) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—");
const num = (v: any) => (typeof v === "number" ? v.toLocaleString() : "—");
const score = (v: any) => (typeof v === "number" ? v.toFixed(3) : "—");

export function QualityView() {
  const [d, setD] = useState<any>(undefined);
  const [kg, setKg] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [qa, setQa] = useState<Record<string, { status: "running" | "done" | "error"; msg?: string }>>({});

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [dash, k] = await Promise.all([metricsDashboard().catch(() => null), kgStats().catch(() => null)]);
      setD(dash || {}); setKg(k || null);
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const doBuildCommunities = async () => {
    setQa(p => ({ ...p, comm: { status: "running" } }));
    try {
      const r = await rebuildCommunities();
      if (r && r.ok) { setQa(p => ({ ...p, comm: { status: "done", msg: `已构建 ${r.stats?.communities ?? "?"} 个社区` } })); load(); }
      else setQa(p => ({ ...p, comm: { status: "error", msg: (r && r.message) || "构建失败" } }));
    } catch { setQa(p => ({ ...p, comm: { status: "error", msg: "失败，请重试" } })); }
  };
  const doRebuildIndex = async () => {
    setQa(p => ({ ...p, index: { status: "running" } }));
    try {
      const r = await rebuildIndex();
      if (r && r.ok) setQa(p => ({ ...p, index: { status: "done", msg: "已触发后台重建（稍后生效）" } }));
      else setQa(p => ({ ...p, index: { status: "error", msg: "触发失败" } }));
    } catch { setQa(p => ({ ...p, index: { status: "error", msg: "失败，请重试" } })); }
  };

  const lat = (d && d.latency) || {}, cost = (d && d.cost) || {}, rq = (d && d.retrieval_quality) || {};
  const slo = (d && d.slo) || {}, routing = (d && d.llm_routing) || {}, errors = (d && d.errors) || {};
  const sloOk = slo.ok !== false;
  const breaches: any[] = Array.isArray(slo.breaches) ? slo.breaches : [];
  const SLO_NAMES: Record<string, string> = { total_latency_p95_ms: "P95 延迟", total_latency_p50_ms: "P50 延迟", retrieve_latency_p95_ms: "检索延迟 P95", cost_per_query_p95_usd: "每查询成本 P95", cost_per_query_p50_usd: "每查询成本 P50", insufficient_rate: "资料不足率" };
  const breachLabel = (b: any): string => {
    if (typeof b === "string") return b;
    if (b && typeof b === "object") {
      const name = SLO_NAMES[b.slo] || b.slo || "指标";
      return (b.actual != null && b.target != null) ? `${name}（${b.actual} > ${b.target}）` : name;
    }
    return String(b);
  };
  const communities = kg?.communities ?? kg?.num_communities;
  const entities = kg?.entities ?? kg?.num_entities;
  const insuf = rq.insufficient_rate;
  const needComm = typeof communities === "number" && communities === 0 && typeof entities === "number" && entities >= 5;
  const needIndex = typeof insuf === "number" && insuf >= 0.3;

  const QAResult = ({ s }: { s?: { status: string; msg?: string } }) => {
    if (!s) return null;
    if (s.status === "done") return <span className="inline-flex items-center gap-1 text-[11.5px]" style={{ color: "var(--success)" }}><CheckCircle2 size={13} /> {s.msg}</span>;
    if (s.status === "error") return <span className="inline-flex items-center gap-1 text-[11.5px]" style={{ color: "var(--error)" }}><XCircle size={13} /> {s.msg}</span>;
    return null;
  };

  return (
    <PanelShell>
      <PageHeader icon={Activity} title="质量看板"
        subtitle="延迟 · 成本 · 检索质量 · SLO · 路由省钱 —— 来自后端可观测聚合，无流量时多为「—」"
        actions={<Button icon={RefreshCw} busy={busy} onClick={load} size="sm">刷新</Button>} />

      {d === undefined ? <StateView kind="loading" message="读取看板中…" /> : (<>
        <div className="flex items-center gap-2 mb-5 px-4 py-3 rounded-2xl text-[12.5px] font-medium"
          style={{ background: sloOk ? "color-mix(in srgb, var(--success) 10%, transparent)" : "color-mix(in srgb, var(--error) 10%, transparent)", color: sloOk ? "var(--success)" : "var(--error)" }}>
          {sloOk ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          {sloOk ? "SLO 正常 —— 当前无违约" : `SLO 违约 ${breaches.length} 项：${breaches.slice(0, 3).map(breachLabel).join("、") || "见详情"}`}
        </div>

        <SectionTitle>核心指标</SectionTitle>
        <CardGrid min={170}>
          <StatCard label="P50 延迟" value={ms(lat.p50_ms)} unit="ms" icon={Gauge} />
          <StatCard label="P95 延迟" value={ms(lat.p95_ms)} unit="ms" icon={Gauge} tone={typeof lat.p95_ms === "number" && lat.p95_ms > 8000 ? "warning" : "default"} />
          <StatCard label="每查询成本 P50" value={usd(cost.per_query_p50_usd)} icon={DollarSign} />
          <StatCard label="检索 top 分" value={score(rq.top_score_avg)} icon={Target} tone="accent" />
          <StatCard label="本地路由占比" value={pct(routing.local_ratio)} icon={Cloud} tone="success" hint="越高越省钱" />
          <StatCard label="累计错误" value={num(errors.total)} icon={Bug} tone={typeof errors.total === "number" && errors.total > 0 ? "error" : "default"} />
        </CardGrid>

        <SectionTitle right={<span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>从指标直接动手，不只是看</span>}>一键改善</SectionTitle>
        <Card>
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2.5 flex-wrap">
              <Button variant={needComm ? "primary" : "secondary"} icon={Wrench} busy={qa.comm?.status === "running"} onClick={doBuildCommunities} size="sm">构建社区检索</Button>
              {needComm && <Badge tone="warning">{num(entities)} 实体但 0 社区，构建后开启全局检索</Badge>}
              {qa.comm?.status === "running" && <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>较重，可能几分钟，勿关闭</span>}
              <QAResult s={qa.comm} />
            </div>
            <div className="flex items-center gap-2.5 flex-wrap">
              <Button variant={needIndex ? "primary" : "secondary"} icon={RefreshCw} busy={qa.index?.status === "running"} onClick={doRebuildIndex} size="sm">重建检索索引</Button>
              {needIndex && <Badge tone="warning">资料不足率 {pct(insuf)} 偏高，重建索引或补充资料</Badge>}
              <QAResult s={qa.index} />
            </div>
          </div>
        </Card>

        <SectionTitle>明细</SectionTitle>
        <CardGrid min={280}>
          <Card>
            <CardHeader title="延迟" icon={Gauge} />
            <Detail label="P50（总）" val={`${ms(lat.p50_ms)} ms`} />
            <Detail label="P95（总）" val={`${ms(lat.p95_ms)} ms`} />
            <Detail label="检索 P95" val={`${ms(lat.retrieve_p95_ms)} ms`} />
            <Detail label="请求数" val={num(lat.n_requests)} last />
          </Card>
          <Card>
            <CardHeader title="成本" icon={DollarSign} />
            <Detail label="每查询 P50" val={usd(cost.per_query_p50_usd)} />
            <Detail label="每查询 P95" val={usd(cost.per_query_p95_usd)} />
            <Detail label="累计" val={usd(cost.total_usd)} last />
          </Card>
          <Card>
            <CardHeader title="云/本地路由" icon={Cloud} />
            <Detail label="本地调用" val={num(routing.local)} />
            <Detail label="云端调用" val={num(routing.cloud)} />
            <Detail label="本地占比" val={pct(routing.local_ratio)} accent last />
          </Card>
          <Card>
            <CardHeader title="知识图谱规模" icon={Network} />
            <Detail label="实体" val={num(entities)} />
            <Detail label="关系" val={num(kg?.relations ?? kg?.num_relations)} />
            <Detail label="社区" val={num(communities)} accent={communities === 0} last />
          </Card>
        </CardGrid>
      </>)}
    </PanelShell>
  );
}

function Detail({ label, val, accent, last }: { label: string; val: string; accent?: boolean; last?: boolean }) {
  return (
    <div className="flex justify-between items-baseline py-2" style={last ? undefined : { borderBottom: "1px dashed var(--border)" }}>
      <span className="text-[11.5px]" style={{ color: "var(--text-secondary)" }}>{label}</span>
      <span className="text-[13px] font-mono font-semibold" style={{ color: accent ? "var(--accent)" : "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{val}</span>
    </div>
  );
}
