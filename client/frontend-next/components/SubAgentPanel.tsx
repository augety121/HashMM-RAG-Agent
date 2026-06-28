"use client";
/** components/SubAgentPanel.tsx — 子 agent 编排实时可视（V103.30）。
 *
 * 渲染 orchestrator 的「团队/DAG」：每个子 agent（专员）一张卡，状态实时点亮
 * 待命(灰) → 运行中(脉冲) → 完成(绿勾 + 耗时 + 结果预览)。
 * 流式区实时用 + MsgBubble 回放用，同一个组件。这是 Marvis/大厂云端给不了的「在场感」。
 */
import { Users, Loader2, CheckCircle2, Circle } from "lucide-react";

export type OrchMember = {
  id: string; step?: number; role_label?: string; task?: string;
  status?: "pending" | "running" | "done"; elapsed_ms?: number; preview?: string;
};
export type Orchestration = { strategy?: string; members: OrchMember[] };

const STRATEGY_LABEL: Record<string, string> = {
  compare: "对比", sequential: "串行", single: "单步", parallel: "并行",
};

export function SubAgentPanel({ orch }: { orch?: Orchestration | null }) {
  if (!orch || !Array.isArray(orch.members) || orch.members.length === 0) return null;
  const total = orch.members.length;
  const done = orch.members.filter(m => m.status === "done").length;

  return (
    <div className="rounded-xl mb-3 overflow-hidden" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
        <Users size={13} style={{ color: "var(--accent)" }} />
        <span className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
          子 agent 编排
        </span>
        {orch.strategy && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>
            {STRATEGY_LABEL[orch.strategy] || orch.strategy}
          </span>
        )}
        <span className="ml-auto text-[10.5px] font-mono" style={{ color: "var(--text-tertiary)" }}>{done}/{total}</span>
      </div>
      <div className="flex flex-col">
        {orch.members.map((m, i) => {
          const st = m.status || "pending";
          return (
            <div key={m.id || i} className="flex items-start gap-2.5 px-3 py-2"
              style={{ borderTop: i > 0 ? "1px solid var(--border)" : "none" }}>
              <div className="mt-0.5 flex-shrink-0">
                {st === "done"
                  ? <CheckCircle2 size={14} style={{ color: "#16a34a" }} />
                  : st === "running"
                    ? <Loader2 size={14} className="animate-spin" style={{ color: "var(--accent)" }} />
                    : <Circle size={14} style={{ color: "var(--text-tertiary)" }} />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  {m.role_label && (
                    <span className="text-[11px] font-medium px-1.5 py-0.5 rounded"
                      style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{m.role_label}</span>
                  )}
                  <span className="text-[12px] truncate" style={{ color: st === "pending" ? "var(--text-tertiary)" : "var(--text-primary)" }}>
                    {m.task || `步骤 ${m.step ?? i + 1}`}
                  </span>
                  {st === "done" && typeof m.elapsed_ms === "number" && m.elapsed_ms > 0 && (
                    <span className="ml-auto text-[10px] font-mono flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>
                      {m.elapsed_ms < 1000 ? `${m.elapsed_ms}ms` : `${(m.elapsed_ms / 1000).toFixed(1)}s`}
                    </span>
                  )}
                </div>
                {st === "done" && m.preview && (
                  <div className="text-[11px] mt-1 line-clamp-2 leading-snug" style={{ color: "var(--text-tertiary)" }}>
                    {m.preview}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
