"use client";
/** GlobalWorkspaceView — 总控中枢（V254）。
 *
 * 理念对标 Anthropic《A global workspace in language models》与 GWT：
 * 各专家模块（对话/深度检索/多智能体/派活/画布/电脑操作/记忆…）平时并行、
 * 彼此隔离地工作；重要信息进入**全局工作区**被广播后才"全局可见、可统筹"。
 * 本视图就是那块工作区的可视化：
 *   · 全局焦点：系统当前统筹的目标（自动捕获高显著度事件，也可人来拍板）
 *   · 意识缓冲：最近 7 条"进入意识"的广播（小容量，呼应工作记忆）
 *   · 模块面板：每个专家模块的四色状态 + 最近动作 + 事件计数
 *   · 事件史：含未进入意识的低显著度事件（"处理过但没广播"），排障用
 * 数据源 routes/workspace_gw.py：快照轮询 + SSE 实时推送双通道，SSE 断了退化为轮询。
 */
import { useEffect, useRef, useState } from "react";
import { gwState, gwFocus, gwBroadcast, gwAsk, listModels, getHealth, deepSearchStatus, gwRules, gwRuleSet, loopsList, loopGoal, loopInterval, loopPause, loopResume, loopStop, loopAcceptance, type GwSnapshot, type GwEvent, type EventRule, archAdvise, type LoopInfo, type ArchAdvice } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Crosshair, Radio, Loader2, Send, BrainCircuit, Activity, CircleDot, MessageCircleQuestion, ChevronDown, HeartPulse, Rocket, Users, MessageSquare, Zap, RefreshCcw, Target, Timer, StopCircle, PauseCircle, PlayCircle, ShieldCheck, PlugZap, Network, CheckCircle2, AlertTriangle } from "lucide-react";

const STATE_COLOR: Record<string, string> = {
  working: "#b45309", done: "#15803d", error: "#b42318", blocked: "#b42318", idle: "var(--text-tertiary)",
};
const STATE_LABEL: Record<string, string> = {
  working: "执行中", done: "已完成", error: "出错", blocked: "受阻", idle: "空闲",
};

function ago(ts?: number | null): string {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s 前`;
  if (s < 3600) return `${Math.round(s / 60)}m 前`;
  return `${Math.round(s / 3600)}h 前`;
}

function EventRow({ e, dim }: { e: GwEvent; dim?: boolean }) {
  return (
    <div className="flex items-start gap-2 py-1.5" style={{ opacity: dim ? 0.55 : 1 }}>
      <span className="mt-1 w-1.5 h-1.5 rounded-full flex-shrink-0"
        style={{ background: e.won ? "var(--accent)" : "var(--text-tertiary)" }} />
      <div className="min-w-0 flex-1">
        <div className="text-[12px] leading-snug" style={{ color: "var(--text-primary)" }}>{e.summary}</div>
        <div className="text-[10px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
          {e.module} · {e.kind} · 显著度 {Math.round((e.salience || 0) * 100)}% · {ago(e.ts)}
        </div>
      </div>
    </div>
  );
}

export function GlobalWorkspaceView() {
  const token = useStore(s => s.token);
  const [snap, setSnap] = useState<GwSnapshot | null>(null);
  const [err, setErr] = useState("");
  const [focusDraft, setFocusDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  async function refresh() {
    try { setSnap(await gwState(80)); setErr(""); }
    catch (e) { setErr((e as Error)?.message || "后端版本过旧（缺 /api/gw），请升级后端"); }
  }

  useEffect(() => {
    refresh();
    // SSE 实时（带 token query 不行——EventSource 无 header，走匿名可读端点即可）
    try {
      const es = new EventSource("/api/gw/stream");
      esRef.current = es;
      es.addEventListener("snapshot", (ev) => { try { setSnap(JSON.parse((ev as MessageEvent).data)); setLive(true); } catch { /* */ } });
      es.addEventListener("gw", () => { refresh(); });   // 有新广播 → 拉全量快照（简单可靠）
      es.onerror = () => { setLive(false); };
    } catch { setLive(false); }
    const t = setInterval(refresh, 8000);   // SSE 之外的兜底轮询
    return () => { clearInterval(t); esRef.current?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const focus = snap?.focus;
  const consciousIds = new Set((snap?.buffer || []).map(b => b.id));

  return (
    <div className="flex-1 overflow-y-auto px-6 py-5" style={{ background: "var(--bg-primary)" }}>
      <div className="max-w-[1080px] mx-auto">
        {/* 顶：徽章式标题（V259 三工坊统一视觉）+ 实时状态 */}
        <div className="flex items-center gap-2.5 mb-4">
          <div className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0" style={{ background: "var(--accent-light)" }}>
            <BrainCircuit size={16} style={{ color: "var(--accent)" }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[15px] font-bold" style={{ color: "var(--text-primary)" }}>总控中枢</div>
            <div className="text-[11px] truncate" style={{ color: "var(--text-tertiary)" }}>
              各模块并行工作、彼此隔离；重要事件广播进这块「全局工作区」后，才全局可见、可统筹（GWT）
            </div>
          </div>
          <span className="ml-auto inline-flex items-center gap-1 text-[10.5px] px-2 py-0.5 rounded-full"
            style={{ background: live ? "rgba(21,128,61,0.12)" : "var(--bg-tertiary)", color: live ? "#15803d" : "var(--text-tertiary)" }}>
            <CircleDot size={9} /> {live ? "实时" : "轮询"}
          </span>
        </div>
        {err && <div className="mb-4 text-[12px] px-3 py-2 rounded-xl" style={{ background: "rgba(180,35,24,0.08)", color: "#b42318" }}>{err}</div>}

        {/* 全局焦点 */}
        <div className="rounded-2xl px-5 py-4 mb-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2 mb-2">
            <Crosshair size={14} style={{ color: "var(--accent)" }} />
            <span className="text-[12.5px] font-bold" style={{ color: "var(--text-primary)" }}>全局焦点</span>
            {focus && <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              来自 {focus.source} · {focus.by === "auto" ? "自动捕获" : `由 ${focus.by} 设定`} · {ago(focus.ts)}
            </span>}
          </div>
          <div className="text-[14.5px] font-medium mb-3" style={{ color: focus ? "var(--text-primary)" : "var(--text-tertiary)" }}>
            {focus?.goal || "（暂无——系统空闲，或下方手动设定一个统筹目标）"}
          </div>
          <div className="flex gap-2">
            <input value={focusDraft} onChange={e => setFocusDraft(e.target.value)}
              onKeyDown={async e => { if (e.key === "Enter" && focusDraft.trim()) { setBusy(true); try { await gwFocus(focusDraft.trim()); setFocusDraft(""); refresh(); } finally { setBusy(false); } } }}
              placeholder="人来拍板：设定系统当前该统筹的目标（回车提交，留空提交=清除）"
              className="flex-1 px-3 py-2 rounded-xl text-[12.5px] outline-none"
              style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            <button disabled={busy} onClick={async () => { setBusy(true); try { await gwFocus(focusDraft.trim()); setFocusDraft(""); refresh(); } finally { setBusy(false); } }}
              className="px-3.5 py-2 rounded-xl text-[12px] font-semibold text-white disabled:opacity-60" style={{ background: "var(--accent)" }}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : "设定"}
            </button>
          </div>
        </div>

        {/* 系统体检条（V256）：总控=整个项目的总控——版本/模型/深检/高级能力一屏在握 */}
        <SystemPulse />

        {/* 指挥台（V256）：从总控直接下达目标，分发到问答 / 智能体团队 */}
        <CommandBar />

        {/* 循环工程（V258，Claude Code Loop Engineering）：目标循环 / 时间循环 */}
        <LoopsPanel />

        {/* 事件自动化（V257，Quder"事件驱动"适配）：事情一发生它就动 */}
        <EventRules />

        {/* 问工作区（V255，J-lens 产品化）：把工作区状态"可读化"注入 prompt，
            由**任意已配置的模型 API** 回答——工作区帮助其他 API 智能回答。 */}
        <WorkspaceAsk />

        {/* 接入指南（V258）：其他大厂 API 一行拼接获得全局感知 */}
        <ApiGuideCard />

        {/* V274 架构顾问：输入任务→按资料 6.1/6.5 推荐单体/中心化/去中心化，讲清为什么 */}
        <ArchAdvisorCard />

        {/* 模块面板 */}
        <div className="text-[12.5px] font-bold mb-2 inline-flex items-center gap-1.5" style={{ color: "var(--text-primary)" }}>
          <Activity size={13} style={{ color: "var(--accent)" }} /> 专家模块
        </div>
        <div className="grid gap-2.5 mb-5" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" }}>
          {(snap?.modules || []).map(m => (
            <div key={m.id} className="rounded-xl px-3.5 py-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", opacity: m.active || m.events > 0 ? 1 : 0.62 }}>
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full" style={{ background: m.active ? STATE_COLOR[m.state] || "var(--accent)" : "var(--border)" }} />
                <span className="text-[12.5px] font-semibold" style={{ color: "var(--text-primary)" }}>{m.name}</span>
                <span className="ml-auto text-[10px] font-medium" style={{ color: STATE_COLOR[m.state] || "var(--text-tertiary)" }}>{STATE_LABEL[m.state] || m.state}</span>
              </div>
              <div className="text-[10.5px] mt-1 truncate" title={m.detail || m.desc} style={{ color: "var(--text-tertiary)" }}>{m.detail || m.desc}</div>
              <div className="text-[10px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>事件 {m.events} · 进意识 {m.won} · {ago(m.last_ts)}</div>
            </div>
          ))}
        </div>

        <div className="grid gap-4" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)" }}>
          {/* 意识缓冲 */}
          <div className="rounded-2xl px-4 py-3.5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="flex items-center gap-1.5 mb-1">
              <Radio size={13} style={{ color: "var(--accent)" }} />
              <span className="text-[12.5px] font-bold" style={{ color: "var(--text-primary)" }}>意识缓冲</span>
              <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>最近 {snap?.buffer?.length || 0}/7 条进入工作区被广播的事件</span>
            </div>
            {(snap?.buffer || []).slice().reverse().map(e => <EventRow key={e.id} e={e} />)}
            {!snap?.buffer?.length && <div className="text-[11.5px] py-3" style={{ color: "var(--text-tertiary)" }}>还没有广播——发起一次对话 / 深度检索 / 多智能体即见。</div>}
            <div className="flex gap-1.5 mt-2 pt-2" style={{ borderTop: "1px dashed var(--border)" }}>
              <input value={noteDraft} onChange={e => setNoteDraft(e.target.value)}
                onKeyDown={async e => { if (e.key === "Enter" && noteDraft.trim()) { await gwBroadcast(noteDraft.trim()).catch(() => {}); setNoteDraft(""); refresh(); } }}
                placeholder="手动广播一条备注 / 里程碑（回车）"
                className="flex-1 px-2.5 py-1.5 rounded-lg text-[11.5px] outline-none"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <button onClick={async () => { if (noteDraft.trim()) { await gwBroadcast(noteDraft.trim()).catch(() => {}); setNoteDraft(""); refresh(); } }}
                className="px-2.5 rounded-lg" style={{ border: "1px solid var(--border)" }} aria-label="广播">
                <Send size={12} style={{ color: "var(--accent)" }} /></button>
            </div>
          </div>

          {/* 事件史 */}
          <div className="rounded-2xl px-4 py-3.5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="text-[12.5px] font-bold mb-1" style={{ color: "var(--text-primary)" }}>
              事件史 <span className="font-normal text-[10px]" style={{ color: "var(--text-tertiary)" }}>（灰点 = 显著度不足、未进意识的"无声处理"）</span>
            </div>
            <div className="max-h-[380px] overflow-y-auto pr-1">
              {(snap?.history || []).map(e => <EventRow key={e.id} e={e} dim={!consciousIds.has(e.id) && !e.won} />)}
              {!snap?.history?.length && <div className="text-[11.5px] py-3" style={{ color: "var(--text-tertiary)" }}>暂无事件。</div>}
            </div>
          </div>
        </div>
        {!token && <div className="mt-4 text-[11px]" style={{ color: "var(--text-tertiary)" }}>提示：设定焦点 / 手动广播需要登录；只读观测无需。</div>}
      </div>
    </div>
  );
}

/** 问工作区（V255）：工作区读出（focus/意识缓冲/模块状态）注入 → 任意已配置模型作答。
 *  对标 J-lens：镜头把内部激活解码成可读文本；这里把系统内部态解码给**任何 API** 消费。 */
function WorkspaceAsk() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [ans, setAns] = useState<{ answer: string; model?: string; context_used?: string } | null>(null);
  const [errA, setErrA] = useState("");
  const [ctxOpen, setCtxOpen] = useState(false);
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [modelId, setModelId] = useState("");

  useEffect(() => {
    // 模型下拉：admin 才能列（普通用户 403 → 静默隐藏，用默认模型）
    listModels().then(ms => setModels((ms || []).map(m => ({ id: String((m as { id?: string }).id || ""), name: String((m as { name?: string }).name || "") })).filter(m => m.id))).catch(() => {});
  }, []);

  async function ask() {
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true); setErrA(""); setAns(null);
    try {
      const r = await gwAsk(question, modelId || undefined);
      if (r.ok) setAns({ answer: r.answer, model: r.model, context_used: r.context_used });
      else setErrA(r.error || r.answer || "回答失败");
    } catch (e) { setErrA((e as Error)?.message || "后端版本过旧（缺 /api/gw/ask），请升级后端"); }
    finally { setBusy(false); }
  }

  return (
    <div className="rounded-2xl px-5 py-4 mb-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 mb-1">
        <MessageCircleQuestion size={14} style={{ color: "var(--accent)" }} />
        <span className="text-[12.5px] font-bold" style={{ color: "var(--text-primary)" }}>问工作区</span>
        <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
          工作区读出会注入提问——接入的**任意模型 API** 都能借此感知全局、智能回答
        </span>
      </div>
      <div className="flex gap-2 mt-2">
        <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === "Enter") ask(); }}
          placeholder="例：系统现在在忙什么？刚才深度检索查到了什么？下一步该干嘛？"
          className="flex-1 px-3 py-2 rounded-xl text-[12.5px] outline-none"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        {models.length > 0 && (
          <select value={modelId} onChange={e => setModelId(e.target.value)} aria-label="选择回答模型"
            className="px-2 py-2 rounded-xl text-[11.5px] outline-none max-w-[130px]"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            <option value="">默认模型</option>
            {models.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        )}
        <button onClick={ask} disabled={busy || !q.trim()}
          className="px-3.5 py-2 rounded-xl text-[12px] font-semibold text-white disabled:opacity-60" style={{ background: "var(--accent)" }}>
          {busy ? <Loader2 size={13} className="animate-spin" /> : "提问"}
        </button>
      </div>
      {errA && <div className="mt-2 text-[11.5px]" style={{ color: "#b42318" }}>{errA}</div>}
      {ans && (
        <div className="mt-3 rounded-xl px-3.5 py-3" style={{ background: "var(--bg-primary)", borderLeft: "3px solid var(--accent)", border: "1px solid var(--border)" }}>
          <div className="text-[10.5px] mb-1" style={{ color: "var(--text-tertiary)" }}>由 {ans.model || "默认模型"} 基于工作区读出回答</div>
          <div className="text-[13px] whitespace-pre-wrap leading-relaxed" style={{ color: "var(--text-primary)" }}>{ans.answer}</div>
          {ans.context_used && (
            <button onClick={() => setCtxOpen(v => !v)} className="mt-2 inline-flex items-center gap-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              <ChevronDown size={11} style={{ transform: ctxOpen ? "rotate(180deg)" : "none" }} /> 查看注入的工作区读出
            </button>
          )}
          {ctxOpen && ans.context_used && (
            <pre className="mt-1.5 px-2.5 py-2 rounded-lg text-[10.5px] whitespace-pre-wrap"
              style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>{ans.context_used}</pre>
          )}
        </div>
      )}
    </div>
  );
}


/** 系统体检条（V256）：后端版本 / 深度检索档位 / 高级能力开启数——总控一眼掌握全局健康。 */
function SystemPulse() {
  const [h, setH] = useState<{ release?: string; features?: { advanced_on_count?: number; advanced_total?: number } } | null>(null);
  const [ds, setDs] = useState<{ available: boolean; full_model_dir: boolean; lite: boolean } | null>(null);
  useEffect(() => {
    getHealth().then(setH).catch(() => {});
    deepSearchStatus().then(setDs).catch(() => {});
  }, []);
  const chip = (label: string, ok: boolean | null, detail?: string) => (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px]"
      style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: ok === null ? "var(--text-tertiary)" : ok ? "#16a34a" : "#dc2626" }} />
      {label}{detail ? ` · ${detail}` : ""}
    </span>
  );
  return (
    <div className="rounded-2xl px-5 py-3 mb-4 flex items-center gap-2 flex-wrap" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <HeartPulse size={14} style={{ color: "var(--accent)" }} />
      <span className="text-[12.5px] font-bold mr-1" style={{ color: "var(--text-primary)" }}>系统体检</span>
      {chip("后端", !!h, h?.release || "连接中")}
      {chip("深度检索", ds ? ds.available : null, ds ? (ds.full_model_dir ? "full" : ds.lite ? "lite" : "不可用") : undefined)}
      {chip("高级能力", h?.features ? (h.features.advanced_on_count || 0) > 0 : null,
        h?.features ? `${h.features.advanced_on_count ?? 0}/${h.features.advanced_total ?? 0}` : undefined)}
    </div>
  );
}

/** 指挥台（V256）：一个目标，从总控分发出去——交给问答（回填聊天框）或组建智能体团队。 */
function CommandBar() {
  const set = useStore(s => s.set);
  const [goal, setGoal] = useState("");
  const [sent, setSent] = useState("");
  function toChat() {
    const g = goal.trim(); if (!g) return;
    set({ pendingPrompt: g });
    setSent("已填入聊天框——回到会话即可发送"); setTimeout(() => setSent(""), 2500);
  }
  function toTeam() {
    const g = goal.trim(); if (!g) return;
    set({ pendingPrompt: g, desktopView: "agents" });
    setSent(""); // 直接跳走
  }
  return (
    <div className="rounded-2xl px-5 py-4 mb-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 mb-2">
        <Rocket size={14} style={{ color: "var(--accent)" }} />
        <span className="text-[12.5px] font-bold" style={{ color: "var(--text-primary)" }}>指挥台</span>
        <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>从总控下达一个目标，分发给合适的执行方式</span>
      </div>
      <div className="flex gap-2">
        <input value={goal} onChange={e => setGoal(e.target.value)} onKeyDown={e => { if (e.key === "Enter") toChat(); }}
          placeholder="例：把上季度销售数据做成分析报告"
          className="flex-1 px-3 py-2 rounded-xl text-[12.5px] outline-none"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        <button onClick={toChat} disabled={!goal.trim()}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-semibold disabled:opacity-50"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <MessageSquare size={12} /> 交给问答</button>
        <button onClick={toTeam} disabled={!goal.trim()}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-semibold text-white disabled:opacity-50"
          style={{ background: "var(--accent)" }}>
          <Users size={12} /> 组建团队</button>
      </div>
      {sent && <div className="mt-2 text-[11px]" style={{ color: "#15803d" }}>{sent}</div>}
    </div>
  );
}


/** 事件自动化（V257）：任务跑完/跑出错，系统自己知道并推进通知——不用盯着、不用轮询。 */
function EventRules() {
  const [rules, setRules] = useState<EventRule[]>([]);
  const [miss, setMiss] = useState(false);
  useEffect(() => { gwRules().then(r => setRules(r.rules || [])).catch(() => setMiss(true)); }, []);
  if (miss || !rules.length) return null;
  return (
    <div className="rounded-2xl px-5 py-3.5 mb-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 mb-2">
        <Zap size={14} style={{ color: "var(--accent)" }} />
        <span className="text-[12.5px] font-bold" style={{ color: "var(--text-primary)" }}>事件自动化</span>
        <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>任务完成/失败自动推通知——事情一发生它就动，不用盯着</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {rules.map(r => (
          <button key={r.id}
            onClick={() => { const on = !r.on; setRules(rs => rs.map(x => x.id === r.id ? { ...x, on } : x)); gwRuleSet(r.id, on).catch(() => {}); }}
            className="px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors"
            style={{ background: r.on ? "var(--accent-light)" : "var(--bg-primary)", color: r.on ? "var(--accent)" : "var(--text-tertiary)", border: "1px solid " + (r.on ? "var(--accent)" : "var(--border)") }}>
            {r.name}{r.on ? "" : "（已关）"}
          </button>
        ))}
      </div>
    </div>
  );
}


/** 接入其他大厂 API（V258）：J-lens 读出的 OpenAI 兼容接入姿势——一行拼接即用。 */
function ApiGuideCard() {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-2xl px-5 py-3 mb-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <button onClick={() => setOpen(v => !v)} className="inline-flex items-center gap-1.5 text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
        <PlugZap size={13} style={{ color: "var(--accent)" }} /> 接入其他大厂 API
        <span className="text-[10.5px] font-normal" style={{ color: "var(--text-tertiary)" }}>OpenAI / Qwen / GLM / Kimi / Doubao / DeepSeek…</span>
        <ChevronDown size={12} style={{ transform: open ? "rotate(180deg)" : "none", color: "var(--text-tertiary)" }} />
      </button>
      {open && (
        <pre className="mt-2 px-3 py-2.5 rounded-lg text-[10.5px] leading-relaxed whitespace-pre-wrap"
          style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
{`任何 OpenAI 兼容的 chat.completions 接口，两步获得本系统的全局感知：

1. ctx = GET /api/gw/context?format=system      ← 返回一条 system 消息对象
2. body.messages = [ctx, ...原有 messages]       ← 拼到最前面即可

该 API 的回答从此知道：系统焦点是什么、各模块在忙什么、最近发生了什么。
format=text 返回纯文本（自拼 prompt）；format=messages 返回整段 messages。
在系统内配置的模型（「模型/后端」）则由 /api/gw/ask 自动完成注入，无需手工拼接。`}
        </pre>
      )}
    </div>
  );
}

/** V335 可恢复长任务：真实 AgentLoop、工具证据、预算、暂停/恢复与崩溃恢复。 */
function LoopsPanel() {
  const sid = useStore(s => s.sid);
  const [loops, setLoops] = useState<LoopInfo[]>([]);
  const [tab, setTab] = useState<"goal" | "interval">("goal");
  const [goal, setGoal] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [rounds, setRounds] = useState(4);
  const [mode, setMode] = useState<"read_only" | "workspace">("read_only");
  const [networkMode, setNetworkMode] = useState<"deny" | "allow" | "allowlist">("deny");
  const [allowedOrigins, setAllowedOrigins] = useState("");
  const [allowSubagents, setAllowSubagents] = useState(false);
  const [minutes, setMinutes] = useState(60);
  const [tokenK, setTokenK] = useState(50);
  const [prompt, setPrompt] = useState("");
  const [every, setEvery] = useState(30);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [miss, setMiss] = useState(false);
  const [openId, setOpenId] = useState("");
  const [acceptanceNotes, setAcceptanceNotes] = useState<Record<string, string>>({});
  const [actionId, setActionId] = useState("");

  const pull = () => loopsList().then(r => setLoops(r.loops || []));

  useEffect(() => {
    let alive = true;
    const refresh = () => loopsList().then(r => { if (alive) setLoops(r.loops || []); }).catch(() => { if (alive) setMiss(true); });
    refresh();
    const t = setInterval(refresh, 4000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (miss) return null;

  async function create() {
    setErr(""); setBusy(true);
    try {
      if (tab === "goal") {
        if (!goal.trim()) { setErr("先写清目标（例：产出一份 90 分以上的产品对比报告）"); return; }
        await loopGoal({
          goal: goal.trim(), acceptance: acceptance.trim() || undefined, max_rounds: rounds,
          conv_id: sid || undefined, approval_mode: mode, max_tokens: tokenK * 1000,
          max_seconds: minutes * 60,
          network_mode: networkMode,
          allowed_origins: networkMode === "allowlist" ? allowedOrigins.split(/[\s,，]+/).filter(Boolean) : [],
          allow_subagents: allowSubagents,
        });
        setGoal("");
        setAcceptance("");
      } else {
        if (!prompt.trim()) { setErr("先写清巡检任务"); return; }
        await loopInterval({
          prompt: prompt.trim(), interval_min: every, conv_id: sid || undefined,
          approval_mode: mode, network_mode: networkMode,
          allowed_origins: networkMode === "allowlist" ? allowedOrigins.split(/[\s,，]+/).filter(Boolean) : [],
          allow_subagents: allowSubagents,
        });
        setPrompt("");
      }
      const r = await loopsList(); setLoops(r.loops || []);
    } catch (e) { setErr((e as Error)?.message || "创建失败（并发上限 5）"); }
    finally { setBusy(false); }
  }

  async function control(l: LoopInfo, action: "pause" | "resume" | "stop") {
    setActionId(l.id); setErr("");
    try {
      if (action === "pause") await loopPause(l.id);
      else if (action === "resume") await loopResume(l.id);
      else await loopStop(l.id);
      await pull();
    } catch (e) { setErr((e as Error)?.message || "操作失败"); }
    finally { setActionId(""); }
  }

  async function confirmDelivery(l: LoopInfo, accepted: boolean) {
    setActionId(l.id); setErr("");
    try {
      await loopAcceptance(l.id, accepted, acceptanceNotes[l.id] || "");
      setAcceptanceNotes(current => ({ ...current, [l.id]: "" }));
      await pull();
    } catch (e) { setErr((e as Error)?.message || "验收记录失败"); }
    finally { setActionId(""); }
  }

  const stateColor = (s: string) => s === "running" ? "#b45309" : s === "queued" ? "#2563eb" : s === "done" ? "#15803d" : s === "fail" ? "#b42318" : "var(--text-tertiary)";
  const stateName = (s: string) => ({ queued: "排队中", running: "运行中", paused: "已暂停", done: "已达成", fail: "未达成", stopped: "已停止" } as Record<string, string>)[s] || s;
  const tokenText = (n?: number) => n == null ? "0" : n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);

  return (
    <div className="rounded-2xl px-5 py-4 mb-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 mb-2">
        <RefreshCcw size={14} style={{ color: "var(--accent)" }} />
        <span className="text-[12.5px] font-bold" style={{ color: "var(--text-primary)" }}>可恢复长任务</span>
        <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
          真正调用 RAG 与工具 · 独立证据验收 · 重启不丢进度
        </span>
      </div>
      <div className="flex items-center gap-1.5 mb-2">
        <button onClick={() => setTab("goal")} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11.5px] font-semibold"
          style={{ background: tab === "goal" ? "var(--accent)" : "var(--bg-primary)", color: tab === "goal" ? "#fff" : "var(--text-secondary)", border: "1px solid " + (tab === "goal" ? "var(--accent)" : "var(--border)") }}>
          <Target size={11} /> 目标循环</button>
        <button onClick={() => setTab("interval")} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11.5px] font-semibold"
          style={{ background: tab === "interval" ? "var(--accent)" : "var(--bg-primary)", color: tab === "interval" ? "#fff" : "var(--text-secondary)", border: "1px solid " + (tab === "interval" ? "var(--accent)" : "var(--border)") }}>
          <Timer size={11} /> 时间循环</button>
        <span className="flex-1" />
        <ShieldCheck size={12} style={{ color: mode === "read_only" ? "#15803d" : "#b45309" }} />
        <select value={mode} onChange={e => setMode(e.target.value as "read_only" | "workspace")} aria-label="任务授权模式"
          className="px-2 py-1 rounded-lg text-[11px] outline-none"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <option value="read_only">只读安全（推荐）</option>
          <option value="workspace">允许工作区写入</option>
        </select>
      </div>
      <div className="mb-2 text-[10.5px] leading-relaxed" style={{ color: mode === "workspace" ? "#b45309" : "var(--text-tertiary)" }}>
        {mode === "read_only"
          ? "只读任务可检索知识库和读取文件；是否联网、是否派生专员由下面的任务范围单独控制。"
          : "写入模式可创建/修改工作区内容；若服务在执行中重启，任务会暂停并等你确认恢复，避免重复副作用。"}
      </div>
      <div className="mb-2 rounded-xl px-3 py-2 flex flex-wrap items-center gap-2" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
        <span className="text-[10.5px] font-semibold" style={{ color: "var(--text-secondary)" }}>本任务范围</span>
        <select value={networkMode} onChange={e => setNetworkMode(e.target.value as "deny" | "allow" | "allowlist")} aria-label="任务联网范围"
          className="px-2 py-1 rounded-lg text-[11px] outline-none"
          style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <option value="deny">不联网（推荐）</option>
          <option value="allowlist">仅指定站点</option>
          <option value="allow">允许公开网络</option>
        </select>
        <label className="inline-flex items-center gap-1.5 text-[11px] cursor-pointer" style={{ color: "var(--text-secondary)" }}>
          <input type="checkbox" checked={allowSubagents} onChange={e => setAllowSubagents(e.target.checked)} />
          允许按需派生专员
        </label>
        <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>权限随任务持久化，子 Agent 只能继承更窄范围</span>
      </div>
      {networkMode === "allowlist" && (
        <input value={allowedOrigins} onChange={e => setAllowedOrigins(e.target.value)}
          placeholder="允许站点，例如 https://openai.com, https://docs.example.com"
          aria-label="允许联网的站点"
          className="w-full mb-2 px-3 py-1.5 rounded-lg text-[11.5px] outline-none"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
      )}
      {tab === "goal" ? (
        <div className="space-y-2">
          <div className="flex gap-2 items-center">
            <input value={goal} onChange={e => setGoal(e.target.value)} onKeyDown={e => { if (e.key === "Enter") create(); }}
              placeholder="目标（例：核实三份资料并生成有来源的竞品报告）"
              className="flex-1 px-3 py-2 rounded-xl text-[12px] outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            <button onClick={create} disabled={busy} className="px-4 py-2 rounded-xl text-[12px] font-semibold text-white disabled:opacity-60" style={{ background: "var(--accent)" }}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : "启动任务"}</button>
          </div>
          <div className="flex gap-2 items-center">
            <input value={acceptance} onChange={e => setAcceptance(e.target.value)}
              placeholder="可验收条件（可选，例如：含 3 个官方来源、生成 report.md、测试全部通过）"
              className="flex-1 px-3 py-1.5 rounded-lg text-[11.5px] outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            <label className="text-[10.5px] whitespace-nowrap" style={{ color: "var(--text-tertiary)" }}>轮数
              <input type="number" min={1} max={8} value={rounds} onChange={e => setRounds(Math.max(1, Math.min(8, Number(e.target.value) || 4)))} aria-label="最大轮数"
                className="w-11 ml-1 px-1 py-1 rounded-lg text-[11px] outline-none text-center" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} /></label>
            <label className="text-[10.5px] whitespace-nowrap" style={{ color: "var(--text-tertiary)" }}>分钟
              <input type="number" min={1} max={2880} value={minutes} onChange={e => setMinutes(Math.max(1, Math.min(2880, Number(e.target.value) || 60)))} aria-label="时间预算（分钟）"
                className="w-14 ml-1 px-1 py-1 rounded-lg text-[11px] outline-none text-center" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} /></label>
            <label className="text-[10.5px] whitespace-nowrap" style={{ color: "var(--text-tertiary)" }}>Token(k)
              <input type="number" min={2} max={500} value={tokenK} onChange={e => setTokenK(Math.max(2, Math.min(500, Number(e.target.value) || 50)))} aria-label="Token 预算（千）"
                className="w-14 ml-1 px-1 py-1 rounded-lg text-[11px] outline-none text-center" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} /></label>
          </div>
        </div>
      ) : (
        <div className="flex gap-2 items-center">
          <input value={prompt} onChange={e => setPrompt(e.target.value)} onKeyDown={e => { if (e.key === "Enter") create(); }}
            placeholder="巡检任务（例：检查知识库最新文档有无互相矛盾的结论）"
            className="flex-1 px-3 py-2 rounded-xl text-[12px] outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <label className="text-[11px] whitespace-nowrap" style={{ color: "var(--text-tertiary)" }}>每
            <input type="number" min={5} max={720} value={every} onChange={e => setEvery(Math.max(5, Number(e.target.value) || 30))} aria-label="间隔分钟"
              className="w-14 mx-1 px-1.5 py-1.5 rounded-lg text-[11.5px] outline-none text-center" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} /> 分钟</label>
          <button onClick={create} disabled={busy} className="px-3 py-2 rounded-xl text-[12px] font-semibold text-white disabled:opacity-60" style={{ background: "var(--accent)" }}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : "启动"}</button>
        </div>
      )}
      {err && <div className="mt-1.5 text-[11.5px]" style={{ color: "#b42318" }}>{err}</div>}
      {loops.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {loops.slice(0, 6).map(l => (
            <div key={l.id} className="rounded-xl px-3 py-2" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2">
                {l.type === "goal" ? <Target size={12} style={{ color: "var(--text-tertiary)" }} /> : <Timer size={12} style={{ color: "var(--text-tertiary)" }} />}
                <button onClick={() => setOpenId(openId === l.id ? "" : l.id)} className="text-[12px] flex-1 truncate text-left" style={{ color: "var(--text-primary)" }}>
                  {l.goal || l.prompt}</button>
                <span className="text-[10.5px] whitespace-nowrap" style={{ color: "var(--text-tertiary)" }}>
                  {l.type === "goal" ? `${l.rounds ?? 0}/${l.max_rounds} 轮 · ${l.score ?? 0} 分` : `${l.runs ?? 0}/${l.max_runs} 次 · 每 ${l.interval_min} 分`}</span>
                <span className="text-[10px] whitespace-nowrap" title="该任务累计 Token / 预算" style={{ color: "var(--text-tertiary)" }}>
                  {tokenText(l.tokens_used)}/{tokenText(l.max_tokens)} tok</span>
                <span className="text-[10.5px] font-semibold whitespace-nowrap" style={{ color: stateColor(l.status) }}>{stateName(l.status)}</span>
                {actionId === l.id && <Loader2 size={13} className="animate-spin" style={{ color: "var(--text-tertiary)" }} />}
                {actionId !== l.id && (l.status === "running" || l.status === "queued") && <>
                  <button onClick={() => control(l, "pause")} title="暂停并保留进度" aria-label="暂停循环"
                    className="p-0.5 rounded" style={{ color: "#b45309" }}><PauseCircle size={14} /></button>
                  <button onClick={() => control(l, "stop")} title="停止" aria-label="停止循环"
                    className="p-0.5 rounded" style={{ color: "#b42318" }}><StopCircle size={14} /></button>
                </>}
                {actionId !== l.id && (l.status === "paused" || l.status === "stopped") && (
                  <button onClick={() => control(l, "resume")} title="从已保存进度恢复" aria-label="恢复循环"
                    className="p-0.5 rounded" style={{ color: "#15803d" }}><PlayCircle size={14} /></button>
                )}
              </div>
              {l.recovered && (
                <div className="mt-1 text-[10px]" style={{ color: l.status === "paused" ? "#b45309" : "#2563eb" }}>
                  已从上次服务状态恢复{l.status === "paused" ? "；写入任务需确认后继续" : "并继续执行"}
                </div>
              )}
              {l.work_runtime_state === "degraded" && (
                <div className="mt-1 text-[10px] leading-relaxed" style={{ color: "#b45309" }}>
                  跨端工作记录暂未同步：{l.work_runtime_error || "下一次状态更新时会重试"}
                </div>
              )}
              {l.work_runtime_state === "linked" && l.work_run_id && (
                <div className="mt-1 text-[10px]" style={{ color: "#15803d" }}>
                  已接入统一工作记录，桌面端与 App 可继续查看进度
                </div>
              )}
              {openId === l.id && (
                <div className="mt-1.5 pt-1.5 text-[11px] space-y-1" style={{ borderTop: "1px dashed var(--border)", color: "var(--text-secondary)" }}>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                    <span>权限：{l.approval_mode === "workspace" ? "工作区写入" : "只读安全"}</span>
                    <span>联网：{l.execution_scope?.network?.mode === "allow" ? "公开网络" : l.execution_scope?.network?.mode === "allowlist" ? `指定站点 ${l.execution_scope.network.origins.length} 个` : "关闭"}</span>
                    <span>专员：{l.execution_scope?.allow_subagents ? "按需启用" : "关闭"}</span>
                    <span>工具范围：{l.execution_scope?.allowed_tools?.length ?? 0} 个</span>
                    <span>活跃耗时：{Math.round(l.active_seconds || 0)}s / {Math.round((l.max_seconds || 0) / 60)}m</span>
                    <span>停止原因：{l.stop_reason || "—"}</span>
                    {l.type === "goal" && <span style={{ color: l.verified ? "#15803d" : "#b45309" }}>证据校验：{l.verified ? "通过" : "未通过"}</span>}
                  </div>
                  {l.completion_gate ? <div className="rounded-lg px-2.5 py-2" style={{ background: "var(--bg-secondary)", border: `1px solid ${l.completion_gate.can_claim_complete ? "rgba(21,128,61,.2)" : l.completion_gate.status === "delivered_with_limits" ? "rgba(180,83,9,.2)" : "rgba(180,35,24,.2)"}` }}>
                    <div className="flex items-center gap-1.5 text-[10.5px]">
                      {l.completion_gate.can_claim_complete ? <CheckCircle2 size={11} style={{ color: "#15803d" }} /> : <AlertTriangle size={11} style={{ color: l.completion_gate.status === "delivered_with_limits" ? "#b45309" : "#b42318" }} />}
                      <span className="font-medium" style={{ color: "var(--text-primary)" }}>完成门</span>
                      <span className="ml-auto font-mono" style={{ color: l.completion_gate.can_claim_complete ? "#15803d" : "var(--text-tertiary)" }}>
                        {l.completion_gate.can_claim_complete ? "已验证" : l.completion_gate.status === "delivered_with_limits" ? "待复核" : "未闭环"}
                      </span>
                    </div>
                    <div className="mt-1 text-[10px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                      {l.completion_gate.summary.passed}/{l.completion_gate.summary.required} 必需条件通过 · {l.completion_gate.next_action}
                    </div>
                    {l.type === "goal" && l.status === "done" && l.completion_gate.criteria?.some(c => c.check_id === "user_acceptance" && c.status !== "passed") ? (
                      <div className="mt-2 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
                        <div className="text-[10px] mb-1.5" style={{ color: "var(--text-tertiary)" }}>
                          请按你写下的完成标准检查交付。接受或退回都将作为可审计证据保存，不会自动扩大权限。
                        </div>
                        <div className="flex items-center gap-1.5">
                          <input
                            value={acceptanceNotes[l.id] || ""}
                            onChange={e => setAcceptanceNotes(current => ({ ...current, [l.id]: e.target.value }))}
                            maxLength={500}
                            placeholder="验收说明（退回时建议填写）"
                            className="min-w-0 flex-1 px-2 py-1.5 rounded-lg text-[10.5px] outline-none"
                            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                          />
                          <button disabled={actionId === l.id} onClick={() => confirmDelivery(l, false)}
                            className="px-2 py-1.5 rounded-lg text-[10.5px] font-medium disabled:opacity-50"
                            style={{ background: "rgba(180,35,24,.06)", border: "1px solid rgba(180,35,24,.16)", color: "#b42318" }}>
                            退回复核
                          </button>
                          <button disabled={actionId === l.id} onClick={() => confirmDelivery(l, true)}
                            className="px-2 py-1.5 rounded-lg text-[10.5px] font-medium disabled:opacity-50"
                            style={{ background: "rgba(21,128,61,.08)", border: "1px solid rgba(21,128,61,.18)", color: "#15803d" }}>
                            接受交付
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </div> : null}
                  {l.evidence_graph ? <div className="rounded-lg px-2.5 py-2" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                    <div className="flex items-center gap-1.5 text-[10.5px]">
                      <Network size={11} style={{ color: l.evidence_graph.status === "blocked" ? "#b42318" : "var(--accent)" }} />
                      <span className="font-medium" style={{ color: "var(--text-primary)" }}>任务证据图</span>
                      <span className="ml-auto font-mono" style={{ color: l.evidence_graph.summary.blockers ? "#b42318" : "var(--text-tertiary)" }}>
                        {l.evidence_graph.summary.nodes} 节点 · {l.evidence_graph.summary.edges} 连接 · {l.evidence_graph.summary.blockers} 阻塞
                      </span>
                    </div>
                    {l.evidence_graph.summary.blockers > 0 && l.evidence_graph.next_actions?.[0] ? <div className="mt-1 text-[10px] leading-relaxed" style={{ color: "#b45309" }}>下一轮优先：{l.evidence_graph.next_actions[0]}</div> : null}
                  </div> : null}
                  {l.causal_work_graph ? <div className="rounded-lg px-2.5 py-2" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                    <div className="flex items-center gap-1.5 text-[10.5px]">
                      <Network size={11} style={{ color: l.causal_work_graph.status === "ready" ? "#15803d" : l.causal_work_graph.status === "stale" ? "#b45309" : "#b42318" }} />
                      <span className="font-medium" style={{ color: "var(--text-primary)" }}>因果工作图</span>
                      <span className="ml-auto font-mono" style={{ color: "var(--text-tertiary)" }}>
                        第 {Math.max(1, ...l.causal_work_graph.nodes.map(n => n.revision || 1))} 代 · {l.causal_work_graph.summary.receipts} 回执
                      </span>
                    </div>
                    <div className="mt-1 text-[10px] leading-relaxed" style={{ color: l.causal_work_graph.status === "ready" ? "var(--text-secondary)" : "#b45309" }}>
                      {l.causal_work_graph.summary.nodes} 节点 · {l.causal_work_graph.summary.stale_nodes} 过期 · {l.causal_work_graph.summary.invalid_receipts} 无效回执
                    </div>
                  </div> : null}
                  {l.execution_frontier ? <div className="rounded-lg px-2.5 py-2" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                    <div className="flex items-center gap-1.5 text-[10.5px]">
                      <Activity size={11} style={{ color: l.execution_frontier.summary.scope_blocked ? "#b45309" : "var(--accent)" }} />
                      <span className="font-medium" style={{ color: "var(--text-primary)" }}>下一工作集</span>
                      <span className="ml-auto font-mono" style={{ color: l.execution_frontier.summary.scope_blocked ? "#b42318" : "var(--text-tertiary)" }}>
                        {l.execution_frontier.summary.ready_routes} 可行 · {l.execution_frontier.summary.scope_blocked} 权限受限
                      </span>
                    </div>
                    {l.execution_frontier.items?.[0]?.minimum_action ? <div className="mt-1 text-[10px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>优先动作：{l.execution_frontier.items[0].minimum_action}</div> : null}
                    <div className="mt-1 text-[9px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>路线可用不等于已批准或已执行。</div>
                  </div> : null}
                  {(l.history || []).slice(-5).map((h, i) => (
                    <div key={i}>{l.type === "goal" ? `第 ${h.round} 轮 · ${h.score} 分 · ${h.verified ? "证据通过" : "证据不足"}` : `第 ${h.run} 次`}：{h.note || ""}</div>
                  ))}
                  {(l.task_contract?.success_criteria || []).length > 0 && (
                    <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                      完成标准：{(l.task_contract?.success_criteria || []).map(c => c.label).join("；")}
                    </div>
                  )}
                  {(l.verification?.checks || []).length > 0 && (
                    <div className="flex flex-wrap gap-1 pt-0.5">
                      {(l.verification?.checks || []).map(c => (
                        <span key={c.check_id} title={c.detail} className="px-1.5 py-0.5 rounded-md text-[9.5px]"
                          style={{
                            background: c.status === "passed" ? "rgba(21,128,61,.08)" : c.status === "failed" ? "rgba(180,35,24,.08)" : "var(--bg-tertiary)",
                            color: c.status === "passed" ? "#15803d" : c.status === "failed" ? "#b42318" : "var(--text-tertiary)",
                          }}>
                          {c.status === "passed" ? "已验证" : c.status === "failed" ? "未通过" : "待核验"} · {c.label}
                        </span>
                      ))}
                    </div>
                  )}
                  {(l.tools || []).length > 0 && <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                    工具证据：{(l.tools || []).slice(-8).map(t => `${t.name}(${t.status})`).join("、")}
                  </div>}
                  {(l.files || []).length > 0 && <div className="text-[10.5px]" style={{ color: "#15803d" }}>
                    产出文件：{(l.files || []).map(f => f.filename).filter(Boolean).join("、")}
                  </div>}
                  {(l.trace || []).length > 0 && <div className="max-h-[90px] overflow-y-auto text-[10px] space-y-0.5" style={{ color: "var(--text-tertiary)" }}>
                    {(l.trace || []).slice(-8).map((t, i) => <div key={i}>· {t.kind === "tool" ? `${t.name} · ${t.status}` : `${t.node || "进度"} · ${t.detail || ""}`}</div>)}
                  </div>}
                  {l.result && <div className="whitespace-pre-wrap max-h-[160px] overflow-y-auto pt-1" style={{ color: "var(--text-primary)" }}>{l.result.slice(0, 800)}</div>}
                  {l.last && <div className="whitespace-pre-wrap max-h-[120px] overflow-y-auto pt-1" style={{ color: "var(--text-primary)" }}>{l.last.slice(0, 800)}</div>}
                  {l.error && <div style={{ color: "#b42318" }}>运行错误：{l.error}</div>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* V274 架构顾问卡：多 Agent 不一定更好——先问"这个任务该用什么架构"（资料 6.1/6.2/6.5）。 */
function ArchAdvisorCard() {
  const [task, setTask] = useState("");
  const [adv, setAdv] = useState<ArchAdvice | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!task.trim() || busy) return;
    setBusy(true);
    try { setAdv(await archAdvise(task.trim())); } catch (e) { setAdv({ ok: false, detail: (e as Error).message }); }
    finally { setBusy(false); }
  };
  const COLOR: Record<string, string> = { SAS: "#16a34a", MAS_CENTRAL: "#2563eb", MAS_DECENTRAL: "#9333ea", MAS_INDEP: "#d97706" };
  return (
    <div className="rounded-2xl px-4 py-3.5 mb-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-1.5 mb-1">
        <Network size={13} style={{ color: "var(--accent)" }} />
        <span className="text-[12.5px] font-bold" style={{ color: "var(--text-primary)" }}>架构顾问</span>
        <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>多 Agent 不一定更好 · 按任务选架构</span>
      </div>
      <div className="flex gap-2 mt-2">
        <input value={task} onChange={e => setTask(e.target.value)} onKeyDown={e => { if (e.key === "Enter") run(); }}
          placeholder="描述你的任务，如：对账三个部门财务并统一口径分析"
          className="flex-1 px-3 py-1.5 rounded-lg text-[12px] outline-none"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        <button onClick={run} disabled={busy}
          className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-white disabled:opacity-60"
          style={{ background: "var(--accent)" }}>{busy ? "分析中" : "推荐"}</button>
      </div>
      {adv && adv.ok && (
        <div className="mt-3 rounded-xl p-3" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2 mb-1.5">
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold text-white" style={{ background: COLOR[adv.arch || "SAS"] || "var(--accent)" }}>{adv.arch_name}</span>
            <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>建议 {adv.n_agents} 个 Agent · 置信 {adv.confidence}</span>
          </div>
          <div className="text-[12px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>{adv.reason}</div>
          {adv.metrics && (
            <div className="text-[10.5px] mt-2 flex flex-wrap gap-x-4 gap-y-1" style={{ color: "var(--text-tertiary)" }}>
              <span>LLM 调用 {adv.metrics.llm}</span><span>通信 {adv.metrics.comm}</span>
              <span>并行 {adv.metrics.parallel}</span><span>错误放大 {adv.metrics.amplify}</span>
            </div>
          )}
          {adv.alternatives && adv.alternatives.length > 0 && (
            <div className="text-[10.5px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>备选：{adv.alternatives.join("、")}</div>
          )}
        </div>
      )}
      {adv && !adv.ok && <div className="text-[11.5px] mt-2" style={{ color: "#b42318" }}>{adv.detail}</div>}
    </div>
  );
}
