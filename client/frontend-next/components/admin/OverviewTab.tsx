"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, BrainCircuit, Database, FileClock, Loader2, RefreshCw, Users } from "lucide-react";
import { authHeaders } from "@/lib/api";

type SourceState = { status: "ok" | "unavailable"; latency_ms?: number; issue?: string };
type Overview = {
  schema: string; generated_at: number;
  sources: Record<string, SourceState>;
  models: Array<Record<string, unknown>> | null;
  users: Array<Record<string, unknown>> | null;
  knowledge_bases: Array<Record<string, unknown>> | null;
  evolution_skills: number | null;
  services: { status?: unknown; detail?: unknown } | null;
  recent_logs: number | null;
  jobs: Record<string, number> | null;
};

function Card({ icon: Icon, label, value, note }: { icon: typeof Users; label: string; value: number | null; note: string }) {
  return <div className="rounded-2xl p-4" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
    <div className="flex items-center gap-2 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}><Icon size={13} />{label}</div>
    <div className="mt-2 text-[24px] font-semibold tabular-nums" style={{ color: "var(--text-primary)" }}>{value == null ? "—" : value.toLocaleString()}</div>
    <div className="mt-1 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{note}</div>
  </div>;
}

export function OverviewTab() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const response = await fetch("/api/admin/overview", { headers: authHeaders() });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error((body as { detail?: string }).detail || `读取失败（${response.status}）`);
      setData(body as Overview);
    } catch (cause) { setError((cause as Error)?.message || "管理总览暂时不可用"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const issues = useMemo(() => Object.entries(data?.sources || {}).filter(([, value]) => value.status !== "ok"), [data]);
  const serviceStatus = data?.services?.status;
  const servicesReady = serviceStatus && typeof serviceStatus === "object"
    ? Object.values(serviceStatus as Record<string, unknown>).filter(Boolean).length : null;
  const pendingJobs = data?.jobs
    ? Object.entries(data.jobs).filter(([key]) => !["done", "complete", "completed", "success"].includes(key)).reduce((sum, [, value]) => sum + Number(value || 0), 0)
    : null;

  if (loading && !data) return <div className="py-20 flex items-center justify-center gap-2 text-[12px]" style={{ color: "var(--text-tertiary)" }}><Loader2 size={15} className="animate-spin" />正在核对控制面数据</div>;
  return <div className="space-y-5">
    <div className="flex items-start justify-between gap-4">
      <div><h2 className="text-[15px] font-semibold">平台总览</h2><p className="mt-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>只展示服务端已验证事实；读取失败不会填充为 0。</p></div>
      <button onClick={() => void load()} disabled={loading} className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-[11px] disabled:opacity-50" style={{ border: "1px solid var(--border)" }}><RefreshCw size={12} className={loading ? "animate-spin" : ""} />刷新</button>
    </div>
    {error && <div className="rounded-xl px-3 py-2 flex items-center gap-2 text-[11px]" style={{ color: "var(--error)", background: "color-mix(in srgb, var(--error) 8%, transparent)" }}><AlertTriangle size={13} />{error}；保留最近一次成功结果。</div>}
    {issues.length > 0 && <div className="rounded-xl px-3 py-2 text-[10.5px]" style={{ color: "#92400e", background: "#fef3c7" }}>部分数据不可验证：{issues.map(([name]) => name).join("、")}。对应卡片显示“—”。</div>}
    <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
      <Card icon={Users} label="成员" value={data?.users?.length ?? null} note="身份目录" />
      <Card icon={BrainCircuit} label="模型" value={data?.models?.length ?? null} note="平台模型配置" />
      <Card icon={Database} label="知识库" value={data?.knowledge_bases?.length ?? null} note="RAG 数据边界" />
      <Card icon={FileClock} label="审计事件" value={data?.recent_logs ?? null} note="服务端审计账本" />
    </div>
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      <Card icon={Activity} label="就绪服务" value={servicesReady} note="当前服务注册表快照" />
      <Card icon={FileClock} label="未终态任务" value={pendingJobs} note="后台任务状态聚合" />
      <Card icon={BrainCircuit} label="演化技能" value={data?.evolution_skills ?? null} note="含待复核候选" />
    </div>
    {data?.generated_at && <div className="text-right text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>数据时间 {new Date(data.generated_at * 1000).toLocaleString()} · {Object.values(data.sources).filter(item => item.status === "ok").length}/{Object.keys(data.sources).length} 数据源已验证</div>}
  </div>;
}

