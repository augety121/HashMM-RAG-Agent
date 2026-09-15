"use client";
import { useState } from "react";
import {
  CheckCircle2, Loader2, AlertCircle, ChevronDown,
  Brain, Search, Network, Wrench, FileText, Sparkles,
  ShieldCheck, Quote, ListChecks, Zap, RefreshCw, Flag,
} from "lucide-react";

/**
 * AgentRunTimeline —— 把一轮 agent run 的所有 trace 步骤按"大阶段"(phase)分组，
 * 串成一条 deep-research 风的可回放时间线。
 *
 * 数据来源：后端 hashmm/api/run_timeline.py 在 HASHMM_AGENT_TIMELINE=1 时，给每个
 * trace step 注入了 `phase`(understand/retrieve/reason/act/synthesize) 与单调递增的
 * `step_id`。本组件据此分组、排序、折叠展示。
 *
 * 兼容/降级：
 *   - 若某些 step 没有 phase(后端未开时间线，或老事件)，按其 node 即时推断 phase，
 *     推断不出归到 "other" —— 永远能渲染，不会因缺字段崩。
 *   - 与 AgentLog 的 AgentStep 字段兼容(id/node/detail/status/elapsed_ms)，可直接复用同一份 steps。
 *
 * 这是纯增量组件：不改 AgentLog、不改 ChatArea。想用可视化时间线时挂上即可。
 */

export interface TimelineStep {
  id: string;
  node: string;
  detail: string;
  status?: "running" | "done" | "error";
  elapsed_ms?: number;
  result_preview?: string;
  phase?: string;     // understand | retrieve | reason | act | synthesize | other
  step_id?: number;
}

interface Props {
  steps: TimelineStep[];
  totalElapsed?: number;
  defaultExpanded?: boolean;
}

// node → phase 兜底推断（与后端 run_timeline._NODE_PHASE 保持一致）。
const NODE_PHASE: Record<string, string> = {
  classify: "understand", skill_match: "understand", memory_recall: "understand",
  safety_check: "understand", context_build: "understand",
  retrieve: "retrieve", rerank: "retrieve", kg: "retrieve",
  decompose: "reason", sub_agent: "reason", evolution: "reason",
  tool: "act", code: "act",
  generate: "synthesize", done: "synthesize",
  dod: "synthesize", citation: "synthesize", verify: "act",
  retrieval_adapt: "retrieve", error_recover: "act",
};

// 5 大阶段的展示元信息（顺序即时间线从上到下的顺序）。
const PHASES: { key: string; label: string; Icon: typeof Brain; color: string }[] = [
  { key: "understand", label: "理解", Icon: Brain, color: "#8b5cf6" },
  { key: "retrieve", label: "检索", Icon: Search, color: "#3b82f6" },
  { key: "reason", label: "推理", Icon: Network, color: "#f59e0b" },
  { key: "act", label: "执行", Icon: Wrench, color: "#10b981" },
  { key: "synthesize", label: "合成", Icon: FileText, color: "#ec4899" },
  { key: "other", label: "其他", Icon: Sparkles, color: "var(--text-tertiary)" },
];

const NODE_LABELS: Record<string, string> = {
  classify: "意图分析", skill_match: "技能匹配", memory_recall: "记忆检索",
  retrieve: "知识检索", rerank: "精排筛选", context_build: "构建上下文",
  generate: "生成回答", tool: "工具调用", code: "代码执行", kg: "图谱检索",
  sub_agent: "子任务", decompose: "任务分解", safety_check: "安全检查",
  evolution: "自我进化", done: "完成",
  dod: "完成度检查", citation: "引用校验", verify: "语法验证",
  retrieval_adapt: "检索改写", error_recover: "错误恢复",
};

function phaseOf(s: TimelineStep): string {
  if (s.phase && s.phase.length) return s.phase;
  return NODE_PHASE[s.node] || "other";
}

function StatusIcon({ status }: { status?: string }) {
  if (status === "running") return <Loader2 size={12} className="animate-spin" style={{ color: "var(--accent)" }} />;
  if (status === "error") return <AlertCircle size={12} style={{ color: "#ef4444" }} />;
  return <CheckCircle2 size={12} style={{ color: "#22c55e" }} />;
}

/** V82: 从 trace steps 聚合 Loop 工程质量信号 → 可视徽章。
 *  绿色=质量门通过（引用校验/任务完成/语法验证）；琥珀=自适应触发（检索改写/错误恢复）。 */
function QualityBadges({ steps }: { steps: TimelineStep[] }) {
  const find = (node: string) => steps.find(s => s.node === node);
  const badges: { icon: typeof ShieldCheck; label: string; tone: "ok" | "warn"; tip?: string }[] = [];

  // 质量门（绿）：从 detail 文本判定通过/问题
  const cit = find("citation");
  if (cit) badges.push({ icon: Quote, tone: cit.detail.includes("有效") ? "ok" : "warn",
    label: cit.detail.includes("有效") ? "引用校验" : "引用已修正",
    tip: cit.detail || "回答中的引用编号已与检索结果核对" });
  const dod = find("dod");
  if (dod) {
    const m = dod.detail.match(/(\d+)\s*项全部完成/);
    badges.push({ icon: ListChecks, tone: m ? "ok" : "warn",
      label: m ? `任务清单 ${m[1]}/${m[1]}` : "任务补全",
      tip: dod.detail || "已核对任务清单完成度" });
  }
  const verify = steps.find(s => s.node === "verify");
  if (verify) badges.push({ icon: ShieldCheck, tone: verify.detail.includes("通过") ? "ok" : "warn",
    label: "语法验证", tip: verify.detail || "已对生成的代码做语法检查" });

  // 自适应（琥珀）：触发即展示次数
  const radapt = steps.filter(s => s.node === "retrieval_adapt").length;
  if (radapt) badges.push({ icon: Zap, tone: "warn", label: `检索改写 ${radapt}`,
    tip: "检索结果质量低时自动改写查询重试，提升召回" });
  const erec = steps.filter(s => s.node === "error_recover").length;
  if (erec) badges.push({ icon: RefreshCw, tone: "warn", label: `错误恢复 ${erec}`,
    tip: "工具执行失败时自动分析原因并换路重试" });

  if (!badges.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mb-2">
      {badges.map((b, i) => {
        const Icon = b.icon;
        const ok = b.tone === "ok";
        return (
          <span key={i} title={b.tip || b.label}
            className="inline-flex items-center gap-1 px-2 py-[3px] rounded-full text-[10px] font-medium cursor-default"
            style={{
              background: ok ? "rgba(16,163,74,.10)" : "rgba(217,119,6,.10)",
              color: ok ? "#16A34A" : "#D97706",
              border: `1px solid ${ok ? "rgba(16,163,74,.25)" : "rgba(217,119,6,.25)"}`,
            }}>
            <Icon size={11} /> {b.label}
          </span>
        );
      })}
    </div>
  );
}

export function AgentRunTimeline({ steps, totalElapsed, defaultExpanded = true }: Props) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  if (!steps || steps.length === 0) return null;

  // 按 step_id 稳定排序（有则用，没有保持原序）。
  const ordered = [...steps].sort((a, b) => {
    const ai = a.step_id ?? Number.MAX_SAFE_INTEGER;
    const bi = b.step_id ?? Number.MAX_SAFE_INTEGER;
    return ai - bi;
  });

  // 分组：phase → steps。
  const grouped: Record<string, TimelineStep[]> = {};
  for (const s of ordered) {
    const p = phaseOf(s);
    (grouped[p] = grouped[p] || []).push(s);
  }

  const activePhases = PHASES.filter(p => (grouped[p.key] || []).length > 0);
  const isRunning = steps.some(s => s.status === "running");

  return (
    <div className="mt-3 mb-1">
      {/* 顶部摘要 */}
      <div className="flex items-center gap-2 text-[11px] font-medium mb-2" style={{ color: "var(--text-tertiary)" }}>
        <span>{isRunning ? "Agent 执行中..." : `Agent 轨迹 · ${steps.length} 步 / ${activePhases.length} 阶段`}</span>
        {totalElapsed != null && totalElapsed > 0 && (
          <span className="font-mono text-[10px]">
            {totalElapsed < 1000 ? `${totalElapsed}ms` : `${(totalElapsed / 1000).toFixed(1)}s`}
          </span>
        )}
      </div>

      {/* V82: Loop 工程质量徽章条——让用户看见 agent 的自我把关与自适应 */}
      {!isRunning && <QualityBadges steps={steps} />}

      <div className="space-y-1">
        {activePhases.map((phase, pi) => {
          const items = grouped[phase.key];
          const open = collapsed[phase.key] === undefined ? defaultExpanded : !collapsed[phase.key];
          const Icon = phase.Icon;
          return (
            <div key={phase.key}>
              {/* 阶段头 */}
              <button
                onClick={() => setCollapsed(c => ({ ...c, [phase.key]: open }))}
                className="flex items-center gap-2 w-full px-2 py-1 rounded-md transition-colors hover:bg-[var(--bg-secondary)]"
              >
                <span className="flex items-center justify-center rounded-full"
                  style={{ width: 18, height: 18, background: phase.color, color: "#fff" }}>
                  <Icon size={11} />
                </span>
                <span className="text-[11px] font-semibold" style={{ color: "var(--text-secondary)" }}>
                  {phase.label}
                </span>
                <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                  {items.length}
                </span>
                <ChevronDown size={11} className={`ml-auto transition-transform ${open ? "rotate-180" : ""}`}
                  style={{ color: "var(--text-tertiary)" }} />
              </button>

              {/* 阶段内的步骤 */}
              {open && (
                <div className="ml-3 pl-3 mt-0.5 space-y-0.5 anim-fade-up"
                  style={{ borderLeft: `1px solid var(--border)` }}>
                  {items.map((step, i) => (
                    <div key={step.id || `${phase.key}-${i}`}
                      className="flex items-start gap-2 px-2 py-1 rounded-md transition-colors hover:bg-[var(--bg-secondary)]">
                      <div className="pt-0.5"><StatusIcon status={step.status} /></div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
                            {NODE_LABELS[step.node] || step.node}
                          </span>
                          {step.step_id != null && (
                            <span className="text-[9px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                              #{step.step_id}
                            </span>
                          )}
                          {step.elapsed_ms != null && (
                            <span className="text-[9px] font-mono ml-auto" style={{ color: "var(--text-tertiary)" }}>
                              {step.elapsed_ms}ms
                            </span>
                          )}
                        </div>
                        {step.detail && (
                          <div className="text-[10px] mt-0.5 truncate" style={{ color: "var(--text-tertiary)" }}>
                            {step.detail}
                          </div>
                        )}
                        {step.result_preview && (
                          <div className="mt-1 text-[10px] px-2 py-1 rounded leading-relaxed"
                            style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
                            {step.result_preview.slice(0, 300)}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 阶段间连接线 */}
              {pi < activePhases.length - 1 && (
                <div className="ml-[10px] w-px h-2" style={{ background: "var(--border)" }} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default AgentRunTimeline;
