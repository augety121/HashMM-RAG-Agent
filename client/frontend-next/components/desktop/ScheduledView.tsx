"use client";
/** components/desktop/ScheduledView.tsx — 定时任务 / 主动服务（V103.52 改用统一设计系统 PanelKit）。
 *
 * 后端 scheduler 的「持久化定时任务」入口（管理员可见）：让 Agent 按计划主动干活
 * （每早汇总文档、定期巡检 KG 等），呼应主动式助理「不等你问、提前为你做」。
 */
import { useEffect, useState, useCallback } from "react";
import { Clock, Play, Power, RefreshCw, CheckCircle2, XCircle, Plus } from "lucide-react";
import { listScheduled, runScheduled, toggleScheduled, createScheduledTask } from "@/lib/api";
import { PanelShell, PageHeader, Card, Button, Badge, Field, StateView, inputClass, inputStyle } from "./ui/PanelKit";

type Task = { id?: string; name?: string; action?: string; schedule_kind?: string; interval_seconds?: number; daily_at?: string; enabled?: number | boolean; last_run?: number; next_run?: number; last_status?: string; run_count?: number };

function scheduleText(t: Task): string {
  if (t.schedule_kind === "daily" && t.daily_at) return `每天 ${t.daily_at}`;
  const s = t.interval_seconds || 0;
  if (!s) return "—";
  if (s % 3600 === 0) return `每 ${s / 3600} 小时`;
  if (s % 60 === 0) return `每 ${s / 60} 分钟`;
  return `每 ${s} 秒`;
}
const fmtTime = (ts?: number) => (typeof ts === "number" && ts > 0 ? new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false }) : "—");

export function ScheduledView() {
  const [data, setData] = useState<any>(undefined);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [acting, setActing] = useState<string>("");
  const [showForm, setShowForm] = useState(false);
  const [fAction, setFAction] = useState("");
  const [fName, setFName] = useState("");
  const [fKind, setFKind] = useState<"daily" | "interval">("daily");
  const [fDailyAt, setFDailyAt] = useState("09:00");
  const [fEvery, setFEvery] = useState(1);
  const [fUnit, setFUnit] = useState<"hours" | "minutes">("hours");
  const [creating, setCreating] = useState(false);
  const [createMsg, setCreateMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const d = await listScheduled().catch(() => null);
      if (d === null) { setDenied(true); setData({}); }
      else { setDenied(false); setData(d || {}); }
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const onRun = async (id: string) => { setActing(id); try { await runScheduled(id); } catch { /* */ } finally { setActing(""); load(); } };
  const onToggle = async (id: string) => { setActing(id); try { await toggleScheduled(id); } catch { /* */ } finally { setActing(""); load(); } };

  const onCreate = async () => {
    const action = fAction || actions[0] || "";
    if (!action) { setCreateMsg({ ok: false, text: "请选择一个动作" }); return; }
    setCreating(true); setCreateMsg(null);
    const body: Record<string, unknown> = { action, name: fName.trim(), schedule_kind: fKind };
    if (fKind === "daily") body.daily_at = fDailyAt;
    else body.interval_seconds = Math.max(1, fEvery) * (fUnit === "hours" ? 3600 : 60);
    try {
      const r = await createScheduledTask(body);
      if (r && r.ok) { setCreateMsg({ ok: true, text: "已创建任务" }); setFName(""); setShowForm(false); load(); }
      else setCreateMsg({ ok: false, text: "创建失败" });
    } catch { setCreateMsg({ ok: false, text: "创建失败，请重试" }); }
    finally { setCreating(false); }
  };

  const d = data || {};
  const tasks: Task[] = Array.isArray(d.tasks) ? d.tasks : [];
  const actions: string[] = Array.isArray(d.actions) ? d.actions : [];
  const schedulerOn = d.enabled === true;

  return (
    <PanelShell>
      <PageHeader icon={Clock} title="定时任务 · 主动服务"
        subtitle="让 Agent 按计划主动干活（每早汇总、定期巡检等）—— 不等你问、提前为你做"
        actions={<>
          <Button variant={showForm ? "secondary" : "primary"} icon={Plus} size="sm" onClick={() => { setShowForm(v => !v); setCreateMsg(null); }}>新建任务</Button>
          <Button variant="secondary" icon={RefreshCw} busy={busy} size="sm" onClick={load}>刷新</Button>
        </>} />

      {denied && <StateView kind="empty" icon={Clock} message="此面板需要管理员权限，或后端未连接。" />}

      {!denied && data !== undefined && (<>
        <div className="flex items-center gap-2 flex-wrap mb-4">
          <Badge tone={schedulerOn ? "success" : "neutral"}>调度器 {schedulerOn ? "运行中" : "未开启"}</Badge>
          {actions.map(a => <Badge key={a} tone="neutral" mono>{a}</Badge>)}
        </div>

        {showForm && (
          <Card className="mb-4">
            <div className="text-[13.5px] font-semibold mb-3" style={{ color: "var(--text-primary)" }}>新建定时任务</div>
            <div className="flex flex-col gap-3">
              <Field label="动作">
                <select value={fAction || actions[0] || ""} onChange={e => setFAction(e.target.value)} className={inputClass} style={inputStyle}>
                  {actions.length === 0 && <option value="">（无可用动作）</option>}
                  {actions.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
              </Field>
              <Field label="名称（可选）"><input value={fName} onChange={e => setFName(e.target.value)} placeholder="如：每早语料简报" className={inputClass} style={inputStyle} /></Field>
              <Field label="调度方式">
                <div className="flex items-center gap-2 flex-wrap">
                  {(["daily", "interval"] as const).map(k => (
                    <button key={k} onClick={() => setFKind(k)} className="text-[12px] px-3 py-1.5 rounded-lg transition-colors"
                      style={{ background: fKind === k ? "var(--accent)" : "var(--bg-tertiary)", color: fKind === k ? "#fff" : "var(--text-secondary)" }}>{k === "daily" ? "每天定点" : "固定间隔"}</button>
                  ))}
                  {fKind === "daily" ? (
                    <input type="time" value={fDailyAt} onChange={e => setFDailyAt(e.target.value)} className="text-[12.5px] px-2.5 py-1.5 rounded-lg outline-none" style={inputStyle} />
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <span className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>每</span>
                      <input type="number" min={1} value={fEvery} onChange={e => setFEvery(parseInt(e.target.value) || 1)} className="w-16 text-[12.5px] px-2 py-1.5 rounded-lg outline-none" style={inputStyle} />
                      <select value={fUnit} onChange={e => setFUnit(e.target.value as "hours" | "minutes")} className="text-[12.5px] px-2 py-1.5 rounded-lg outline-none" style={inputStyle}>
                        <option value="hours">小时</option><option value="minutes">分钟</option>
                      </select>
                    </div>
                  )}
                </div>
              </Field>
              <div className="flex items-center gap-2.5">
                <Button variant="primary" busy={creating} size="sm" onClick={onCreate}>创建任务</Button>
                {createMsg && <span className="text-[11.5px]" style={{ color: createMsg.ok ? "var(--success)" : "var(--error)" }}>{createMsg.text}</span>}
              </div>
            </div>
          </Card>
        )}

        {tasks.length === 0 && <StateView kind="empty" icon={Clock}
          title="还没有定时任务"
          message="让 Agent 按计划主动干活 —— 比如每天定点汇总新文档、定期巡检知识图谱健康，不等你问、提前为你做。"
          action={<Button variant="primary" icon={Plus} size="sm" onClick={() => { setShowForm(true); setCreateMsg(null); }}>新建任务</Button>} />}

        {tasks.length > 0 && (
          <div className="flex flex-col gap-2">
            {tasks.map((t, i) => {
              const on = t.enabled === 1 || t.enabled === true;
              const id = t.id || "";
              return (
                <Card key={id || i} padding="p-4">
                  <div className="flex items-center gap-2.5">
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: on ? "var(--success)" : "var(--text-tertiary)" }} />
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
                        {t.name || t.action || "（未命名任务）"}<span className="ml-2 text-[10.5px] font-mono font-normal" style={{ color: "var(--text-tertiary)" }}>{t.action}</span>
                      </div>
                      <div className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{scheduleText(t)} · 下次 {fmtTime(t.next_run)} · 已运行 {t.run_count ?? 0} 次</div>
                    </div>
                    <button onClick={() => onRun(id)} disabled={acting === id} title="立即运行" aria-label="立即运行" className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] flex-shrink-0"><Play size={14} style={{ color: "var(--accent)" }} /></button>
                    <button onClick={() => onToggle(id)} disabled={acting === id} title={on ? "禁用" : "启用"} aria-label={on ? "禁用" : "启用"} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)] flex-shrink-0"><Power size={14} style={{ color: on ? "var(--success)" : "var(--text-tertiary)" }} /></button>
                  </div>
                  {t.last_run ? (
                    <div className="flex items-center gap-1.5 mt-2 pl-4 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                      {t.last_status === "error" ? <XCircle size={11} style={{ color: "var(--error)" }} /> : <CheckCircle2 size={11} style={{ color: "var(--success)" }} />}
                      上次 {fmtTime(t.last_run)} · {t.last_status || "ok"}
                    </div>
                  ) : null}
                </Card>
              );
            })}
          </div>
        )}
      </>)}
    </PanelShell>
  );
}
