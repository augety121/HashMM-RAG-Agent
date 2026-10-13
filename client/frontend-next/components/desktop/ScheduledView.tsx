"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CalendarClock, CheckCircle2, ChevronRight, Clock, Database, History,
  ListChecks, Newspaper, Pause, Pencil, Play, Plus, Radar, RefreshCw, ShieldCheck, Trash2, XCircle,
} from "lucide-react";
import {
  createUserRoutine, deleteUserRoutine, listUserRoutines, runUserRoutine,
  toggleUserRoutine, updateUserRoutine, type UserRoutine,
} from "@/lib/api";
import { useStore } from "@/lib/store";
import {
  Badge, Button, Card, Field, PageHeader, PanelShell, StateView,
  inputClass, inputStyle,
} from "./ui/PanelKit";

type Task = UserRoutine;
type Template = {
  action: string;
  name: string;
  description: string;
  dailyAt: string;
  icon: typeof Clock;
};

const TEMPLATES: Template[] = [
  {
    action: "ai_news_radar", name: "AI 热点候选雷达", dailyAt: "08:30", icon: Radar,
    description: "用账号级联网搜索收集最新候选线索；结果回到原对话后再核验和成稿，不会自动发布。",
  },
  {
    action: "daily_brief", name: "每日工作简报", dailyAt: "09:00", icon: Newspaper,
    description: "汇总需要继续、确认和关注的工作，结果回到原对话。",
  },
  {
    action: "corpus_digest", name: "资料更新摘要", dailyAt: "09:30", icon: Database,
    description: "整理资料库的新内容与重要变化，并保留出处。",
  },
  {
    action: "kg_health", name: "知识健康检查", dailyAt: "10:00", icon: ShieldCheck,
    description: "检查知识关系和可检索状态，只报告真实问题。",
  },
];

function scheduleText(task: Task): string {
  if (task.schedule_kind === "daily" && task.daily_at) return `每天 ${task.daily_at}`;
  const seconds = task.interval_seconds || 0;
  if (!seconds) return "未设置";
  if (seconds % 3600 === 0) return `每 ${seconds / 3600} 小时`;
  if (seconds % 60 === 0) return `每 ${seconds / 60} 分钟`;
  return `每 ${seconds} 秒`;
}

function fmtTime(value?: number): string {
  return typeof value === "number" && value > 0
    ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false })
    : "尚无";
}

function workStatusText(value?: string): string {
  return {
    queued: "准备中",
    running: "进行中",
    waiting_input: "等待补充",
    waiting_approval: "等待确认",
    blocked: "暂时受阻",
    completed: "已完成",
    failed: "未完成",
    interrupted: "已中断",
    cancelled: "已取消",
  }[String(value || "")] || "尚无运行";
}

function verificationText(value?: string): string {
  return {
    verified: "证据已核验",
    partially_verified: "部分已核验",
    evidence_required: "仍需核验",
    failed: "核验失败",
    pending: "等待核验",
  }[String(value || "")] || "等待首轮运行";
}

export function ScheduledView() {
  const sid = useStore(state => state.sid);
  const token = useStore(state => state.token);
  const [data, setData] = useState<Awaited<ReturnType<typeof listUserRoutines>> | null | undefined>(undefined);
  const [loadError, setLoadError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [creating, setCreating] = useState(false);
  const [acting, setActing] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [action, setAction] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"daily" | "interval">("daily");
  const [dailyAt, setDailyAt] = useState("09:00");
  const [every, setEvery] = useState(1);
  const [unit, setUnit] = useState<"hours" | "minutes">("hours");
  const [editingId, setEditingId] = useState("");
  const [timezone, setTimezone] = useState(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [destination, setDestination] = useState<"conversation" | "work_ledger">(sid ? "conversation" : "work_ledger");
  const [boundConversationId, setBoundConversationId] = useState("");

  const load = useCallback(async () => {
    if (!token) {
      setData(null);
      setLoadError("");
      return;
    }
    setLoadError("");
    try {
      const result = await listUserRoutines();
      setData(result);
      setSelectedId(current => result.items.some(item => item.id === current) ? current : (result.items[0]?.id || ""));
    } catch (error) {
      setData(null);
      setLoadError(error instanceof Error ? error.message : "暂时无法读取自动任务。");
    }
  }, [token]);

  useEffect(() => { void load(); }, [load]);

  const tasks = data?.items || [];
  const actions = data?.actions || [];
  const selected = useMemo(() => tasks.find(task => task.id === selectedId) || null, [tasks, selectedId]);
  const schedulerOn = data?.available === true;

  const openTemplate = (template: Template) => {
    setEditingId("");
    setAction(template.action);
    setName(template.name);
    setKind("daily");
    setDailyAt(template.dailyAt);
    setTimezone(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    setDestination(sid ? "conversation" : "work_ledger");
    setBoundConversationId(sid || "");
    setShowForm(true);
    setMessage(null);
  };

  const openNew = () => {
    setEditingId("");
    setAction(actions[0]?.id || "");
    setName("");
    setKind("daily");
    setDailyAt("09:00");
    setEvery(1);
    setUnit("hours");
    setTimezone(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    setDestination(sid ? "conversation" : "work_ledger");
    setBoundConversationId(sid || "");
    setShowForm(true);
    setMessage(null);
  };

  const openEdit = (task: Task) => {
    setEditingId(task.id);
    setAction(task.action);
    setName(task.name);
    setKind(task.schedule_kind);
    setDailyAt(task.daily_at || "09:00");
    const seconds = Math.max(60, task.interval_seconds || 3600);
    if (seconds % 3600 === 0) {
      setEvery(seconds / 3600);
      setUnit("hours");
    } else {
      setEvery(Math.max(1, Math.round(seconds / 60)));
      setUnit("minutes");
    }
    setTimezone(task.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    setDestination(task.result_destination || (task.conversation_id ? "conversation" : "work_ledger"));
    setBoundConversationId(task.conversation_id || "");
    setShowForm(true);
    setMessage(null);
  };

  const save = async () => {
    const selectedAction = action || actions[0]?.id || "";
    if (!selectedAction) {
      setMessage({ ok: false, text: "当前服务端没有返回可用动作。" });
      return;
    }
    setCreating(true);
    setMessage(null);
    try {
      const input = {
        name: name.trim(),
        schedule_kind: kind,
        daily_at: kind === "daily" ? dailyAt : undefined,
        interval_seconds: kind === "interval" ? Math.max(1, every) * (unit === "hours" ? 3600 : 60) : undefined,
        conversation_id: destination === "conversation" ? (boundConversationId || sid || undefined) : "",
        timezone,
        result_destination: destination,
      } as const;
      const result = editingId
        ? await updateUserRoutine(editingId, input)
        : await createUserRoutine({ ...input, action: selectedAction });
      setShowForm(false);
      setName("");
      setEditingId("");
      setMessage({ ok: true, text: editingId ? "自动任务已更新。" : "自动任务已创建。" });
      await load();
      if (result.task?.id) setSelectedId(result.task.id);
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : "创建失败，请重试。" });
    } finally {
      setCreating(false);
    }
  };

  const run = async (task: Task) => {
    setActing(task.id);
    setMessage(null);
    try {
      const result = await runUserRoutine(task.id);
      setMessage({
        ok: result.ok === true,
        text: result.ok
          ? "本轮长任务已完成，执行、验证与投递记录均已保存。"
          : "本轮没有完整结束；失败证据和可恢复入口已保留在运行记录。",
      });
      await load();
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : "运行失败，请稍后重试。" });
    } finally {
      setActing("");
    }
  };

  const toggle = async (task: Task) => {
    setActing(task.id);
    setMessage(null);
    try {
      await toggleUserRoutine(task.id, !task.enabled);
      await load();
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : "状态没有更新。" });
    } finally {
      setActing("");
    }
  };

  const remove = async (task: Task) => {
    if (!window.confirm(`删除“${task.name || task.action}”？已写回 Chat 的结果不会被删除。`)) return;
    setActing(task.id);
    setMessage(null);
    try {
      await deleteUserRoutine(task.id);
      await load();
      setMessage({ ok: true, text: "自动任务已删除。" });
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : "删除失败。" });
    } finally {
      setActing("");
    }
  };

  return (
    <PanelShell className="workbench-vnext scheduled-workbench">
      <PageHeader icon={CalendarClock} title="自动任务"
        subtitle="把重复工作交给 HashMM；每次运行都有记录，结果回到创建它的对话"
        actions={<>
          <Button variant="primary" icon={Plus} size="sm" disabled={!token}
            onClick={openNew}>新建安排</Button>
          <Button variant="secondary" icon={RefreshCw} size="sm" disabled={!token}
            onClick={() => void load()}>刷新</Button>
        </>} />

      {!token && (
        <StateView kind="empty" icon={Clock} title="登录后使用自动任务"
          message="任务会绑定你的账号、对话和权限，只有你可以查看和管理。" />
      )}
      {token && data === undefined && <StateView kind="loading" message="正在读取你的安排…" />}
      {token && loadError && <StateView kind="error" title="自动任务暂时不可用" message={loadError} onRetry={() => void load()} />}

      {token && data && !loadError && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Badge tone={schedulerOn ? "success" : "warning"}>{schedulerOn ? "后台执行已就绪" : "当前仅支持手动运行"}</Badge>
            <Badge>{tasks.length} 项安排</Badge>
            <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              {sid ? "新任务会把结果送回当前对话" : "未选中对话时，结果只保留在任务记录"}
            </span>
          </div>

          {message && (
            <div className="mb-3 rounded-xl px-3 py-2 text-[11px]"
              style={{
                color: message.ok ? "var(--success)" : "var(--error)",
                background: "var(--bg-primary)", border: "1px solid var(--border)",
              }}>{message.text}</div>
          )}

          <div className="grid overflow-hidden rounded-xl lg:grid-cols-[300px_minmax(0,1fr)]"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", minHeight: 480 }}>
            <aside style={{ borderRight: "1px solid var(--border)" }}>
              <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
                <div>
                  <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>我的安排</div>
                  <div className="mt-0.5 text-[9.8px]" style={{ color: "var(--text-tertiary)" }}>选择一项查看运行结果</div>
                </div>
                <button className="rounded-lg p-1.5 hover:bg-[var(--bg-tertiary)]" title="新建安排"
                  onClick={openNew}><Plus size={14} style={{ color: "var(--accent)" }} /></button>
              </div>

              <div className="p-2">
                {tasks.map(task => (
                  <button key={task.id} onClick={() => { setSelectedId(task.id); setShowForm(false); }}
                    className="mb-1 flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors"
                    style={{
                      background: selectedId === task.id && !showForm ? "var(--accent-light)" : "transparent",
                      color: "var(--text-primary)",
                    }}>
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: task.enabled ? "var(--success)" : "var(--text-tertiary)" }} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[11.8px] font-medium">{task.name || task.action}</span>
                      <span className="mt-0.5 block truncate text-[9.8px]" style={{ color: "var(--text-tertiary)" }}>{scheduleText(task)}</span>
                    </span>
                    <ChevronRight size={13} style={{ color: "var(--text-tertiary)" }} />
                  </button>
                ))}
                {tasks.length === 0 && (
                  <div className="px-3 py-5 text-center text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>还没有安排</div>
                )}
              </div>

              <div className="mx-3 mt-2 pt-3" style={{ borderTop: "1px solid var(--border)" }}>
                <div className="px-1 text-[10px] font-medium" style={{ color: "var(--text-tertiary)" }}>常用安排</div>
                <div className="mt-2 space-y-1">
                  {TEMPLATES.map(template => {
                    const Icon = template.icon;
                    const supported = actions.some(item => item.id === template.action);
                    return (
                      <button key={template.action} disabled={!supported} onClick={() => openTemplate(template)}
                        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-[var(--bg-tertiary)] disabled:opacity-35">
                        <Icon size={13} style={{ color: "var(--accent)" }} />
                        <span className="min-w-0 flex-1 truncate text-[10.8px]" style={{ color: "var(--text-secondary)" }}>{template.name}</span>
                        <Plus size={11} style={{ color: "var(--text-tertiary)" }} />
                      </button>
                    );
                  })}
                </div>
              </div>
            </aside>

            <main className="min-w-0 p-5 sm:p-6">
              {showForm ? (
                <div className="mx-auto max-w-[620px]">
                  <div className="mb-5">
                    <h2 className="text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>{editingId ? "编辑安排" : "新建安排"}</h2>
                    <p className="mt-1 text-[10.8px]" style={{ color: "var(--text-tertiary)" }}>按你的本地时区运行；动作只读，结果去向清晰可控。</p>
                  </div>
                  <div className="space-y-4">
                    <Field label="要完成什么">
                      <select value={action || actions[0]?.id || ""} onChange={event => setAction(event.target.value)}
                        disabled={Boolean(editingId)} className={inputClass} style={inputStyle}>
                        {actions.length === 0 && <option value="">没有可用动作</option>}
                        {actions.map(item => <option key={item.id} value={item.id}>{item.name} · {item.description}</option>)}
                      </select>
                    </Field>
                    <Field label="任务名称（可选）">
                      <input value={name} onChange={event => setName(event.target.value)}
                        placeholder="例如：工作日上午简报" className={inputClass} style={inputStyle} />
                    </Field>
                    <Field label="什么时候运行">
                      <div className="flex flex-wrap items-center gap-2">
                        {(["daily", "interval"] as const).map(value => (
                          <button key={value} onClick={() => setKind(value)}
                            className="rounded-lg px-3 py-2 text-[11px]"
                            style={{
                              color: kind === value ? "#fff" : "var(--text-secondary)",
                              background: kind === value ? "var(--accent)" : "var(--bg-tertiary)",
                            }}>{value === "daily" ? "每天定点" : "固定间隔"}</button>
                        ))}
                        {kind === "daily" ? (
                          <input type="time" value={dailyAt} onChange={event => setDailyAt(event.target.value)}
                            className="rounded-lg px-3 py-2 text-[11px] outline-none" style={inputStyle} />
                        ) : (
                          <>
                            <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>每</span>
                            <input type="number" min={1} value={every} onChange={event => setEvery(Number(event.target.value) || 1)}
                              className="w-16 rounded-lg px-2 py-2 text-[11px] outline-none" style={inputStyle} />
                            <select value={unit} onChange={event => setUnit(event.target.value as "hours" | "minutes")}
                              className="rounded-lg px-2 py-2 text-[11px] outline-none" style={inputStyle}>
                              <option value="hours">小时</option><option value="minutes">分钟</option>
                            </select>
                          </>
                        )}
                      </div>
                    </Field>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Field label="所在时区">
                        <input value={timezone} onChange={event => setTimezone(event.target.value)}
                          placeholder="例如 Asia/Shanghai" className={inputClass} style={inputStyle} />
                      </Field>
                      <Field label="结果保存到">
                        <select value={destination} onChange={event => setDestination(event.target.value as "conversation" | "work_ledger")}
                          className={inputClass} style={inputStyle}>
                          <option value="work_ledger">任务记录</option>
                          <option value="conversation" disabled={!boundConversationId && !sid}>原对话</option>
                        </select>
                      </Field>
                    </div>
                    <div className="rounded-xl px-3 py-2.5 text-[10.8px] leading-5"
                      style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                      {destination === "conversation"
                        ? "结果会回到绑定对话；创建和每次执行时都会重新检查对话归属。"
                        : "结果会进入统一任务记录，可在桌面端和 App 的“今天/工作”继续处理。"}
                      <br />当前动作仅执行读取与汇总，不会无人确认地提交、发送、删除或付款。
                    </div>
                    <div className="flex items-center gap-2">
                      <Button variant="primary" size="sm" busy={creating} onClick={() => void save()}>{editingId ? "保存修改" : "创建安排"}</Button>
                      <Button variant="ghost" size="sm" onClick={() => { setShowForm(false); setEditingId(""); }}>取消</Button>
                    </div>
                  </div>
                </div>
              ) : selected ? (
                <div>
                  <div className="flex flex-wrap items-start gap-3 pb-5" style={{ borderBottom: "1px solid var(--border)" }}>
                    <span className="flex h-10 w-10 items-center justify-center rounded-xl"
                      style={{ color: "var(--accent)", background: "var(--accent-light)" }}><Clock size={18} /></span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>{selected.name || selected.action}</h2>
                        <Badge tone={selected.enabled ? "success" : "neutral"}>{selected.enabled ? "按计划运行" : "已暂停"}</Badge>
                      </div>
                      <p className="mt-1 text-[10.8px]" style={{ color: "var(--text-tertiary)" }}>{scheduleText(selected)} · 动作 {selected.action}</p>
                    </div>
                    <Button variant="primary" icon={Play} size="sm" busy={acting === selected.id}
                      onClick={() => void run(selected)}>立即运行</Button>
                  </div>

                  <div className="grid gap-3 py-5 sm:grid-cols-2 xl:grid-cols-4">
                    {[
                      ["下次运行", fmtTime(selected.next_run)],
                      ["上次运行", fmtTime(selected.last_run)],
                      ["运行次数", `${selected.run_count || 0} 次`],
                      ["结果去向", selected.result_destination === "conversation" ? "原对话" : "任务记录"],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                        <div className="text-[9.8px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                        <div className="mt-1 truncate text-[11.5px] font-medium" style={{ color: "var(--text-primary)" }}>{value}</div>
                      </div>
                    ))}
                  </div>

                  <div className="mb-4 rounded-2xl p-4"
                    style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                    <div className="flex flex-wrap items-start gap-3">
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
                        style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
                        <ListChecks size={17} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>长任务运行记录</div>
                          <Badge tone={selected.last_work_status === "failed" ? "error" : selected.last_work_status === "completed" ? "success" : "neutral"}>
                            {workStatusText(selected.last_work_status)}
                          </Badge>
                          <Badge>{verificationText(selected.verification_status)}</Badge>
                        </div>
                        <p className="mt-1 text-[10.3px] leading-5" style={{ color: "var(--text-tertiary)" }}>
                          每次运行依次记录权限边界、动作、验证和投递；这里只展示可审计事实，不展示或保存模型私有思维过程。
                        </p>
                        {(selected.next_actions || []).length > 0 && (
                          <div className="mt-3 space-y-1.5">
                            {(selected.next_actions || []).slice(0, 3).map((item, index) => (
                              <div key={`${item}-${index}`} className="flex items-start gap-2 text-[10.5px]" style={{ color: "var(--text-secondary)" }}>
                                <span className="mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: "var(--accent)" }} />
                                <span>{item}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      {selected.last_work_run_id && (
                        <Button variant="secondary" icon={History} size="sm"
                          onClick={() => useStore.getState().set({
                            desktopView: "work-detail",
                            workDetailId: selected.last_work_run_id,
                            workDetailParent: "work-active",
                          })}>查看任务链</Button>
                      )}
                    </div>
                  </div>

                  <div className="rounded-2xl p-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                    <div className="flex items-center gap-2 text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
                      <History size={14} /> 最近一次结果
                      {selected.last_run > 0 && (
                        <Badge tone={selected.last_status === "error" ? "error" : "success"}>
                          {selected.last_status === "error" ? <><XCircle size={10} /> 失败</> : <><CheckCircle2 size={10} /> 已完成</>}
                        </Badge>
                      )}
                    </div>
                    <div className="mt-3 whitespace-pre-wrap text-[10.8px] leading-5" style={{ color: "var(--text-secondary)" }}>
                      {selected.last_result || "还没有运行结果。立即运行一次后，真实结果会显示在这里。"}
                    </div>
                  </div>

                  <div className="mt-4 flex flex-wrap items-center gap-2">
                    <Button variant="secondary" icon={Pencil} size="sm" disabled={acting === selected.id}
                      onClick={() => openEdit(selected)}>编辑安排</Button>
                    <Button variant="secondary" icon={selected.enabled ? Pause : Play} size="sm" busy={acting === selected.id}
                      onClick={() => void toggle(selected)}>{selected.enabled ? "暂停安排" : "恢复安排"}</Button>
                    <Button variant="danger" icon={Trash2} size="sm" disabled={acting === selected.id}
                      onClick={() => void remove(selected)}>删除安排</Button>
                  </div>
                </div>
              ) : (
                <div className="mx-auto max-w-[560px] py-12">
                  <div className="flex h-12 w-12 items-center justify-center rounded-2xl"
                    style={{ color: "var(--accent)", background: "var(--accent-light)" }}><Clock size={22} /></div>
                  <h2 className="mt-4 text-[17px] font-semibold" style={{ color: "var(--text-primary)" }}>安排第一项重复工作</h2>
                  <p className="mt-2 text-[11px] leading-5" style={{ color: "var(--text-tertiary)" }}>
                    从左侧常用安排开始，或创建自己的任务。每次执行都有状态和结果，不会把计划当作已经完成。
                  </p>
                  <Button variant="primary" icon={Plus} size="sm" onClick={openNew}>新建安排</Button>
                </div>
              )}
            </main>
          </div>
        </>
      )}
    </PanelShell>
  );
}
