"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Clock3, ExternalLink, ShieldCheck, XCircle } from "lucide-react";
import { getDesktop, type DesktopApprovalRecord, type DesktopApprovalSnapshot } from "@/lib/desktop";

const SOURCE_LABELS: Record<string, string> = {
  "browser-navigation": "浏览器站点",
  "browser-use": "Browser Use",
  "computer-use": "Computer Use",
  checkpoint: "检查点",
  workspace: "工作区",
  "software-update": "软件更新",
  desktop: "桌面能力",
};

export function useDesktopApprovals() {
  const [snapshot, setSnapshot] = useState<DesktopApprovalSnapshot | null>(null);

  useEffect(() => {
    const desktop = getDesktop();
    if (!desktop?.listDesktopApprovals) return;
    let alive = true;
    const update = (next: DesktopApprovalSnapshot) => { if (alive && next?.schema) setSnapshot(next); };
    void desktop.listDesktopApprovals().then(update).catch(() => { /* old desktop shell */ });
    const off = desktop.onDesktopApprovalChanged?.(update);
    return () => { alive = false; off?.(); };
  }, []);

  return snapshot;
}

function outcome(record: DesktopApprovalRecord) {
  if (record.status === "pending") return { label: "等待确认", color: "#b45309", Icon: Clock3 };
  if (record.status === "interrupted") return { label: "已安全中止", color: "var(--text-tertiary)", Icon: XCircle };
  const button = record.prompt.buttons.find(item => item.id === record.decision);
  const denied = record.decision === record.prompt.cancelId;
  return { label: button?.label || (denied ? "已拒绝" : "已处理"), color: denied ? "#b42318" : "#15803d", Icon: denied ? XCircle : CheckCircle2 };
}

function shortTime(value: number) {
  if (!value) return "";
  try {
    const date = new Date(value);
    const today = new Date();
    if (date.toDateString() === today.toDateString()) return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  } catch { return ""; }
}

export function DesktopApprovalCenter({
  snapshot,
  currentTaskId,
  taskNames,
}: {
  snapshot: DesktopApprovalSnapshot | null;
  currentTaskId: string;
  taskNames: Record<string, string>;
}) {
  const [error, setError] = useState("");
  const items = useMemo(() => {
    const rows = snapshot?.items || [];
    const pending = rows.filter(row => row.status === "pending");
    const recentCurrent = rows.filter(row => row.status !== "pending" && row.prompt.taskId === currentTaskId).slice(0, 8);
    return [...pending, ...recentCurrent];
  }, [snapshot, currentTaskId]);

  if (!snapshot) return null;

  const present = async (id: string) => {
    setError("");
    try {
      const result = await getDesktop()?.presentDesktopPrompt?.(id);
      if (result && !result.ok) setError(result.error || "该审批暂时不能处理");
    } catch { setError("审批界面暂时不可用"); }
  };

  const decide = (record: DesktopApprovalRecord, decision: string) => {
    setError("");
    getDesktop()?.resolveDesktopPrompt?.(record.id, decision);
  };

  return (
    <section className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
      <div className="px-3 py-2 flex items-center gap-2" style={{ background: "var(--bg-secondary)" }}>
        <ShieldCheck size={13} style={{ color: "var(--accent)" }} />
        <span className="text-[11.5px] font-semibold" style={{ color: "var(--text-primary)" }}>操作确认</span>
        <span className="ml-auto text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
          {items.some(row => row.status === "pending") ? `${items.filter(row => row.status === "pending").length} 项待处理` : "当前 Chat 无待处理"}
        </span>
      </div>
      {error ? <div className="px-3 py-2 text-[10px]" style={{ color: "#b42318", borderTop: "1px solid var(--border)" }}>{error}</div> : null}
      {items.length ? items.map((record, index) => {
        const state = outcome(record);
        const active = record.id === snapshot.activeId;
        const sameTask = record.prompt.taskId === currentTaskId;
        const taskLabel = sameTask ? "当前 Chat" : record.prompt.taskId === "application" ? "应用" : (taskNames[record.prompt.taskId] || "其他任务");
        return (
          <div key={record.id} className="px-3 py-2.5" style={{ borderTop: index || error ? "1px solid var(--border)" : "none", background: "var(--bg-primary)" }}>
            <div className="flex items-start gap-2">
              <state.Icon size={13} className="mt-0.5 flex-none" style={{ color: state.color }} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="min-w-0 flex-1 truncate text-[11px] font-medium" style={{ color: "var(--text-primary)" }}>{record.prompt.title}</span>
                  <span className="text-[9px] flex-none" style={{ color: state.color }}>{state.label}</span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
                  <span>{SOURCE_LABELS[record.prompt.source] || record.prompt.source}</span><span>·</span><span className="truncate">{taskLabel}</span><span className="ml-auto flex-none">{shortTime(record.requestedAt)}</span>
                </div>
                {record.prompt.target ? <div className="mt-1 truncate text-[9.5px]" title={record.prompt.target} style={{ color: "var(--text-secondary)" }}>{record.prompt.target}</div> : null}
                {record.status === "pending" ? (
                  active ? <div className="mt-2 flex flex-wrap gap-1.5">
                    {record.prompt.buttons.map(button => <button key={button.id} onClick={() => decide(record, button.id)}
                      className="rounded-lg px-2 py-1 text-[9.5px] font-medium"
                      style={button.tone === "primary" ? { background: "var(--accent)", color: "#fff" } : button.tone === "danger" ? { border: "1px solid rgba(239,68,68,.25)", color: "#b42318" } : { border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                      {button.label}
                    </button>)}
                    <button onClick={() => void present(record.id)} className="ml-auto inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[9.5px]" style={{ color: "var(--accent)" }}><ExternalLink size={10} />查看详情</button>
                  </div> : <div className="mt-1.5 inline-flex items-center gap-1 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}><AlertTriangle size={10} />等待前一项确认后自动继续</div>
                ) : null}
              </div>
            </div>
          </div>
        );
      }) : <div className="px-3 py-4 text-center text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>当前 Chat 没有需要处理的桌面操作。</div>}
    </section>
  );
}
