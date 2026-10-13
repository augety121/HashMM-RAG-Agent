"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  CircleStop,
  Eye,
  History,
  Loader2,
  MessageSquareText,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import {
  agentRouteGet,
  agentRouteSet,
  createConversation,
  teamAgents,
  teamList,
  teamPreviewV2,
  teamRetry,
  teamStart,
  teamStatus,
  teamStop,
  type AgentInfo,
  type TeamRole,
  type TeamStatus,
} from "@/lib/api";
import { openArtifact } from "@/lib/artifact";
import { useStore } from "@/lib/store";
import { Badge, PageHeader } from "./ui/PanelKit";

type Phase = "brief" | "review" | "running" | "done";

const EXAMPLES = [
  "比较三个方案，给出有依据的推荐",
  "阅读这些资料，整理成一份可交付报告",
  "调研一个主题，核验事实后形成结论",
];

function memberInitial(name: string) {
  return (name || "协").slice(0, 1);
}

function stateLabel(state?: string) {
  if (state === "run") return "正在处理";
  if (state === "ok") return "已完成";
  if (state === "fail") return "需要处理";
  if (state === "stop") return "已停止";
  return "等待开始";
}

function statusLabel(status: TeamStatus["status"]) {
  if (status === "done") return "已完成";
  if (status === "running") return "进行中";
  if (status === "stopping") return "正在停止";
  if (status === "stopped") return "已停止";
  return "需要处理";
}

function MemberMark({ name, active = false }: { name: string; active?: boolean }) {
  return (
    <span
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-[12px] font-bold"
      style={{
        background: active ? "var(--accent)" : "var(--accent-light)",
        color: active ? "#fff" : "var(--accent)",
      }}
    >
      {memberInitial(name)}
    </span>
  );
}

export function AgentsStudioView() {
  const sid = useStore(s => s.sid);
  const set = useStore(s => s.set);
  const addSession = useStore(s => s.addSession);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentQuery, setAgentQuery] = useState("");
  const [agentCategory, setAgentCategory] = useState("all");
  const [mode, setMode] = useState<"auto" | "manual">("auto");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [goal, setGoal] = useState("");
  const [outcome, setOutcome] = useState("");
  const [criteria, setCriteria] = useState("");
  const [roles, setRoles] = useState<TeamRole[] | null>(null);
  const [phase, setPhase] = useState<Phase>("brief");
  const [execMode, setExecMode] = useState<"parallel" | "pipeline">("parallel");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [status, setStatus] = useState<TeamStatus | null>(null);
  const [history, setHistory] = useState<TeamStatus[]>([]);
  const [routeMode, setRouteMode] = useState("");
  const [routeSaved, setRouteSaved] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    teamAgents()
      .then(r => setAgents(r.agents || []))
      .catch(() => setErr("暂时无法读取可用协作者，请检查连接后重试。"));
    teamList().then(r => setHistory(r.items || [])).catch(() => {});
    agentRouteGet().then(r => setRouteMode(r.mode || "auto")).catch(() => setRouteMode("auto"));
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function composedGoal() {
    return [
      goal.trim(),
      outcome.trim() ? `期望结果：${outcome.trim()}` : "",
      criteria.trim() ? `完成标准：${criteria.trim()}` : "",
    ].filter(Boolean).join("\n");
  }

  function toggleAgent(id: string) {
    setPicked(previous => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else if (next.size < 4) next.add(id);
      return next;
    });
  }

  async function saveRoute(value: string) {
    setRouteMode(value);
    try {
      await agentRouteSet(value);
      setRouteSaved(true);
      setTimeout(() => setRouteSaved(false), 1500);
    } catch {
      setErr("协作偏好没有保存，请稍后重试。");
    }
  }

  async function preview() {
    if (!goal.trim()) {
      setErr("先写下这次协作要解决的问题。");
      return;
    }
    if (mode === "manual" && picked.size < 2) {
      setErr("请至少选择两位协作者。");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const response = await teamPreviewV2(
        composedGoal(),
        mode === "manual" ? Array.from(picked) : undefined,
      );
      setRoles((response.roles || []).map(role => ({ ...role })));
      setPhase("review");
    } catch (error) {
      setErr(`暂时无法安排协作：${(error as Error)?.message || "服务不可用"}`);
    } finally {
      setBusy(false);
    }
  }

  function watch(teamId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const next = await teamStatus(teamId);
        setStatus(next);
        if (next.status !== "running" && next.status !== "stopping") {
          if (pollRef.current) clearInterval(pollRef.current);
          setPhase("done");
          teamList().then(r => setHistory(r.items || [])).catch(() => {});
        }
      } catch {
        // A single refresh failure must not discard a running job.
      }
    }, 1500);
  }

  async function start() {
    const selectedRoles = (roles || []).filter(role => role.role.trim() && role.task.trim());
    if (selectedRoles.length < 2) {
      setErr("至少保留两项分工后再开始。");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      let conversationId = sid;
      if (!conversationId) {
        conversationId = `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
        await createConversation(conversationId, goal.trim().slice(0, 20));
        addSession({
          id: conversationId,
          title: goal.trim().slice(0, 20),
          messages: [],
          created: Date.now(),
        });
        set({ sid: conversationId });
      }
      const completeGoal = composedGoal();
      const response = await teamStart(
        completeGoal,
        conversationId,
        selectedRoles.slice(0, 4),
        execMode,
      );
      setStatus({
        team_id: response.team_id,
        goal: completeGoal,
        conv_id: conversationId,
        file: response.file || "",
        status: "running",
        final: "",
        roles: selectedRoles.map(role => ({ ...role, state: "wait" })),
        created: Date.now() / 1000,
        mode: execMode,
      });
      setPhase("running");
      watch(response.team_id);
    } catch (error) {
      setErr(`协作没有开始：${(error as Error)?.message || "服务不可用"}`);
    } finally {
      setBusy(false);
    }
  }

  async function openHistory(item: TeamStatus) {
    setBusy(true);
    setErr("");
    try {
      const next = await teamStatus(item.team_id);
      setStatus(next);
      setGoal(next.goal.split("\n期望结果：")[0] || next.goal);
      setRoles(next.roles || []);
      setExecMode(next.mode || "parallel");
      const isActive = next.status === "running" || next.status === "stopping";
      setPhase(isActive ? "running" : "done");
      if (isActive) watch(next.team_id);
    } catch (error) {
      setErr(`无法打开这项协作：${(error as Error)?.message || "请求失败"}`);
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!status || busy || (status.status !== "running" && status.status !== "stopping")) return;
    setBusy(true);
    try {
      const response = await teamStop(status.team_id);
      setStatus(response.team);
    } catch (error) {
      setErr(`停止失败：${(error as Error)?.message || "请求失败"}`);
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    if (!status || busy || status.status === "running" || status.status === "stopping") return;
    setBusy(true);
    setErr("");
    try {
      const response = await teamRetry(status.team_id);
      const next = await teamStatus(response.team_id);
      setStatus(next);
      setRoles(next.roles || []);
      setExecMode(next.mode || "parallel");
      setPhase("running");
      watch(response.team_id);
    } catch (error) {
      setErr(`重新开始失败：${(error as Error)?.message || "请求失败"}`);
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    if (pollRef.current) clearInterval(pollRef.current);
    setPhase("brief");
    setRoles(null);
    setStatus(null);
    setErr("");
  }

  const visibleRoles = status?.roles || roles || [];
  const completed = visibleRoles.filter(role => role.state === "ok").length;
  const isWorking = status?.status === "running" || status?.status === "stopping";
  const categories = Array.from(new Set(agents.map(agent => agent.category).filter(Boolean) as string[])).sort();
  const normalizedAgentQuery = agentQuery.trim().toLowerCase();
  const visibleAgents = agents.filter(agent => {
    if (agentCategory !== "all" && (agent.category || "core") !== agentCategory) return false;
    if (!normalizedAgentQuery) return true;
    return `${agent.name} ${agent.skill} ${agent.category || "core"}`.toLowerCase().includes(normalizedAgentQuery);
  });

  return (
    <div className="flex-1 overflow-y-auto workbench-vnext agents-workbench" style={{ background: "var(--bg-secondary)" }}>
      <div className="mx-auto max-w-[1040px] px-7 py-7">
        <PageHeader
          icon={Users}
          title="智能体协作"
          subtitle={`从全部 ${agents.length || 285} 位专业角色中组建可审阅的工作小组，过程和成果始终回到当前 Chat。`}
          actions={<div className="flex items-center gap-2">
            <Badge tone={isWorking ? "accent" : phase === "done" ? "success" : "neutral"}>{isWorking ? "正在推进" : phase === "done" ? "可验收" : "由你确认"}</Badge>
            <button
              type="button"
              onClick={() => set({ desktopView: null })}
              className="workspace-secondary-action"
              title="回到当前 Chat，继续补充目标或验收结果"
            >
              <MessageSquareText size={12} /> 回到对话
            </button>
            {phase !== "brief" && <button onClick={reset} className="workspace-secondary-action"><RefreshCw size={12} /> 新建协作</button>}
          </div>}
        />
        <div className="workspace-object-shell overflow-hidden rounded-2xl" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
          <aside className="workspace-object-list">
            <div className="flex items-center gap-2 px-3 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
              <History size={13} style={{ color: "var(--text-tertiary)" }} />
              <div className="min-w-0 flex-1"><div className="text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}>最近协作</div><div className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>{history.length} 项工作</div></div>
              <button onClick={reset} className="rounded-lg p-1.5 hover:bg-[var(--bg-tertiary)]" aria-label="发起协作"><Plus size={13} /></button>
            </div>
            <div className="max-h-[610px] overflow-y-auto p-2">
              <button onClick={reset} className="workspace-object-row" data-selected={phase === "brief" && !status}>
                <span className="workspace-object-icon"><Sparkles size={14} /></span>
                <span className="min-w-0 flex-1"><span className="block text-[10.8px] font-medium" style={{ color: "var(--text-primary)" }}>发起一项协作</span><span className="block text-[9px]" style={{ color: "var(--text-tertiary)" }}>从目标和交付物开始</span></span>
              </button>
              {history.map(item => (
                <button key={item.team_id} onClick={() => void openHistory(item)} className="workspace-object-row" data-selected={status?.team_id === item.team_id}>
                  <span className="workspace-object-icon">{item.status === "done" ? <CheckCircle2 size={14} /> : <Circle size={14} />}</span>
                  <span className="min-w-0 flex-1"><span className="block truncate text-[10.8px] font-medium" style={{ color: "var(--text-primary)" }}>{item.goal.split("\n")[0]}</span><span className="block text-[9px]" style={{ color: "var(--text-tertiary)" }}>{statusLabel(item.status)} · {(item.roles || []).length} 项分工</span></span>
                </button>
              ))}
              {!history.length && <div className="px-3 py-8 text-center text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>复杂工作开始后会保留在这里。</div>}
            </div>
          </aside>
          <main className="workspace-object-detail p-6">

        <div className="mb-6 grid grid-cols-3 gap-2">
          {[
            ["1", "说明工作", "目标与交付物"],
            ["2", "确认分工", "先看清再开始"],
            ["3", "查看结果", "可暂停、重试和追问"],
          ].map(([number, title, description], index) => {
            const phaseIndex = phase === "brief" ? 0 : phase === "review" ? 1 : 2;
            const active = index <= phaseIndex;
            return (
              <div key={number} className="rounded-2xl px-4 py-3" style={{ background: active ? "var(--accent-light)" : "var(--bg-secondary)", border: `1px solid ${active ? "color-mix(in srgb, var(--accent) 32%, var(--border))" : "var(--border)"}` }}>
                <div className="flex items-center gap-2">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold" style={{ background: active ? "var(--accent)" : "var(--bg-tertiary)", color: active ? "#fff" : "var(--text-tertiary)" }}>
                    {index < phaseIndex ? <Check size={13} /> : number}
                  </span>
                  <strong className="text-[12.5px]" style={{ color: "var(--text-primary)" }}>{title}</strong>
                </div>
                <div className="ml-8 mt-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{description}</div>
              </div>
            );
          })}
        </div>

        {phase === "brief" && (
          <section className="rounded-[22px] p-5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <div className="mb-3 text-[14px] font-bold" style={{ color: "var(--text-primary)" }}>这次要一起完成什么？</div>
            <textarea
              value={goal}
              onChange={event => setGoal(event.target.value)}
              rows={3}
              placeholder="例如：比较三套办公软件，结合团队需求给出推荐，并形成一页决策说明"
              className="w-full resize-none rounded-2xl px-4 py-3 text-[13px] outline-none"
              style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
            <div className="mt-2 flex flex-wrap gap-2">
              {EXAMPLES.map(example => (
                <button key={example} onClick={() => setGoal(example)} className="rounded-full px-3 py-1.5 text-[11px]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)", background: "var(--bg-primary)" }}>
                  {example}
                </button>
              ))}
            </div>

            <div className="mt-5 grid gap-3 md:grid-cols-2">
              <label className="block">
                <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>希望拿到什么结果</span>
                <input value={outcome} onChange={event => setOutcome(event.target.value)} placeholder="例如：一页结论和可下载报告" className="w-full rounded-xl px-3 py-2.5 text-[12px] outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>什么样才算完成</span>
                <input value={criteria} onChange={event => setCriteria(event.target.value)} placeholder="例如：关键结论有来源，风险写清楚" className="w-full rounded-xl px-3 py-2.5 text-[12px] outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              </label>
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-2">
              <button onClick={() => setMode("auto")} className="rounded-xl px-3 py-2 text-[12px] font-semibold" style={{ background: mode === "auto" ? "var(--accent)" : "var(--bg-primary)", color: mode === "auto" ? "#fff" : "var(--text-secondary)", border: `1px solid ${mode === "auto" ? "var(--accent)" : "var(--border)"}` }}>
                让 HashMM 安排
              </button>
              <button onClick={() => setMode("manual")} className="rounded-xl px-3 py-2 text-[12px] font-semibold" style={{ background: mode === "manual" ? "var(--accent)" : "var(--bg-primary)", color: mode === "manual" ? "#fff" : "var(--text-secondary)", border: `1px solid ${mode === "manual" ? "var(--accent)" : "var(--border)"}` }}>
                我来选择
              </button>
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                {mode === "auto" ? "系统会按任务选择 2–4 位合适的协作者" : `已选择 ${picked.size}/4`}
              </span>
              <button onClick={preview} disabled={busy} className="ml-auto inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-[12.5px] font-semibold text-white disabled:opacity-60" style={{ background: "var(--accent)" }}>
                {busy ? <Loader2 size={14} className="animate-spin" /> : <ArrowRight size={14} />}
                查看安排
              </button>
            </div>

            {mode === "manual" && (
              <div className="mt-4">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <label className="flex min-w-[240px] flex-1 items-center gap-2 rounded-xl px-3 py-2" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                    <Search size={13} style={{ color: "var(--text-tertiary)" }} />
                    <input value={agentQuery} onChange={event => setAgentQuery(event.target.value)} placeholder={`搜索全部 ${agents.length} 位协作者`} className="min-w-0 flex-1 bg-transparent text-[11.5px] outline-none" style={{ color: "var(--text-primary)" }} />
                  </label>
                  <select value={agentCategory} onChange={event => setAgentCategory(event.target.value)} className="rounded-xl px-3 py-2 text-[11.5px] outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                    <option value="all">全部分类</option>
                    <option value="core">HashMM 核心</option>
                    {categories.map(category => <option key={category} value={category}>{category}</option>)}
                  </select>
                  <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>显示 {visibleAgents.length} · 已选 {picked.size}/4</span>
                </div>
                <div className="grid max-h-[420px] gap-2 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-3">
                {visibleAgents.map(agent => {
                  const active = picked.has(agent.id);
                  return (
                    <button key={agent.id} onClick={() => toggleAgent(agent.id)} className="flex items-start gap-3 rounded-2xl p-3 text-left" style={{ background: active ? "var(--accent-light)" : "var(--bg-primary)", border: `1px solid ${active ? "var(--accent)" : "var(--border)"}` }}>
                      <MemberMark name={agent.name} active={active} />
                      <span className="min-w-0">
                        <strong className="block text-[12.5px]" style={{ color: "var(--text-primary)" }}>{agent.name}</strong>
                        <small className="mt-1 block text-[10.5px] leading-4" style={{ color: "var(--text-tertiary)" }}>{agent.skill}</small>
                        <small className="mt-1.5 block text-[9px] uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>{agent.category || "HashMM core"}{agent.license ? ` · ${agent.license}` : ""}</small>
                      </span>
                      {active && <CheckCircle2 size={14} className="ml-auto shrink-0" style={{ color: "var(--accent)" }} />}
                    </button>
                  );
                })}
                {!visibleAgents.length && <div className="col-span-full py-10 text-center text-[11px]" style={{ color: "var(--text-tertiary)" }}>没有匹配的协作者，请换个关键词或分类。</div>}
                </div>
              </div>
            )}
          </section>
        )}

        {phase !== "brief" && visibleRoles.length > 0 && (
          <section>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <div>
                <h2 className="text-[16px] font-bold" style={{ color: "var(--text-primary)" }}>
                  {phase === "review" ? "确认这次分工" : isWorking ? "正在共同完成" : "协作结果"}
                </h2>
                <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  {phase === "review" ? "可以修改每个人负责的内容，确认后才会开始。" : `${completed}/${visibleRoles.length} 项已经完成`}
                </p>
              </div>
              {phase === "review" && (
                <div className="ml-auto flex rounded-xl p-1" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                  <button onClick={() => setExecMode("parallel")} className="rounded-lg px-3 py-1.5 text-[11px]" style={{ background: execMode === "parallel" ? "var(--accent-light)" : "transparent", color: execMode === "parallel" ? "var(--accent)" : "var(--text-tertiary)" }}>同时推进</button>
                  <button onClick={() => setExecMode("pipeline")} className="rounded-lg px-3 py-1.5 text-[11px]" style={{ background: execMode === "pipeline" ? "var(--accent-light)" : "transparent", color: execMode === "pipeline" ? "var(--accent)" : "var(--text-tertiary)" }}>依次接力</button>
                </div>
              )}
              {phase === "review" && (
                <button onClick={start} disabled={busy} className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-[12px] font-semibold text-white disabled:opacity-60" style={{ background: "var(--accent)" }}>
                  {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                  确认并开始
                </button>
              )}
              {phase !== "review" && isWorking && (
                <button onClick={stop} disabled={busy || status?.status === "stopping"} className="ml-auto inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[11.5px]" style={{ border: "1px solid var(--border)", color: "#b42318" }}>
                  <CircleStop size={13} />
                  {status?.status === "stopping" ? "正在停止" : "停止"}
                </button>
              )}
              {phase === "done" && (
                <button onClick={retry} disabled={busy} className="ml-auto inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[11.5px]" style={{ border: "1px solid var(--border)", color: "var(--accent)" }}>
                  <RotateCcw size={13} />
                  重新开始
                </button>
              )}
              {phase !== "review" && status?.file && status.conv_id && (
                <button onClick={() => openArtifact(status.conv_id, { filename: status.file, download_url: `/api/conversations/${status.conv_id}/download/${status.file}` })} className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[11.5px]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                  <Eye size={13} />
                  打开成果
                </button>
              )}
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              {visibleRoles.map((role, index) => (
                <article key={`${role.role}-${index}`} className="rounded-[18px] p-4" style={{ background: "var(--bg-secondary)", border: `1px solid ${role.state === "run" ? "var(--accent)" : "var(--border)"}` }}>
                  <div className="flex items-center gap-2.5">
                    <MemberMark name={role.role} active={role.state === "run"} />
                    <div className="min-w-0 flex-1">
                      <strong className="block truncate text-[13px]" style={{ color: "var(--text-primary)" }}>{role.role}</strong>
                      <span className="text-[10.5px]" style={{ color: role.state === "fail" ? "#b42318" : role.state === "ok" ? "#15803d" : "var(--text-tertiary)" }}>{phase === "review" ? "等待确认" : stateLabel(role.state)}</span>
                    </div>
                  </div>
                  {phase === "review" ? (
                    <label className="mt-3 block">
                      <span className="mb-1 block text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>负责内容</span>
                      <input value={role.task} onChange={event => setRoles(current => current!.map((item, itemIndex) => itemIndex === index ? { ...item, task: event.target.value.slice(0, 160) } : item))} className="w-full rounded-xl px-3 py-2 text-[11.5px] outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                    </label>
                  ) : (
                    <p className="mt-3 text-[11.5px] leading-5" style={{ color: "var(--text-secondary)" }}>{role.task}</p>
                  )}
                  {role.finding && <div className="mt-3 max-h-[190px] overflow-y-auto whitespace-pre-wrap border-t pt-3 text-[11.5px] leading-5" style={{ borderColor: "var(--border)", color: "var(--text-primary)" }}>{role.finding}</div>}
                  {role.err && <p className="mt-2 text-[10.5px]" style={{ color: "#b42318" }}>{role.err}</p>}
                </article>
              ))}
            </div>

            {status?.evidence && (
              <div className="mt-3 flex items-start gap-3 rounded-2xl px-4 py-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <ShieldCheck size={16} className="mt-0.5 shrink-0" style={{ color: status.evidence.state === "ready" ? "#15803d" : "var(--text-tertiary)" }} />
                <div className="min-w-0 flex-1">
                  <strong className="text-[12px]" style={{ color: "var(--text-primary)" }}>使用的资料依据</strong>
                  <p className="mt-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{status.evidence.detail}</p>
                </div>
                <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{status.evidence.count} 条</span>
              </div>
            )}

            {status?.final && (
              <div className="mt-4 rounded-[20px] p-5" style={{ background: "var(--accent-light)", border: "1px solid color-mix(in srgb, var(--accent) 30%, var(--border))" }}>
                <div className="mb-2 flex items-center gap-2">
                  <Sparkles size={15} style={{ color: "var(--accent)" }} />
                  <strong className="text-[13px]" style={{ color: "var(--text-primary)" }}>已经形成结论</strong>
                  {status.conv_id && <button onClick={() => set({ sid: status.conv_id, desktopView: null })} className="ml-auto rounded-lg px-2.5 py-1 text-[10.5px] font-semibold" style={{ color: "var(--accent)", background: "var(--bg-primary)" }}>回到原对话</button>}
                </div>
                <div className="whitespace-pre-wrap text-[12.5px] leading-6" style={{ color: "var(--text-primary)" }}>{status.final}</div>
              </div>
            )}

            {status?.trace && status.trace.length > 0 && (
              <details className="mt-3 rounded-2xl" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <summary className="cursor-pointer px-4 py-3 text-[11.5px] font-semibold" style={{ color: "var(--text-secondary)" }}>查看执行记录 · {status.trace.length} 步</summary>
                <div className="space-y-2 px-4 pb-4">
                  {status.trace.map(item => (
                    <div key={item.id} className="flex gap-3 text-[10.5px]">
                      <span className="w-24 shrink-0 font-semibold" style={{ color: "var(--accent)" }}>{item.node}</span>
                      <span className="flex-1" style={{ color: "var(--text-secondary)" }}>{item.detail}</span>
                    </div>
                  ))}
                </div>
              </details>
            )}

            <button onClick={reset} className="mt-4 inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[11.5px]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
              <RefreshCw size={12} />
              发起另一项协作
            </button>
          </section>
        )}

        {err && <div className="mt-3 rounded-xl px-3 py-2 text-[11.5px]" style={{ background: "#b4231810", color: "#b42318" }}>{err}</div>}

        <details className="mt-7 rounded-2xl" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <summary className="cursor-pointer px-4 py-3 text-[11.5px] font-semibold" style={{ color: "var(--text-secondary)" }}>协作偏好</summary>
          <div className="flex flex-wrap items-center gap-2 border-t px-4 py-3" style={{ borderColor: "var(--border)" }}>
            <span className="mr-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>平时聊天需要协作者时</span>
            {[["auto", "自动选择"], ["off", "不自动加入"]].map(([value, label]) => (
              <button key={value} onClick={() => saveRoute(value)} className="rounded-lg px-2.5 py-1.5 text-[11px]" style={{ background: routeMode === value ? "var(--accent-light)" : "var(--bg-primary)", color: routeMode === value ? "var(--accent)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>{label}</button>
            ))}
            <select value={agents.some(agent => agent.id === routeMode) ? routeMode : ""} onChange={event => event.target.value && saveRoute(event.target.value)} aria-label="固定使用某位协作者" className="rounded-lg px-2.5 py-1.5 text-[11px] outline-none" style={{ background: "var(--bg-primary)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
              <option value="">固定一位协作者</option>
              {agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
            </select>
            {routeSaved && <span className="inline-flex items-center gap-1 text-[10.5px]" style={{ color: "#15803d" }}><Check size={11} />已保存</span>}
          </div>
        </details>
          </main>
        </div>
      </div>
    </div>
  );
}
