"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle, ArrowRight, CheckCircle2, CirclePause, FileCheck2,
  Folder, Loader2, MessageCircleQuestion, Pause, Play, Plus, RefreshCw,
  RotateCcw, ShieldCheck, Target, X,
} from "lucide-react";
import {
  createWorkProject, getUserWorkOverview, listWorkProjects, subscribeWorkFeed, workRunCommand,
  workRunsFeed, type WorkProject,
} from "@/lib/api";
import { useStore } from "@/lib/store";
import type { WorkActionItem, WorkControlAction, WorkPresentation, WorkRun } from "@/lib/types";
import {
  readAccountProjects,
  writeAccountProjects,
  readActiveProject,
  writeActiveProject,
} from "@/lib/accountWorkspaceCache";

const ACTIVE = new Set(["queued", "running", "waiting_input", "waiting_approval", "blocked", "delivered"]);
const RESULTS = new Set(["delivered", "completed", "observed"]);

const PHASE_COLOR: Record<string, string> = {
  preparing: "#64748b", working: "var(--accent)", needs_user: "#b45309",
  needs_attention: "#b42318", review: "#7c3aed", finished: "#15803d",
};

function ago(ts: number): string {
  const delta = Math.max(0, Date.now() / 1000 - ts);
  if (delta < 60) return "刚刚";
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
  return new Date(ts * 1000).toLocaleDateString();
}

async function loadAllWork(projectId = ""): Promise<{
  items: WorkRun[];
  actions: WorkActionItem[];
  cursor: number;
  projects: WorkProject[];
}> {
  const overview = await getUserWorkOverview(projectId);
  let feed = overview.work;
  let cursor = Number(feed.next_cursor || 0);
  const byId = new Map<string, WorkRun>();
  for (const item of feed.items || []) byId.set(item.id, item);
  let authoritativeActions: WorkActionItem[] | null =
    feed.action_inbox?.schema === "hashmm.action-inbox.v1"
      ? feed.action_inbox.items
      : null;
  for (let page = 1; feed.has_more && page < 6; page += 1) {
    feed = await workRunsFeed(cursor, "", { limit: 250, projectId });
    for (const item of feed.items || []) byId.set(item.id, item);
    cursor = feed.next_cursor;
  }
  const items = [...byId.values()].sort((a, b) => b.updated_at - a.updated_at);
  const fallbackActions = items
    .filter(item => item.presentation?.needs_user)
    .map(item => ({
      schema: "hashmm.action-item.v1" as const,
      id: `action:${item.id}`,
      run_id: item.id,
      conversation_id: item.conversation_id,
      type: item.status === "waiting_approval" ? "approval" as const
        : item.status === "waiting_input" ? "question" as const
          : item.status === "delivered" ? "delivery" as const
            : item.status === "failed" ? "failure" as const
              : item.status === "interrupted" ? "interruption" as const : "blocker" as const,
      priority: ["waiting_approval", "waiting_input", "blocked", "failed"].includes(item.status) ? "high" as const : "normal" as const,
      title: item.presentation.title,
      summary: item.presentation.current_step,
      primary_action: item.presentation.primary_action,
      context: {},
      updated_at: item.updated_at,
    }))
    .sort((a, b) => (a.priority === b.priority ? b.updated_at - a.updated_at : a.priority === "high" ? -1 : 1));
  const actions = authoritativeActions ?? fallbackActions;
  return { items, actions, cursor, projects: overview.projects || [] };
}

function presentationFor(run: WorkRun): WorkPresentation {
  if (run.presentation?.schema === "hashmm.work-presentation.v1") return run.presentation;
  const labels: Record<string, string> = {
    queued: "准备中", running: "进行中", waiting_input: "需要补充",
    waiting_approval: "等待确认", blocked: "暂时受阻", delivered: "等待验收",
    completed: "已完成", observed: "历史记录", failed: "未完成",
    cancelled: "已取消", interrupted: "已中断",
  };
  return {
    schema: "hashmm.work-presentation.v1",
    title: run.title || "一项工作",
    category: "工作",
    status_label: labels[run.status] || "状态更新",
    phase: ["completed", "observed", "cancelled"].includes(run.status) ? "finished" : "working",
    current_step: "打开工作可查看服务器记录的最新状态",
    next_action: "查看最新工作记录",
    needs_user: ["waiting_input", "waiting_approval", "blocked", "delivered", "failed", "interrupted"].includes(run.status),
    primary_action: "open",
    progress: { mode: "phase", completed: 0, total: 0, label: "状态以服务器为准" },
    deliverables: [],
    evidence: { status: "not_available", count: 0, label: "尚无可展示的完成证据" },
    sync: { state: "synced", updated_at: run.updated_at },
  };
}

export function WorkOverviewView({ mode }: { mode: "active" | "results" }) {
  const set = useStore(s => s.set);
  const token = useStore(s => s.token);
  const user = useStore(s => s.user);
  const [items, setItems] = useState<WorkRun[]>([]);
  const [actions, setActions] = useState<WorkActionItem[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [commandBusy, setCommandBusy] = useState("");
  const [projects, setProjects] = useState<WorkProject[]>(() => readAccountProjects(useStore.getState().user));
  const [selectedProject, setSelectedProject] = useState(() => readActiveProject(useStore.getState().user));
  const [projectFormOpen, setProjectFormOpen] = useState(false);
  const [newProject, setNewProject] = useState({
    name: "",
    goal: "",
    deliverable: "",
    criteria: "",
    permissionMode: "ask" as WorkProject["permission_mode"],
  });
  const [creatingProject, setCreatingProject] = useState(false);
  const cursorRef = useRef(0);

  const refresh = useCallback(async (quiet = false) => {
    if (!token) {
      setItems([]); setActions([]); setBusy(false); return;
    }
    if (!quiet) setBusy(true);
    try {
      const data = await loadAllWork(selectedProject);
      setItems(data.items); setActions(data.actions); setProjects(data.projects);
      writeAccountProjects(user, data.projects);
      setError("");
      cursorRef.current = Math.max(cursorRef.current, data.cursor);
      if (selectedProject && !data.projects.some(item => item.id === selectedProject)) {
        setSelectedProject("");
        writeActiveProject(user, "");
      }
    } catch (e) {
      setError((e as Error)?.message || "工作暂时无法更新");
    } finally {
      if (!quiet) setBusy(false);
    }
  }, [selectedProject, token, user]);

  useEffect(() => {
    let disposed = false;
    let unsubscribe = () => {};
    void refresh().then(() => {
      if (disposed) return;
      unsubscribe = subscribeWorkFeed({
        afterCursor: cursorRef.current,
        projectId: selectedProject,
        onFeed: feed => {
          cursorRef.current = Math.max(cursorRef.current, Number(feed.next_cursor || 0));
          setItems(previous => {
            const merged = new Map(previous.map(item => [item.id, item]));
            for (const item of feed.items || []) merged.set(item.id, item);
            return [...merged.values()].sort((a, b) => b.updated_at - a.updated_at);
          });
          if (feed.action_inbox?.schema === "hashmm.action-inbox.v1") {
            setActions(feed.action_inbox.items || []);
          }
          setError("");
        },
      });
    });
    // Push is primary; this slower owner-authenticated read repairs missed
    // events after sleep, proxy buffering or a temporary stream interruption.
    const timer = window.setInterval(() => refresh(true), 60_000);
    const online = () => refresh(true);
    window.addEventListener("hmm-backend-online", online);
    return () => {
      disposed = true;
      unsubscribe();
      window.clearInterval(timer);
      window.removeEventListener("hmm-backend-online", online);
    };
  }, [refresh]);

  const chooseProject = useCallback((projectId: string) => {
    cursorRef.current = 0;
    setSelectedProject(projectId);
    writeActiveProject(user, projectId);
  }, [user]);

  useEffect(() => {
    setSelectedProject(readActiveProject(user));
    setProjects(readAccountProjects(user));
  }, [user?.id, user?.username]);

  useEffect(() => {
    const onProjectSelected = (event: Event) => {
      const projectId = String(
        (event as CustomEvent<{ projectId?: string }>).detail?.projectId || "",
      );
      if (projectId) chooseProject(projectId);
    };
    window.addEventListener("hmm-project-selected", onProjectSelected);
    return () => window.removeEventListener("hmm-project-selected", onProjectSelected);
  }, [chooseProject]);

  const addProject = async () => {
    const name = newProject.name.trim();
    if (!name || creatingProject) return;
    setCreatingProject(true);
    try {
      const result = await createWorkProject({
        name,
        goal: newProject.goal.trim(),
        deliverable: newProject.deliverable.trim(),
        success_criteria: newProject.criteria
          .split("\n").map(value => value.trim()).filter(Boolean),
        permission_mode: newProject.permissionMode,
      });
      const next = await listWorkProjects();
      setProjects(next);
      writeAccountProjects(user, next);
      setNewProject({ name: "", goal: "", deliverable: "", criteria: "", permissionMode: "ask" });
      setProjectFormOpen(false);
      chooseProject(result.id);
    } catch (e) {
      setError((e as Error)?.message || "项目没有创建");
    } finally {
      setCreatingProject(false);
    }
  };

  const visible = useMemo(
    () => items.filter(item => (mode === "active" ? ACTIVE : RESULTS).has(item.status)),
    [items, mode],
  );
  const activeProject = useMemo(
    () => projects.find(project => project.id === selectedProject) || null,
    [projects, selectedProject],
  );

  const openWork = (run: WorkRun) => {
    set({
      desktopView: "work-detail",
      workDetailId: run.id,
      workDetailParent: mode === "results" ? "work-results" : "work-active",
    });
  };

  const act = async (run: WorkRun, action: WorkControlAction) => {
    setCommandBusy(run.id);
    try {
      await workRunCommand(run.id, action, run.revision);
      await refresh(true);
    } catch (e) {
      setError((e as Error)?.message || "操作没有完成，请刷新后重试");
    } finally {
      setCommandBusy("");
    }
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto" style={{ background: "var(--canvas)" }}>
      <div className="w-full max-w-[1040px] mx-auto px-6 md:px-10 py-8 md:py-11">
        <header className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-semibold tracking-wide mb-1" style={{ color: "var(--accent)" }}>
              {mode === "active" ? "你的工作" : "交付中心"}
            </div>
            <h1 className="text-[28px] font-semibold tracking-[-0.025em]" style={{ color: "var(--text-primary)" }}>
              {mode === "active" ? "进行中" : "成果"}
            </h1>
            <p className="mt-1 text-[13px] leading-6" style={{ color: "var(--text-tertiary)" }}>
              {mode === "active" ? "查看现在做到哪里，处理需要你的事项，然后从原位置继续。" : "集中查看已经生成并可以继续使用的内容。"}
            </p>
          </div>
          <button onClick={() => { useStore.getState().newChat(); set({ desktopView: null }); }}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-[12px] font-medium text-white"
            style={{ background: "var(--accent)" }}>
            <Plus size={14} /> 开始新工作
          </button>
          <button onClick={() => refresh()} aria-label="刷新"
            className="p-2 rounded-xl hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
            <RefreshCw size={15} className={busy ? "animate-spin" : ""} />
          </button>
        </header>

        <section className="mt-6 rounded-2xl p-3.5"
          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 text-[11px] font-medium mr-1" style={{ color: "var(--text-secondary)" }}>
              <Folder size={13} /> 项目空间
            </span>
            <button onClick={() => chooseProject("")}
              className="px-2.5 py-1.5 rounded-lg text-[10.5px]"
              style={{ color: selectedProject ? "var(--text-tertiary)" : "var(--accent)", background: selectedProject ? "transparent" : "var(--accent-light)" }}>
              全部工作
            </button>
            {projects.map(project => (
              <button key={project.id} onClick={() => chooseProject(project.id)}
                className="px-2.5 py-1.5 rounded-lg text-[10.5px] max-w-[180px] truncate"
                title={`${project.name} · ${project.conv_count || 0} 个对话`}
                style={{ color: selectedProject === project.id ? "var(--accent)" : "var(--text-secondary)", background: selectedProject === project.id ? "var(--accent-light)" : "var(--bg-secondary)" }}>
                {project.name}
              </button>
            ))}
            <button onClick={() => setProjectFormOpen(value => !value)}
              className="ml-auto inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10.5px]"
              style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
              {projectFormOpen ? <X size={12} /> : <Plus size={12} />}
              {projectFormOpen ? "收起" : "定义新项目"}
            </button>
          </div>
          {activeProject ? (
            <div className="mt-3 grid grid-cols-1 md:grid-cols-3 gap-2">
              {[
                ["目标", activeProject.goal || "尚未填写"],
                ["交付物", activeProject.deliverable || "尚未填写"],
                ["验收", activeProject.success_criteria?.length ? `${activeProject.success_criteria.length} 条明确标准` : "尚未填写"],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl px-3 py-2.5" style={{ background: "var(--bg-secondary)" }}>
                  <div className="text-[9.5px] font-medium" style={{ color: "var(--text-tertiary)" }}>{label}</div>
                  <div className="mt-1 text-[10.5px] leading-4 line-clamp-2" style={{ color: "var(--text-secondary)" }}>{value}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-2 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
              选择项目后，新对话、工作进度和成果会沿用同一个目标范围。
            </div>
          )}
          {projectFormOpen && (
            <div className="mt-3 rounded-xl p-3.5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2 mb-3">
                <Target size={14} style={{ color: "var(--accent)" }} />
                <div>
                  <div className="text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}>先说明要完成什么</div>
                  <div className="text-[9.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>这些内容由你确认，HashMM 不会替你编造成功标准。</div>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                <input value={newProject.name}
                  onChange={event => setNewProject(value => ({ ...value, name: event.target.value }))}
                  maxLength={120} placeholder="项目名称"
                  className="px-3 py-2 rounded-xl text-[11px] outline-none"
                  style={{ color: "var(--text-primary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }} />
                <select value={newProject.permissionMode}
                  onChange={event => setNewProject(value => ({ ...value, permissionMode: event.target.value as WorkProject["permission_mode"] }))}
                  className="px-3 py-2 rounded-xl text-[11px] outline-none"
                  style={{ color: "var(--text-primary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                  <option value="ask">执行前按需确认</option>
                  <option value="read_only">只读取和分析</option>
                  <option value="trusted_workspace">允许在项目工作区内修改</option>
                </select>
                <textarea value={newProject.goal}
                  onChange={event => setNewProject(value => ({ ...value, goal: event.target.value }))}
                  maxLength={2000} rows={3} placeholder="目标：最终希望解决什么问题"
                  className="px-3 py-2 rounded-xl text-[11px] leading-5 outline-none resize-none"
                  style={{ color: "var(--text-primary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }} />
                <textarea value={newProject.deliverable}
                  onChange={event => setNewProject(value => ({ ...value, deliverable: event.target.value }))}
                  maxLength={1200} rows={3} placeholder="交付物：希望拿到报告、文档、代码还是完成后的操作"
                  className="px-3 py-2 rounded-xl text-[11px] leading-5 outline-none resize-none"
                  style={{ color: "var(--text-primary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }} />
              </div>
              <textarea value={newProject.criteria}
                onChange={event => setNewProject(value => ({ ...value, criteria: event.target.value }))}
                maxLength={3000} rows={3} placeholder={"验收标准：每行一条，例如\n关键数字附来源\n最终交付可直接下载"}
                className="mt-2.5 w-full px-3 py-2 rounded-xl text-[11px] leading-5 outline-none resize-none"
                style={{ color: "var(--text-primary)", background: "var(--bg-primary)", border: "1px solid var(--border)" }} />
              <div className="mt-3 flex justify-end">
                <button disabled={!newProject.name.trim() || creatingProject} onClick={() => void addProject()}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-[11px] font-medium text-white disabled:opacity-40"
                  style={{ background: "var(--accent)" }}>
                  {creatingProject ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
                  创建并用于新工作
                </button>
              </div>
            </div>
          )}
        </section>

        {mode === "active" && actions.length > 0 && (
          <section className="mt-8">
            <div className="flex items-center justify-between mb-2.5">
              <h2 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>需要你处理</h2>
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{actions.length} 项</span>
            </div>
            <div className="rounded-2xl overflow-hidden" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              {actions.slice(0, 6).map((action, index) => {
                const run = items.find(item => item.id === action.run_id);
                if (!run) return null;
                const Icon = action.type === "question" ? MessageCircleQuestion
                  : action.type === "delivery" ? FileCheck2 : AlertCircle;
                return (
                  <button key={action.id} onClick={() => openWork(run)}
                    className="w-full flex items-center gap-3 px-4 py-3.5 text-left hover:bg-[var(--bg-secondary)] transition-colors"
                    style={index ? { borderTop: "1px solid var(--border)" } : undefined}>
                    <span className="w-8 h-8 rounded-lg flex items-center justify-center"
                      style={{ background: action.priority === "high" ? "rgba(180,83,9,.09)" : "var(--accent-light)", color: action.priority === "high" ? "#b45309" : "var(--accent)" }}>
                      <Icon size={16} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[12.5px] font-medium truncate" style={{ color: "var(--text-primary)" }}>{action.title}</span>
                      <span className="block text-[11px] mt-0.5 truncate" style={{ color: "var(--text-tertiary)" }}>
                        {action.summary}
                        {action.context?.kind === "remote_session" && action.context.scopes?.length
                          ? ` · 本次申请：${action.context.scopes.map(scope => ({ view: "查看屏幕", control: "操作电脑", clipboard: "剪贴板", file_read: "读取文件", file_write: "写入文件", audio: "声音", power: "电源操作" }[scope] || scope)).join("、")}`
                          : ""}
                      </span>
                    </span>
                    <span className="text-[11px]" style={{ color: "var(--accent)" }}>
                      {action.type === "approval" ? "确认" : action.type === "question" ? "补充" : action.type === "delivery" ? "验收" : "查看"}
                    </span>
                    <ArrowRight size={13} style={{ color: "var(--text-tertiary)" }} />
                  </button>
                );
              })}
            </div>
          </section>
        )}

        <section className="mt-8">
          <div className="flex items-center justify-between mb-2.5">
            <h2 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>
              {mode === "active" ? "全部进行中" : "最近成果"}
            </h2>
            <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{visible.length} 项</span>
          </div>
          {error && (
            <div className="mb-3 px-3.5 py-3 rounded-xl text-[12px]" style={{ background: "rgba(180,35,24,.07)", color: "#b42318" }}>
              {error}。本地已有内容仍然保留。
            </div>
          )}
          {busy && items.length === 0 ? (
            <div className="h-44 flex items-center justify-center gap-2 text-[12px]" style={{ color: "var(--text-tertiary)" }}>
              <Loader2 size={16} className="animate-spin" /> 正在读取工作状态
            </div>
          ) : visible.length === 0 ? (
            <div className="py-14 px-6 rounded-2xl text-center" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <CheckCircle2 size={24} className="mx-auto mb-3" style={{ color: "var(--text-tertiary)" }} />
              <div className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                {mode === "active" ? "现在没有进行中的工作" : "还没有可以展示的成果"}
              </div>
              <div className="text-[11.5px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>
                从对话中说明目标，HashMM 会把需要持续处理的内容保存到这里。
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3">
              {visible.map(run => {
                const p = presentationFor(run);
                const progressWidth = p.progress.total > 0 ? `${Math.round(p.progress.completed / p.progress.total * 100)}%` : "0%";
                return (
                  <article key={run.id} className="rounded-2xl px-4 py-4"
                    style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                    <div className="flex items-start gap-3">
                      <span className="mt-1 w-2 h-2 rounded-full flex-shrink-0" style={{ background: PHASE_COLOR[p.phase] || "var(--text-tertiary)" }} />
                      <button className="min-w-0 flex-1 text-left" onClick={() => openWork(run)}>
                        <div className="flex items-center gap-2">
                          <h3 className="text-[13.5px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{p.title}</h3>
                          <span className="px-2 py-0.5 rounded-full text-[10px] flex-shrink-0"
                            style={{ background: "var(--bg-secondary)", color: PHASE_COLOR[p.phase] || "var(--text-secondary)" }}>{p.status_label}</span>
                        </div>
                        <p className="mt-1 text-[11.5px] leading-5 line-clamp-2" style={{ color: "var(--text-secondary)" }}>{p.current_step}</p>
                        <div className="mt-3 flex items-center gap-3">
                          <div className="w-28 h-1 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
                            <div className="h-full rounded-full" style={{ width: progressWidth, background: PHASE_COLOR[p.phase] || "var(--accent)" }} />
                          </div>
                          <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{p.progress.label}</span>
                          {p.evidence.status !== "not_available" && (
                            <span className="inline-flex items-center gap-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                              <ShieldCheck size={11} /> {p.evidence.label}
                            </span>
                          )}
                          <span className="ml-auto text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{ago(run.updated_at)}</span>
                        </div>
                      </button>
                    </div>
                    <div className="mt-3 pt-3 flex items-center gap-2" style={{ borderTop: "1px solid var(--border)" }}>
                      <button onClick={() => openWork(run)} className="text-[11px] font-medium px-2.5 py-1.5 rounded-lg hover:bg-[var(--bg-secondary)]" style={{ color: "var(--accent)" }}>
                        {p.primary_action === "review_delivery" ? "检查成果" : p.needs_user ? "处理并继续" : "打开工作"}
                      </button>
                      {run.control.available_actions.includes("pause") && (
                        <button disabled={commandBusy === run.id} onClick={() => act(run, "pause")} className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-lg hover:bg-[var(--bg-secondary)]" style={{ color: "var(--text-secondary)" }}>
                          <Pause size={11} /> 暂停
                        </button>
                      )}
                      {run.control.available_actions.includes("resume") && (
                        <button disabled={commandBusy === run.id} onClick={() => act(run, "resume")} className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-lg hover:bg-[var(--bg-secondary)]" style={{ color: "var(--text-secondary)" }}>
                          <Play size={11} /> 继续
                        </button>
                      )}
                      {run.control.available_actions.includes("retry") && (
                        <button disabled={commandBusy === run.id} onClick={() => act(run, "retry")} className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-lg hover:bg-[var(--bg-secondary)]" style={{ color: "var(--text-secondary)" }}>
                          <RotateCcw size={11} /> 重新尝试
                        </button>
                      )}
                      {run.status === "interrupted" && <span className="ml-auto inline-flex items-center gap-1 text-[10.5px]" style={{ color: "#b45309" }}><CirclePause size={11} /> 已保留最近进度</span>}
                      {p.deliverables.length > 0 && <span className="ml-auto text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{p.deliverables.length} 个成果</span>}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
