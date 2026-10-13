"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Globe2, Play, ExternalLink, ShieldCheck, ShieldX, Trash2, Download, RefreshCw, CheckCircle2, XCircle, MonitorUp, MessageSquare } from "lucide-react";
import { createDispatch, listDispatch, type DispatchTask } from "@/lib/api";
import { getBrowser, saveFile, type BrowserPolicy, type BrowserTraceEvent } from "@/lib/desktop";
import { useStore } from "@/lib/store";
import { PanelShell, PageHeader, Card, CardHeader, Button, Badge, Field, inputClass, inputStyle, StateView } from "./ui/PanelKit";
import { insertContextIntoChat } from "@/lib/contextInsert";

type MatchMode = "exact" | "subsequence" | "any_order";
const QA_SUFFIX = `\n\n请按网站验收任务执行：逐页操作并以真实页面状态为准；发现问题时记录严重级别、复现步骤、预期结果、实际结果和对应页面 URL。不要把计划或模型自述当作完成证据。`;

function boundedEvents(values: BrowserTraceEvent[]): BrowserTraceEvent[] {
  const next = values.slice(-160);
  let images = 0;
  for (let i = next.length - 1; i >= 0; i--) {
    if (!next[i].image) continue;
    images++;
    if (images > 10) next[i] = { ...next[i], image: undefined, screenshot_present: true };
  }
  return next;
}

export function BrowserView() {
  const sid = useStore(s => s.sid);
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<"browse" | "qa">("browse");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [events, setEvents] = useState<BrowserTraceEvent[]>([]);
  const [policy, setPolicy] = useState<BrowserPolicy>({ version: 1, allow: [], block: [], updated_at: 0 });
  const [session, setSession] = useState({ active: false, visible: false, url: "", title: "" });
  const [tasks, setTasks] = useState<DispatchTask[] | null>(null);
  const [expected, setExpected] = useState("navigate,read");
  const [matchMode, setMatchMode] = useState<MatchMode>("subsequence");

  const browser = typeof window !== "undefined" ? getBrowser() : null;
  const refreshState = useCallback(async () => {
    const b = getBrowser(); if (!b) return;
    const s = await b.state();
    if (s?.ok) { setEvents(s.events || []); setPolicy(s.policy); setSession(s.session); }
  }, []);
  const refreshTasks = useCallback(async () => {
    try { const r = await listDispatch(30); setTasks((r.items || []).filter(x => x.kind === "browser_use")); }
    catch { setTasks([]); }
  }, []);

  useEffect(() => {
    refreshState(); refreshTasks();
    const b = getBrowser(); const off = b?.onEvent(ev => {
      setEvents(prev => boundedEvents([...prev, ev]));
      if (ev.url) setSession(prev => ({ ...prev, active: ev.phase !== "session", url: ev.url || prev.url, title: ev.title || prev.title }));
      if (ev.phase === "session") setSession(prev => ({ ...prev, active: false, visible: false }));
    });
    const timer = setInterval(refreshTasks, 4000);
    return () => { off?.(); clearInterval(timer); };
  }, [refreshState, refreshTasks]);

  const run = async () => {
    const text = goal.trim(); if (!text || busy) return;
    if (!sid) { setError("请先新建或打开一个对话；浏览器结论与 QA 报告需要回到明确的当前对话。"); return; }
    setBusy(true); setError("");
    try {
      await createDispatch("desktop", "browser_use", { goal: text + (mode === "qa" ? QA_SUFFIX : ""), conv_id: sid || "", evidence_schema: "hashmm.browser-trajectory.v1" });
      setGoal(""); await refreshTasks();
      await getBrowser()?.openCockpit();
    } catch (e) { setError((e as Error)?.message || "浏览器任务入队失败"); }
    finally { setBusy(false); }
  };

  const doneEvents = useMemo(() => events.filter(e => e.phase === "done" || e.phase === "error"), [events]);
  const latest = useMemo(() => [...doneEvents].reverse().find(e => e.image), [doneEvents]);
  const evaluation = browser?.evaluate(events, expected, matchMode) || null;
  const latestResult = tasks?.find(t => t.status === "done")?.result || "";

  const changePolicy = async (host: string, decision: "allow" | "block" | "remove") => {
    const r = await getBrowser()?.setPolicy(host, decision);
    if (r?.ok && r.policy) setPolicy(r.policy);
  };
  const clearTrace = async () => { await getBrowser()?.clearTrace(); setEvents([]); };
  const exportEvidence = async () => {
    const data = getBrowser()?.datasetCase({ goal: goal.trim(), expected, mode: matchMode, events, result: latestResult });
    if (!data) { setError("轨迹评测模块不可用"); return; }
    await saveFile(`browser-trajectory-${new Date().toISOString().replace(/[:.]/g, "-")}.json`, JSON.stringify(data, null, 2));
  };
  const sendEvidenceToChat = () => {
    const safeEvents = events.slice(-80).map(({ image: _image, ...ev }) => ({
      ...ev,
      screenshot_present: ev.screenshot_present || !!_image,
    }));
    insertContextIntoChat("browser", "共享浏览器真实轨迹", {
      current_page: session,
      trajectory_evaluation: evaluation,
      latest_task_result: latestResult,
      events: safeEvents,
    }, "请基于我从共享浏览器带回的真实轨迹和任务结果继续分析：先区分已验证事实、失败动作和仍缺的证据，再给结论与下一步。", session.url || "desktop-browser");
  };

  if (!browser) return <PanelShell><StateView kind="error" message="当前不是 HashMM 桌面端，隔离浏览器桥不可用。" /></PanelShell>;
  return (
    <PanelShell>
      <PageHeader icon={Globe2} title="共享浏览器" subtitle="隔离登录态 · 首站授权 · 实时截图与动作证据 · 结果回到当前对话"
        actions={<Button variant="primary" size="sm" icon={MessageSquare} onClick={sendEvidenceToChat}>带证据回 Chat</Button>} />
      <div className="grid grid-cols-1 xl:grid-cols-[minmax(360px,0.9fr)_minmax(480px,1.4fr)] gap-4">
        <div className="space-y-4">
          <Card>
            <CardHeader title="给浏览器一个任务" sub={sid ? `结果将回到当前对话 ${sid}` : "未选对话：结果保留在任务队列"} icon={Play}
              right={<Badge tone={session.active ? "success" : "neutral"}>{session.active ? "执行中" : "空闲"}</Badge>} />
            <div className="grid grid-cols-2 gap-2 mb-2">
              <button className="px-3 py-2 rounded-[10px] text-[12px]" onClick={() => setMode("browse")}
                style={{ border: `1px solid ${mode === "browse" ? "var(--accent)" : "var(--border)"}`, color: mode === "browse" ? "var(--accent)" : "var(--text-secondary)" }}>浏览 / 调研</button>
              <button className="px-3 py-2 rounded-[10px] text-[12px]" onClick={() => setMode("qa")}
                style={{ border: `1px solid ${mode === "qa" ? "var(--accent)" : "var(--border)"}`, color: mode === "qa" ? "var(--accent)" : "var(--text-secondary)" }}>网站验收 / QA</button>
            </div>
            <textarea className={inputClass} style={{ ...inputStyle, minHeight: 112 }} value={goal} onChange={e => setGoal(e.target.value)}
              placeholder={mode === "qa" ? "例如：验收 http://localhost:3000 的登录、导航和错误提示，输出可复现缺陷报告" : "例如：打开产品官网，核对最新文档并整理带 URL 的结论"} />
            {error && <div className="mt-2 text-[11px]" style={{ color: "var(--error)" }}>{error}</div>}
            <div className="flex gap-2 mt-3 flex-wrap">
              <Button variant="primary" icon={Play} onClick={run} busy={busy}>进入 desktop runner 执行</Button>
              <Button variant="secondary" icon={MonitorUp} onClick={() => browser.openCockpit()}>打开共享视图</Button>
            </div>
            <div className="text-[10.5px] mt-2" style={{ color: "var(--text-tertiary)" }}>快捷键 Ctrl+Shift+B。任务链：主 UI → 派活队列 → desktop AgentLoop → Electron 隔离浏览器 → 当前对话/轨迹证据。</div>
          </Card>

          <Card>
            <CardHeader title="站点权限" sub="首次访问原生确认；跨站跳转先拦截，再显式申请" icon={ShieldCheck} />
            <PolicyList title="始终允许" tone="allow" items={policy.allow} onRemove={h => changePolicy(h, "remove")} />
            <PolicyList title="已阻止" tone="block" items={policy.block} onRemove={h => changePolicy(h, "remove")} />
            {!policy.allow.length && !policy.block.length && <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>尚无持久策略；第一次导航时会弹出站点授权。</div>}
          </Card>
        </div>

        <div className="space-y-4">
          <Card padding="p-0 overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
              <Globe2 size={14} style={{ color: "var(--accent)" }} /><span className="text-[12.5px] font-semibold">实时页面证据</span>
              <span className="text-[10.5px] truncate" style={{ color: "var(--text-tertiary)" }}>{latest?.url || session.url || "等待 navigate"}</span>
              <span className="ml-auto"><Button size="sm" variant="ghost" icon={ExternalLink} onClick={() => browser.openCockpit()}>放大</Button></span>
            </div>
            <div className="relative min-h-[300px] flex items-center justify-center" style={{ background: "#0b111a" }}>
              {latest?.image ? <img src={latest.image} alt="受控浏览器最近一次真实截图" className="block max-w-full max-h-[520px] object-contain" />
                : <div className="text-[12px]" style={{ color: "#76859a" }}>任务执行后，这里显示真实 webContents 截图；不是演示图。</div>}
            </div>
          </Card>

          <Card>
            <CardHeader title="动作轨迹验收" sub="把面试资料里的 exact / subsequence / any-order 轨迹评测落到真实事件" icon={ShieldCheck}
              right={<div className="flex gap-1"><Button size="sm" variant="ghost" icon={Trash2} onClick={clearTrace}>清空</Button><Button size="sm" variant="ghost" icon={Download} onClick={exportEvidence}>导出 JSON</Button></div>} />
            <div className="grid grid-cols-[1fr_150px] gap-2">
              <Field label="预期动作序列"><input className={inputClass} style={inputStyle} value={expected} onChange={e => setExpected(e.target.value)} placeholder="navigate,click,read" /></Field>
              <Field label="匹配规则"><select className={inputClass} style={inputStyle} value={matchMode} onChange={e => setMatchMode(e.target.value as MatchMode)}><option value="exact">完全一致</option><option value="subsequence">有序子序列</option><option value="any_order">任意顺序</option></select></Field>
            </div>
            {evaluation && <div className="flex items-center gap-2 mt-3 text-[11.5px]" style={{ color: evaluation.pass ? "var(--success)" : "var(--warning)" }}>
              {evaluation.pass ? <CheckCircle2 size={14} /> : <XCircle size={14} />} {evaluation.summary}<span style={{ color: "var(--text-tertiary)" }}>实际：{evaluation.actual.join(" → ") || "暂无"}</span>
            </div>}
            <div className="mt-3 max-h-[220px] overflow-auto space-y-1.5">
              {doneEvents.length === 0 ? <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>暂无真实动作。</div> : doneEvents.slice(-20).reverse().map(ev => (
                <div key={`${ev.seq}-${ev.phase}`} className="flex items-start gap-2 px-2.5 py-2 rounded-lg text-[11px]" style={{ background: "var(--bg-secondary)" }}>
                  <Badge tone={ev.phase === "error" ? "error" : "success"}>{ev.action}</Badge>
                  <span className="flex-1 min-w-0 break-all" style={{ color: "var(--text-secondary)" }}>{ev.error || ev.title || ev.url || `元素 #${ev.index ?? "-"}`}</span>
                  <span className="font-mono" style={{ color: "var(--text-tertiary)" }}>#{ev.seq}</span>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <CardHeader title="最近浏览器任务" sub="队列状态与结果，不再要求手写 payload JSON" icon={RefreshCw} right={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={refreshTasks}>刷新</Button>} />
            {tasks === null ? <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>加载中…</div> : tasks.length === 0 ? <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>暂无浏览器任务。</div> : tasks.slice(0, 8).map(t => (
              <div key={t.task_id} className="flex items-start gap-2 py-2" style={{ borderTop: "1px solid var(--border)" }}>
                <Badge tone={t.status === "done" ? "success" : t.status === "failed" ? "error" : t.status === "claimed" ? "accent" : "neutral"}>{t.status}</Badge>
                <div className="min-w-0"><div className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{t.task_id}</div>{t.result && <div className="text-[11px] whitespace-pre-wrap line-clamp-3 mt-1" style={{ color: "var(--text-secondary)" }}>{t.result}</div>}</div>
              </div>
            ))}
          </Card>
        </div>
      </div>
    </PanelShell>
  );
}

function PolicyList({ title, tone, items, onRemove }: { title: string; tone: "allow" | "block"; items: string[]; onRemove: (host: string) => void }) {
  if (!items.length) return null;
  return <div className="mb-3"><div className="text-[10.5px] mb-1.5" style={{ color: "var(--text-tertiary)" }}>{title}</div>{items.map(host => <div key={host} className="flex items-center gap-2 py-1.5 text-[11.5px]">{tone === "allow" ? <ShieldCheck size={13} style={{ color: "var(--success)" }} /> : <ShieldX size={13} style={{ color: "var(--error)" }} />}<span className="font-mono flex-1">{host}</span><button onClick={() => onRemove(host)} title="移除策略"><Trash2 size={12} style={{ color: "var(--text-tertiary)" }} /></button></div>)}</div>;
}
