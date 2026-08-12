"use client";

import { useState } from "react";
import { CheckCircle2, ChevronRight, Circle, CircleStop, Clock3, Loader2, PauseCircle, Send, Users, XCircle } from "lucide-react";

export type OrchMember = {
  id: string;
  step?: number;
  role_label?: string;
  task?: string;
  status?: "pending" | "running" | "done" | "failed" | "blocked" | "waiting_input" | "stopped";
  elapsed_ms?: number;
  preview?: string;
  thread_id?: string;
  parent_thread_id?: string;
  session_id?: string;
  scope_id?: string;
  session_status?: string;
  tool_calls?: number;
  allowed_tools?: string[];
  session_steps?: string[];
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
};
export type Orchestration = { team_id?: string; strategy?: string; status?: string; retry_of?: string; members: OrchMember[] };

const STRATEGY_LABEL: Record<string, string> = {
  compare: "对比", sequential: "串行", single: "单步", parallel: "并行", pipeline: "流水线",
};

function StatusIcon({ status }: { status: OrchMember["status"] }) {
  if (status === "done") return <CheckCircle2 size={14} style={{ color: "#15803d" }} />;
  if (status === "stopped") return <CircleStop size={14} style={{ color: "#64748b" }} />;
  if (status === "failed") return <XCircle size={14} style={{ color: "#b42318" }} />;
  if (status === "blocked" || status === "waiting_input") return <PauseCircle size={14} style={{ color: "#b45309" }} />;
  if (status === "running") return <Loader2 size={14} className="animate-spin" style={{ color: "var(--accent)" }} />;
  return <Circle size={14} style={{ color: "var(--text-tertiary)" }} />;
}

function statusLabel(status: OrchMember["status"]): string {
  if (status === "running") return "Active";
  if (status === "done") return "Done";
  if (status === "failed") return "失败";
  if (status === "blocked" || status === "waiting_input") return "等待输入";
  if (status === "stopped") return "已停止";
  return "等待";
}

export function SubAgentPanel({
  orch,
  onMessage,
  onStop,
  busySession = "",
  actionError = "",
}: {
  orch?: Orchestration | null;
  onMessage?: (sessionId: string, content: string) => Promise<void>;
  onStop?: (sessionId: string) => Promise<void>;
  busySession?: string;
  actionError?: string;
}) {
  const [expanded, setExpanded] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  if (!orch || !Array.isArray(orch.members) || orch.members.length === 0) return null;
  const total = orch.members.length;
  const done = orch.members.filter(m => m.status === "done").length;
  const active = orch.members.filter(m => m.status === "running").length;
  const needsInput = orch.members.filter(m => m.status === "blocked" || m.status === "waiting_input").length;

  return (
    <div className="rounded-xl mb-3 overflow-hidden" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
        <Users size={13} style={{ color: "var(--accent)" }} />
        <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>子任务线程</span>
        {orch.strategy && <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>{STRATEGY_LABEL[orch.strategy] || orch.strategy}</span>}
        <span className="ml-auto text-[10px] font-mono" style={{ color: needsInput ? "#b45309" : active ? "var(--accent)" : "var(--text-tertiary)" }}>
          {needsInput ? `${needsInput} 待处理` : active ? `${active} Active` : `${done}/${total} Done`}
        </span>
      </div>
      <div className="flex flex-col">
        {orch.members.map((member, index) => {
          const status = member.status || "pending";
          const key = member.thread_id || member.id || String(index);
          const open = expanded === key;
          const sessionId = member.session_id || "";
          const actionable = !!sessionId && ["pending", "running", "blocked", "waiting_input"].includes(status);
          const busy = busySession === sessionId;
          return (
            <div key={key} style={{ borderTop: index > 0 ? "1px solid var(--border)" : "none" }}>
              <button type="button" onClick={() => setExpanded(open ? "" : key)}
                className="w-full text-left px-3 py-2.5 transition-colors hover:bg-[var(--bg-tertiary)]"
                aria-expanded={open}>
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 shrink-0"><StatusIcon status={status} /></span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] font-semibold" style={{ color: "var(--text-primary)" }}>{member.role_label || `子任务 ${member.step ?? index + 1}`}</span>
                    <span className="text-[9.5px]" style={{ color: status === "failed" ? "#b42318" : status === "running" ? "var(--accent)" : "var(--text-tertiary)" }}>{statusLabel(status)}</span>
                    {typeof member.elapsed_ms === "number" && member.elapsed_ms > 0 && <span className="ml-auto inline-flex items-center gap-1 text-[9.5px] font-mono" style={{ color: "var(--text-tertiary)" }}><Clock3 size={10} />{member.elapsed_ms < 1000 ? `${member.elapsed_ms}ms` : `${(member.elapsed_ms / 1000).toFixed(1)}s`}</span>}
                    <ChevronRight size={12} className={`shrink-0 transition-transform ${open ? "rotate-90" : ""}`} style={{ color: "var(--text-tertiary)" }} />
                  </div>
                  <div className={`${open ? "" : "truncate"} mt-0.5 text-[11px] leading-relaxed`} style={{ color: "var(--text-secondary)" }}>{member.task || "等待协调者分配任务"}</div>
                  {!open && member.preview ? <div className="mt-1 text-[10.5px] line-clamp-1" style={{ color: "var(--text-tertiary)" }}>{member.preview}</div> : null}
                </div>
              </div>
              </button>
              {open && <div className="mx-3 mb-2.5 ml-9 rounded-lg p-2.5" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
                <div className="text-[9.5px] font-semibold" style={{ color: "var(--text-tertiary)" }}>{status === "done" ? "返回主任务的结果" : status === "failed" ? "失败信息" : "当前进展"}</div>
                <div className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed" style={{ color: member.preview ? "var(--text-primary)" : "var(--text-tertiary)" }}>{member.preview || (status === "running" ? "子任务正在独立上下文中执行，完成后结果会回传到主任务。" : "尚无返回内容。")}</div>
                {(member.session_id || member.thread_id) ? <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
                  {member.session_id ? <span className="font-mono">session {member.session_id}</span> : null}
                  {member.thread_id ? <span className="font-mono">thread {member.thread_id}</span> : null}
                  {typeof member.tool_calls === "number" ? <span>{member.tool_calls} 次工具调用</span> : null}
                </div> : null}
                {member.allowed_tools?.length ? <div className="mt-2 text-[9.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>能力边界：{member.allowed_tools.join("、")}</div> : null}
                {member.session_steps?.length ? <div className="mt-1 text-[9.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>执行步骤：{member.session_steps.join(" → ")}</div> : null}
                {actionable && onMessage ? <div className="mt-2.5 pt-2.5" style={{ borderTop: "1px solid var(--border)" }}>
                  <div className="text-[9.5px] font-semibold" style={{ color: "var(--text-tertiary)" }}>执行中纠正</div>
                  <textarea
                    value={drafts[sessionId] || ""}
                    onChange={(event) => setDrafts(current => ({ ...current, [sessionId]: event.target.value.slice(0, 4000) }))}
                    rows={2}
                    placeholder="补充约束、纠正方向或要求重新核验；将在下一次模型回合前通过持久邮箱送达。"
                    className="mt-1.5 w-full resize-none rounded-lg px-2.5 py-2 text-[10.5px] outline-none"
                    style={{ color: "var(--text-primary)", background: "var(--bg-secondary)", border: "1px solid var(--border)" }}
                  />
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <button type="button" disabled={busy || !(drafts[sessionId] || "").trim()}
                      onClick={async () => {
                        const content = (drafts[sessionId] || "").trim();
                        if (!content) return;
                        try {
                          await onMessage(sessionId, content);
                          setDrafts(current => ({ ...current, [sessionId]: "" }));
                        } catch {
                          // 父级会显示服务端返回的有界错误；保留草稿供用户重试。
                        }
                      }}
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium disabled:opacity-40"
                      style={{ color: "#fff", background: "var(--accent)" }}>
                      {busy ? <Loader2 size={10} className="animate-spin" /> : <Send size={10} />}发送纠正
                    </button>
                    {onStop ? <button type="button" disabled={busy} onClick={() => onStop(sessionId)}
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] disabled:opacity-40"
                      style={{ color: "#b42318", border: "1px solid rgba(180,35,24,.25)" }}>
                      <CircleStop size={10} />停止此 Agent
                    </button> : null}
                  </div>
                  {actionError ? <div className="mt-1.5 text-[9.5px]" style={{ color: "#b42318" }}>{actionError}</div> : null}
                </div> : null}
              </div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
