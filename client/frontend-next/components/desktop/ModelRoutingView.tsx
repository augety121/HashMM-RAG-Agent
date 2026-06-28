"use client";
/** components/desktop/ModelRoutingView.tsx — 模型路由 · 角色→后端（V103.52 改用统一设计系统 PanelKit）。
 *
 * 两层架构：本地 Qwen（省/快/私）vs 云端 DeepSeek（强）。每个角色/任务可配 本地 / 云端 / 自动。
 * 空配置 = 完全回退硬编码默认 = 现状。路由真正生效需 HASHMM_LLM_ROUTING=1 且配了本地模型路径。
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { Network, Save, RefreshCw, HardDrive, Server, Zap, Loader2, CheckCircle2, XCircle } from "lucide-react";
import { getLlmRouting, saveLlmRouting, testLlmRouting } from "@/lib/api";
import { PanelShell, PageHeader, Card, Button, Badge, StateView } from "./ui/PanelKit";
import { routingCounts, filterTasks, setAll as setAllRoutes, type RouteTask, type RouteFilter, type Setting as RSetting } from "@/lib/routingStats";

type Task = { task: string; label: string; setting: string; effective: string; default: string };
type Setting = "local" | "cloud" | "auto";

export function ModelRoutingView() {
  const [cfg, setCfg] = useState<any>(undefined);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [edits, setEdits] = useState<Record<string, Setting>>({});
  const [dirty, setDirty] = useState(false);
  const [testing, setTesting] = useState("");
  const [testRes, setTestRes] = useState<Record<string, any>>({});

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const d = await getLlmRouting().catch(() => null);
      if (d === null) { setDenied(true); setCfg({}); }
      else {
        setDenied(false); setCfg(d || {});
        const e: Record<string, Setting> = {};
        (d?.tasks || []).forEach((t: Task) => { e[t.task] = (t.setting as Setting) || "auto"; });
        setEdits(e); setDirty(false);
      }
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const pick = (task: string, val: Setting) => { setEdits(prev => ({ ...prev, [task]: val })); setDirty(true); };
  // V103.90 筛选 + 批量
  const [routeFilter, setRouteFilter] = useState<RouteFilter>("all");
  const applyAll = (val: RSetting) => { setEdits(setAllRoutes((cfg?.tasks || []) as RouteTask[], val)); setDirty(true); };

  const onTest = async (task: string) => {
    setTesting(task);
    setTestRes(prev => ({ ...prev, [task]: undefined }));
    try { const r = await testLlmRouting(task); setTestRes(prev => ({ ...prev, [task]: r })); }
    catch { setTestRes(prev => ({ ...prev, [task]: { ok: false, message: "测试失败" } })); }
    finally { setTesting(""); }
  };

  const save = async () => {
    setSaving(true);
    try {
      const routing: Record<string, string> = {};
      Object.entries(edits).forEach(([k, v]) => { if (v === "local" || v === "cloud") routing[k] = v; });
      const d = await saveLlmRouting(routing).catch(() => null);
      if (d) {
        setCfg(d);
        const e: Record<string, Setting> = {};
        (d?.tasks || []).forEach((t: Task) => { e[t.task] = (t.setting as Setting) || "auto"; });
        setEdits(e); setDirty(false);
      }
    } finally { setSaving(false); }
  };

  const d = cfg || {};
  const tasks: Task[] = Array.isArray(d.tasks) ? d.tasks : [];
  const routingOn = d.enabled === true;
  // V103.90 分布统计 + 筛选
  const counts = useMemo(() => routingCounts(tasks as RouteTask[], edits as Record<string, RSetting>), [tasks, edits]);
  const shownTasks = useMemo(() => filterTasks(tasks as RouteTask[], edits as Record<string, RSetting>, routeFilter) as Task[], [tasks, edits, routeFilter]);

  const SegBtn = ({ task, val, cur }: { task: string; val: Setting; cur: Setting }) => {
    const active = cur === val;
    const label = val === "local" ? "本地" : val === "cloud" ? "云端" : "自动";
    return (
      <button onClick={() => pick(task, val)} className="px-2.5 py-1 text-[11px] transition-colors"
        style={{ background: active ? "var(--accent)" : "transparent", color: active ? "#fff" : "var(--text-secondary)" }}>{label}</button>
    );
  };

  return (
    <PanelShell>
      <PageHeader icon={Network} title="模型路由 · 角色→后端"
        subtitle="每个角色按需走 本地 Qwen（省/快/私）或 云端 DeepSeek（强）—— 多而便宜的杂活落本地，省成本"
        actions={<>
          {dirty && <Button variant="primary" icon={Save} busy={saving} size="sm" onClick={save}>保存</Button>}
          <Button variant="secondary" icon={RefreshCw} busy={busy} size="sm" onClick={load}>刷新</Button>
        </>} />

      {denied && <StateView kind="empty" icon={Network} message="此面板需要管理员权限，或后端未连接。" />}

      {!denied && cfg !== undefined && (<>
        <div className="flex items-center gap-2 flex-wrap mb-3">
          <Badge tone={routingOn ? "success" : "neutral"}>路由 {routingOn ? "已启用" : "未启用"}</Badge>
          <Badge tone="neutral"><HardDrive size={11} /> 本地模型 {d.local_path_set ? "已配" : "未配"}</Badge>
        </div>

        {/* V103.90 分布统计 + 批量 + 筛选 */}
        {tasks.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap mb-3">
            <div className="inline-flex rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
              {([["all", `全部 ${counts.total}`], ["local", `本地 ${counts.local}`], ["cloud", `云端 ${counts.cloud}`], ["auto", `自动 ${counts.autoCount}`]] as const).map(([v, label], i) => (
                <button key={v} onClick={() => setRouteFilter(v)} className="px-2.5 py-1 text-[11px] transition-colors"
                  style={{ background: routeFilter === v ? "var(--accent-light)" : "transparent", color: routeFilter === v ? "var(--accent)" : "var(--text-tertiary)", borderLeft: i ? "1px solid var(--border)" : "none" }}>{label}</button>
              ))}
            </div>
            <span className="text-[10.5px] ml-1" style={{ color: "var(--text-tertiary)" }}>批量：</span>
            <button onClick={() => applyAll("local")} className="text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>全部本地</button>
            <button onClick={() => applyAll("cloud")} className="text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>全部云端</button>
            <button onClick={() => applyAll("auto")} className="text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>全部自动</button>
          </div>
        )}

        {!routingOn && (
          <div className="text-[11.5px] mb-4 px-3.5 py-2.5 rounded-xl leading-relaxed" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>
            配置可随时保存，但路由真正生效需设 <span className="font-mono">HASHMM_LLM_ROUTING=1</span> 且配置本地模型路径 <span className="font-mono">HASHMM_LOCAL_LLM_PATH</span>。未启用时所有调用走云端（即现状）。
          </div>
        )}

        <div className="flex flex-col gap-1.5">
          {shownTasks.map((t) => {
            const cur = edits[t.task] || "auto";
            const eff = cur === "auto" ? t.default : cur;
            return (
              <Card key={t.task} padding="px-3.5 py-2.5">
                <div className="flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                      {t.label}<span className="ml-2 text-[10.5px] font-mono font-normal" style={{ color: "var(--text-tertiary)" }}>{t.task}</span>
                    </div>
                    <div className="text-[10.5px] mt-0.5 inline-flex items-center gap-1" style={{ color: "var(--text-tertiary)" }}>
                      {eff === "local" ? <HardDrive size={10} /> : <Server size={10} />}
                      实际走 {eff === "local" ? "本地 Qwen" : "云端 DeepSeek"}{cur === "auto" && "（默认）"}
                    </div>
                  </div>
                  <button onClick={() => onTest(t.task)} disabled={testing === t.task}
                    className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] flex-shrink-0"
                    style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }} title="真发一句话，看实际走哪个后端、通不通">
                    {testing === t.task ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />} 实测
                  </button>
                  <div className="flex rounded-lg overflow-hidden flex-shrink-0" style={{ border: "1px solid var(--border)" }}>
                    <SegBtn task={t.task} val="local" cur={cur} />
                    <span style={{ width: 1, background: "var(--border)" }} />
                    <SegBtn task={t.task} val="cloud" cur={cur} />
                    <span style={{ width: 1, background: "var(--border)" }} />
                    <SegBtn task={t.task} val="auto" cur={cur} />
                  </div>
                </div>
                {testRes[t.task] && (
                  <div className="mt-2 pt-2 text-[11px]" style={{ borderTop: "1px dashed var(--border)" }}>
                    {testRes[t.task].ok ? (
                      <span className="inline-flex items-center gap-1.5 flex-wrap" style={{ color: "var(--success)" }}>
                        <CheckCircle2 size={12} /> 实测走 <b>{testRes[t.task].backend === "local" ? "本地 Qwen" : "云端 DeepSeek"}</b> · {testRes[t.task].latency_ms}ms
                        {testRes[t.task].sample && <span style={{ color: "var(--text-tertiary)" }}>· 回复「{testRes[t.task].sample}」</span>}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5" style={{ color: "var(--error)" }}><XCircle size={12} /> {testRes[t.task].message || "测试失败"}</span>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      </>)}
    </PanelShell>
  );
}
