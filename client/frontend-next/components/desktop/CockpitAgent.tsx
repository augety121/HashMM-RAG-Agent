"use client";
/** components/desktop/CockpitAgent.tsx — 在工作台里直接让 HashMM 自己动手（V103）。
 *
 *  之前 FanBox 驾驶舱只能在右侧终端里看 *外部* CLI（Claude Code / Codex）干活；HashMM 自己的
 *  「电脑操作」agent 跑在主聊天里，步骤进的是聊天时间线，cockpit 里看不到。本组件把它接进
 *  cockpit：给个目标 → 复用主界面那套 **runComputerUse 循环**（lib/cu.ts，含 AgentLoopController
 *  预算/无进展熔断、视觉降级、安全级别注入、危险操作主进程确认）→ 步骤就地显示。
 *
 *  关键：agent 每写一个文件，左侧文件卡片照常点亮——靠的是 WorkbenchView 已有的 fs:changed
 *  联动，本组件不重复造轮子，只是把"谁在动手"从外部 CLI 扩成"也能是 HashMM 自己"。
 */
import { useRef, useState } from "react";
import { Bot, Play, Square, Loader2, CheckCircle2, AlertCircle, ChevronDown, ChevronRight } from "lucide-react";
import { runComputerUse, type CuStep } from "@/lib/cu";

type Step = CuStep & { id: string };

export function CockpitAgent() {
  const [open, setOpen] = useState(false);
  const [goal, setGoal] = useState("");
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [text, setText] = useState("");
  const stopRef = useRef(false);

  const run = async () => {
    const g = goal.trim();
    if (!g || running) return;
    setRunning(true); setSteps([]); setText(""); stopRef.current = false;
    try {
      await runComputerUse(g, {
        onText: (t) => setText(prev => prev + t),
        onStep: (s) => setSteps(prev => {
          // running → done/error：就地更新同名最近一条 running，而不是再追加
          if (s.status !== "running") {
            const idx = [...prev].reverse().findIndex(x => x.node === s.node && x.status === "running");
            if (idx >= 0) {
              const real = prev.length - 1 - idx;
              const next = prev.slice();
              next[real] = { ...next[real], status: s.status, detail: s.detail };
              return next;
            }
          }
          return [...prev, { ...s, id: `cs-${prev.length}-${Date.now()}` }];
        }),
        shouldStop: () => stopRef.current,
      });
    } catch (e: any) {
      setSteps(prev => [...prev, { node: "done", detail: "失败：" + (e?.message || String(e)), status: "error", id: `cs-err-${Date.now()}` }]);
    }
    setRunning(false);
  };
  const stop = () => { stopRef.current = true; };

  return (
    <div className="flex-shrink-0" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
      <button onClick={() => setOpen(o => !o)} className="w-full flex items-center gap-2 px-3 py-2 text-[12px]" style={{ color: "var(--text-secondary)" }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <Bot size={13} style={{ color: "var(--accent)" }} />
        <span className="font-medium">让 HashMM 自己动手</span>
        {running && <Loader2 size={12} className="animate-spin" style={{ color: "var(--accent)" }} />}
        <span className="flex-1" />
        {steps.length > 0 && <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{steps.length} 步</span>}
      </button>
      {open && (
        <div className="px-3 pb-3">
          <div className="flex gap-1.5">
            <input value={goal} onChange={e => setGoal(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") run(); }}
              placeholder="给个目标，如：把这个目录里的截图按日期改名"
              disabled={running}
              className="flex-1 px-3 py-2 rounded-xl text-[12px] outline-none disabled:opacity-60"
              style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            {running ? (
              <button onClick={stop} className="px-3 py-2 rounded-xl text-[12px] font-medium text-white flex items-center gap-1"
                style={{ background: "var(--danger, #DC2626)" }}><Square size={11} /> 停止</button>
            ) : (
              <button onClick={run} className="px-4 py-2 rounded-xl text-[12px] font-medium text-white flex items-center gap-1"
                style={{ background: "var(--accent)" }}><Play size={11} /> 运行</button>
            )}
          </div>
          {(steps.length > 0 || text) && (
            <div className="mt-2 max-h-[220px] overflow-auto rounded-xl p-2" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)" }}>
              {steps.map(s => (
                <div key={s.id} className="flex items-start gap-1.5 py-0.5 text-[11px] font-mono">
                  {s.status === "running" ? <Loader2 size={11} className="animate-spin mt-0.5 flex-shrink-0" style={{ color: "var(--accent)" }} />
                    : s.status === "error" ? <AlertCircle size={11} className="mt-0.5 flex-shrink-0" style={{ color: "var(--danger, #DC2626)" }} />
                    : <CheckCircle2 size={11} className="mt-0.5 flex-shrink-0" style={{ color: "var(--success, #16A34A)" }} />}
                  <span className="break-all" style={{ color: "var(--text-secondary)" }}>{s.detail}</span>
                </div>
              ))}
              {text && <pre className="m-0 mt-1 whitespace-pre-wrap break-words text-[11px]" style={{ color: "var(--text-primary)" }}>{text}</pre>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
