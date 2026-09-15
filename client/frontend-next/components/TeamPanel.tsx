"use client";
/** TeamPanel — 多智能体协作操作界面（V254）。
 *
 * V253 的痛点（用户实测）：点「多智能体」只是往输入框填了句示例文字，回车后
 * 被当成**普通消息**发出去了——模型一脸懵地回答"资料里没有竞品信息"。
 * 用户要的是**操作界面**，不是一行字。
 *
 * 本面板给全流程一个真正的驾驶舱（数据源 routes/team_ops.py）：
 *   ① 目标输入 → ② 「预览分工」：协调者拆 2-4 角色，每个角色卡可改名/改分工/删除/新增
 *   → ③ 「启动」：并行执行，四色状态实时轮询（等待 / 执行中 / 完成 / 失败），
 *   产出就地展开 → ④ 汇总卡；控制室画布与会话回帖照旧生成（分享/回看用）。
 */
import { useEffect, useRef, useState } from "react";
import { X, Users, Play, Plus, Trash2, Loader2, RefreshCw, Eye, CheckCircle2, XCircle, CircleDashed, Sparkles, CircleStop, RotateCcw } from "lucide-react";
import { teamAgents, teamPreview, teamStart, teamStatus, teamStop, teamRetry, createConversation, type AgentInfo, type TeamRole, type TeamStatus } from "@/lib/api";
import { useStore } from "@/lib/store";
import { openArtifact } from "@/lib/artifact";

const EXAMPLES = [
  "调研三款竞品并给出选型建议",
  "为新功能写一份发布公告 + FAQ + 风险预案",
  "把本季度目标拆成研发/运营/市场三条线的行动清单",
];

function StateBadge({ s }: { s?: string }) {
  if (s === "run") return <span className="inline-flex items-center gap-1 text-[11px] font-semibold" style={{ color: "#b45309" }}><Loader2 size={11} className="animate-spin" /> 执行中</span>;
  if (s === "ok") return <span className="inline-flex items-center gap-1 text-[11px] font-semibold" style={{ color: "#15803d" }}><CheckCircle2 size={11} /> 完成</span>;
  if (s === "fail") return <span className="inline-flex items-center gap-1 text-[11px] font-semibold" style={{ color: "#b42318" }}><XCircle size={11} /> 失败</span>;
  if (s === "stop") return <span className="inline-flex items-center gap-1 text-[11px] font-semibold" style={{ color: "#64748b" }}><CircleStop size={11} /> 已停止</span>;
  return <span className="inline-flex items-center gap-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}><CircleDashed size={11} /> 等待</span>;
}

export function TeamPanel({ open, initialGoal, onClose, onCompleted }: {
  open: boolean;
  initialGoal?: string;
  onClose: () => void;
  /** Team results are written by the backend to the same conversation.  The host
   * uses this callback to refresh its Chat transcript instead of leaving a stale
   * local copy on screen. */
  onCompleted?: (status: TeamStatus) => void;
}) {
  const sid = useStore(s => s.sid);
  const set = useStore(s => s.set);
  const featureContexts = useStore(s => s.featureContexts);
  const [goal, setGoal] = useState(initialGoal || "");
  const [roles, setRoles] = useState<TeamRole[] | null>(null);
  const [phase, setPhase] = useState<"input" | "preview" | "running" | "done">("input");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [status, setStatus] = useState<TeamStatus | null>(null);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentQuery, setAgentQuery] = useState("");
  const [agentLoadError, setAgentLoadError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completedTeamRef = useRef<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setGoal(g => initialGoal || g); setErr(""); setAgentLoadError("");
    void teamAgents().then(result => setAgents(result.agents || [])).catch(() => {
      setAgents([]); setAgentLoadError("角色库暂时无法读取，仍可手动填写角色");
    });
  }, [open, initialGoal]);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);
  if (!open) return null;

  async function doPreview() {
    const g = goal.trim();
    if (!g) { setErr("先写一句团队目标"); return; }
    setBusy(true); setErr("");
    try {
      const r = await teamPreview(g);
      setRoles((r.roles || []).map(x => ({ ...x })));
      setPhase("preview");
    } catch (e) {
      setErr("拆解失败：" + ((e as Error)?.message || "后端版本过旧"));
    } finally { setBusy(false); }
  }

  function watchTeam(teamId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const st = await teamStatus(teamId);
        setStatus(st);
        if (st.status !== "running" && st.status !== "stopping") {
          if (pollRef.current) clearInterval(pollRef.current);
          setPhase("done");
          // The terminal status is authoritative.  Notify the Chat host once so
          // the persisted assistant summary/control-room file becomes visible in
          // the current transcript even when the modal is closed.
          if (completedTeamRef.current !== st.team_id) {
            completedTeamRef.current = st.team_id;
            onCompleted?.(st);
          }
        }
      } catch { /* 单次轮询失败静默，下一轮再试 */ }
    }, 1500);
  }

  async function doStart() {
    const g = goal.trim();
    const rs = (roles || []).filter(r => r.role.trim() && r.task.trim());
    if (rs.length < 2) { setErr("至少保留 2 个角色（最多 4 个）"); return; }
    setBusy(true); setErr("");
    try {
      let cid = sid;
      if (!cid) {
        cid = "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
        await createConversation(cid, g.slice(0, 20));
        set({ sid: cid });
      }
      const contextSnapshot = [...useStore.getState().featureContexts];
      const r = await teamStart(g, cid, rs.slice(0, 4), undefined, contextSnapshot);
      // Context attachments are one-shot, just like a normal Chat send.  Only
      // consume them after the backend has accepted and owner-bound the team.
      useStore.getState().consumeFeatureContexts(contextSnapshot.map(x => x.id));
      completedTeamRef.current = null;
      setPhase("running");
      setStatus({ team_id: r.team_id, goal: g, conv_id: cid, file: r.file || "", status: "running", final: "", roles: rs.map(x => ({ ...x, state: "wait" })), created: Date.now() / 1000 });
      watchTeam(r.team_id);
    } catch (e) {
      setErr("启动失败：" + ((e as Error)?.message || "后端版本过旧"));
    } finally { setBusy(false); }
  }

  async function doStop() {
    if (!status || busy || (status.status !== "running" && status.status !== "stopping")) return;
    setBusy(true); setErr("");
    try { const r = await teamStop(status.team_id); setStatus(r.team); }
    catch (e) { setErr("停止失败：" + ((e as Error)?.message || "请求失败")); }
    finally { setBusy(false); }
  }

  async function doRetry() {
    if (!status || busy || status.status === "running" || status.status === "stopping") return;
    setBusy(true); setErr("");
    try {
      const r = await teamRetry(status.team_id);
      const next = await teamStatus(r.team_id);
      completedTeamRef.current = null;
      setStatus(next); setGoal(next.goal); setPhase("running"); watchTeam(r.team_id);
    } catch (e) { setErr("重新运行失败：" + ((e as Error)?.message || "请求失败")); }
    finally { setBusy(false); }
  }

  function reset() {
    if (pollRef.current) clearInterval(pollRef.current);
    setPhase("input"); setRoles(null); setStatus(null); setErr("");
  }

  const canEdit = phase === "preview";

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.45)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-[640px] max-h-[86vh] flex flex-col rounded-2xl overflow-hidden anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}>
        {/* 头 */}
        <div className="flex items-center gap-2.5 px-5 py-3.5 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <span className="w-7 h-7 rounded-lg inline-flex items-center justify-center" style={{ background: "var(--accent-light)" }}>
            <Users size={15} style={{ color: "var(--accent)" }} />
          </span>
          <div className="flex-1 min-w-0">
            <div className="text-[14px] font-bold" style={{ color: "var(--text-primary)" }}>多智能体协作</div>
            <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>主任务拆分 → 独立子任务上下文 → 并行执行 → 结果回传主对话</div>
          </div>
          {phase !== "input" && (
            <button onClick={reset} className="px-2 py-1 rounded-lg text-[11px] inline-flex items-center gap-1 hover:bg-[var(--bg-tertiary)]"
              style={{ color: "var(--text-tertiary)" }}><RefreshCw size={11} /> 重来</button>
          )}
          <button onClick={onClose} aria-label="关闭" className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)]">
            <X size={16} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {/* ① 目标 */}
          <div className="text-[11.5px] font-semibold mb-1.5" style={{ color: "var(--text-secondary)" }}>团队目标</div>
          <textarea value={goal} onChange={e => setGoal(e.target.value)} rows={2} disabled={phase === "running"}
            placeholder="一句话说清团队要一起完成什么"
            className="w-full px-3 py-2.5 rounded-xl text-[13px] resize-none outline-none"
            style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          {phase === "input" && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {EXAMPLES.map(ex => (
                <button key={ex} onClick={() => setGoal(ex)}
                  className="px-2.5 py-1 rounded-full text-[11px] hover:bg-[var(--bg-tertiary)]"
                  style={{ border: "1px solid var(--border)", color: "var(--text-tertiary)" }}>{ex}</button>
              ))}
            </div>
          )}
          {featureContexts.length > 0 && phase !== "running" && (
            <div className="mt-2 rounded-xl px-3 py-2 text-[11px] leading-relaxed"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
              将带入当前 Chat 的 {featureContexts.length} 项上下文（浏览器、画布或文件等）。
              团队只把它们当作不可信资料，不会当成系统指令或已验证引用。
            </div>
          )}
          {phase === "preview" && (
            <div className="mt-3 rounded-xl px-3 py-2.5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2">
                <div className="text-[11px] font-semibold" style={{ color: "var(--text-secondary)" }}>完整角色库</div>
                <span className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>{agents.length ? `${agents.length} 个可选角色` : agentLoadError || "正在读取…"}</span>
              </div>
              {agents.length > 0 && <input value={agentQuery} onChange={e => setAgentQuery(e.target.value)} placeholder="按名称、能力或类别筛选角色"
                className="mt-2 w-full rounded-lg px-2.5 py-1.5 text-[11px] outline-none"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />}
            </div>
          )}

          {/* ② 分工（预览态可编辑）/ ③ 运行态四色卡 */}
          {(roles || status) && (
            <>
              <div className="flex items-center justify-between mt-4 mb-1.5">
                <div className="text-[11.5px] font-semibold" style={{ color: "var(--text-secondary)" }}>
                  角色分工 {canEdit && <span className="font-normal" style={{ color: "var(--text-tertiary)" }}>（可改名 / 改分工 / 删除，2-4 个）</span>}
                </div>
                {canEdit && (roles?.length || 0) < 4 && (
                  <button onClick={() => setRoles(r => [...(r || []), { role: "", task: "" }])}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] hover:bg-[var(--bg-tertiary)]"
                    style={{ color: "var(--accent)" }}><Plus size={11} /> 加角色</button>
                )}
              </div>
              <div className="space-y-2">
                {(status?.roles || roles || []).map((r, i) => (
                  <div key={i} className="rounded-xl px-3 py-2.5" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                    <div className="flex items-center gap-2">
                      {canEdit ? (
                        <input value={r.role} onChange={e => setRoles(rs => rs!.map((x, j) => j === i ? { ...x, role: e.target.value.slice(0, 8) } : x))}
                          placeholder="角色名" className="w-20 px-2 py-1 rounded-lg text-[12px] font-semibold outline-none"
                          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--accent)" }} />
                      ) : (
                        <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold flex-shrink-0"
                          style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{r.role}</span>
                      )}
                      <div className="flex-1" />
                      {phase !== "preview" && <StateBadge s={r.state} />}
                      {canEdit && (roles?.length || 0) > 2 && (
                        <button onClick={() => setRoles(rs => rs!.filter((_, j) => j !== i))} aria-label="删除角色"
                          className="p-1 rounded-md hover:bg-[var(--bg-tertiary)]"><Trash2 size={12} style={{ color: "var(--text-tertiary)" }} /></button>
                      )}
                    </div>
                    {canEdit ? (
                      <>
                        {agents.length > 0 && (
                          <select value={r.agent_id || ""} onChange={e => {
                            const selected = agents.find(agent => agent.id === e.target.value);
                            setRoles(rs => rs!.map((x, j) => j === i ? {
                              ...x, agent_id: selected?.id || undefined,
                              role: selected?.name ? selected.name.slice(0, 40) : x.role,
                            } : x));
                          }} className="mt-1.5 w-full rounded-lg px-2 py-1.5 text-[11px] outline-none"
                            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                            <option value="">自定义角色（不绑定角色库）</option>
                            {agents.filter(agent => {
                              const q = agentQuery.trim().toLowerCase();
                              return !q || `${agent.name} ${agent.skill} ${agent.category || ""}`.toLowerCase().includes(q) || agent.id === r.agent_id;
                            }).map(agent => <option key={agent.id} value={agent.id}>{agent.name} · {agent.category || "通用"}</option>)}
                          </select>
                        )}
                        <input value={r.task} onChange={e => setRoles(rs => rs!.map((x, j) => j === i ? { ...x, task: e.target.value.slice(0, 120) } : x))}
                          placeholder="该角色的一句话分工" className="mt-1.5 w-full px-2 py-1.5 rounded-lg text-[12px] outline-none"
                          style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                      </>
                    ) : (
                      <div className="mt-1 text-[12px]" style={{ color: "var(--text-secondary)" }}>{r.task}</div>
                    )}
                    {r.finding && (
                      <div className="mt-2 pt-2 text-[12px] whitespace-pre-wrap" style={{ borderTop: "1px dashed var(--border)", color: "var(--text-primary)" }}>{r.finding}</div>
                    )}
                    {r.err && <div className="mt-1.5 text-[11px]" style={{ color: "#b42318" }}>失败：{r.err}</div>}
                  </div>
                ))}
              </div>
            </>
          )}

          {/* ④ 汇总 */}
          {status?.final && (
            <div className="mt-4 rounded-xl px-4 py-3" style={{ background: "var(--bg-secondary)", borderLeft: "3px solid var(--accent)", border: "1px solid var(--border)" }}>
              <div className="text-[11.5px] font-semibold mb-1 inline-flex items-center gap-1.5" style={{ color: "var(--accent)" }}><Sparkles size={12} /> 团队汇总</div>
              <div className="text-[13px] whitespace-pre-wrap leading-relaxed" style={{ color: "var(--text-primary)" }}>{status.final}</div>
            </div>
          )}
          {phase === "done" && status && !status.final && (
            <div className="mt-4 text-[12px] px-3 py-2.5 rounded-xl" style={{ background: "var(--warning-light, #fef3c7)", color: "#92400e" }}>
              角色全部结束但未能产出可靠汇总——展开上方失败角色看原因，可「重来」换个说法再派。
            </div>
          )}
          {err && <div className="mt-3 text-[12px]" style={{ color: "#b42318" }}>{err}</div>}
        </div>

        {/* 脚 */}
        <div className="flex items-center gap-2 px-5 py-3 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
          <div className="text-[11px] flex-1" style={{ color: "var(--text-tertiary)" }}>
            {status?.status === "stopping" ? "停止请求已记录；等待当前模型调用安全返回，返回内容不会进入汇总。" :
             phase === "running" ? "子任务在线程中独立执行；关闭面板不影响运行，状态与结果会回到右栏和主对话。" :
             phase === "done" ? (status?.status === "stopped" ? "团队已停止，可重新运行并保留审计关系。" : "已结束——汇总同时回帖到了会话。") :
             "启动后会在当前会话生成「控制室画布」，可发布给团队同链接看直播。"}
          </div>
          {status && (status.status === "running" || status.status === "stopping") && (
            <button onClick={doStop} disabled={busy || status.status === "stopping"}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl text-[12px] disabled:opacity-50"
              style={{ border: "1px solid var(--border)", color: "#b42318" }}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <CircleStop size={12} />}{status.status === "stopping" ? "停止中" : "停止"}</button>
          )}
          {status && status.status !== "running" && status.status !== "stopping" && (
            <button onClick={doRetry} disabled={busy}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl text-[12px] disabled:opacity-50"
              style={{ border: "1px solid var(--border)", color: "var(--accent)" }}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}重新运行</button>
          )}
          {status?.file && status.conv_id && (
            <button onClick={() => openArtifact(status.conv_id, { filename: status.file, download_url: `/api/conversations/${status.conv_id}/download/${status.file}` })}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl text-[12px] hover:bg-[var(--bg-tertiary)]"
              style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><Eye size={12} /> 看画布</button>
          )}
          {phase === "input" && (
            <button onClick={doPreview} disabled={busy}
              className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-xl text-[12.5px] font-semibold text-white disabled:opacity-60"
              style={{ background: "var(--accent)" }}>{busy ? <Loader2 size={13} className="animate-spin" /> : <Users size={13} />} 预览分工</button>
          )}
          {phase === "preview" && (
            <button onClick={doStart} disabled={busy}
              className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-xl text-[12.5px] font-semibold text-white disabled:opacity-60"
              style={{ background: "var(--accent)" }}>{busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} 启动执行</button>
          )}
        </div>
      </div>
    </div>
  );
}
