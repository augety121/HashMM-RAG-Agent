"use client";
import { useState } from "react";
import { Loader2, CheckCircle2, AlertCircle, ChevronDown, Brain, Zap, MessageSquare, Search, Target, Package, PenLine, Wrench, Code2, Network, Bot, Puzzle, ShieldCheck, TrendingUp, Archive, Lock, CheckCheck, Sparkles, ListChecks, Quote, RefreshCw, Eye } from "lucide-react";

export interface AgentStep {
  id: string;
  node: string;       // "classify" | "retrieve" | "narrate" | "tool" | "done" | ...
  detail: string;     // narrate=完整说明文字；tool=参数/结果摘要；其他=人话描述
  tool?: string;      // V49: 工具名（node === "tool" 时由新协议带上）
  status: "running" | "done" | "error";
  elapsed_ms?: number;
  result_preview?: string;
}

interface Props {
  steps: AgentStep[];
  totalElapsed?: number;
  visible: boolean;
  onToggle: () => void;
}

const NODE_LABELS: Record<string, { Icon: typeof Brain; label: string }> = {
  classify: { Icon: Brain, label: "意图分析" },
  skill_match: { Icon: Zap, label: "技能匹配" },
  memory_recall: { Icon: MessageSquare, label: "记忆检索" },
  retrieve: { Icon: Search, label: "知识检索" },
  rerank: { Icon: Target, label: "精排筛选" },
  context_build: { Icon: Package, label: "构建上下文" },
  generate: { Icon: PenLine, label: "生成回答" },
  think: { Icon: MessageSquare, label: "思考" },
  tool: { Icon: Wrench, label: "工具调用" },
  code: { Icon: Code2, label: "代码执行" },
  kg: { Icon: Network, label: "图谱检索" },
  sub_agent: { Icon: Bot, label: "子任务" },
  decompose: { Icon: Puzzle, label: "任务分解" },
  safety_check: { Icon: ShieldCheck, label: "安全检查" },
  evolution: { Icon: TrendingUp, label: "自我进化" },
  compact: { Icon: Archive, label: "上下文压缩" },
  permission: { Icon: Lock, label: "权限" },
  done: { Icon: CheckCheck, label: "完成" },
  // V86: loop 工程质量节点（后端一直在发，此前缺映射只能落到兜底图标）
  dod: { Icon: ListChecks, label: "完成度检查" },
  citation: { Icon: Quote, label: "引用校验" },
  verify: { Icon: ShieldCheck, label: "语法验证" },
  retrieval_adapt: { Icon: Zap, label: "检索改写" },
  error_recover: { Icon: RefreshCw, label: "错误恢复" },
  vision: { Icon: Eye, label: "图像理解" },
};

function StatusIcon({ status, size = 13 }: { status: string; size?: number }) {
  if (status === "running") return <Loader2 size={size} className="animate-spin" style={{ color: "var(--accent)" }} />;
  if (status === "done") return <CheckCircle2 size={size} style={{ color: "#22c55e" }} />;
  return <AlertCircle size={size} style={{ color: "#ef4444" }} />;
}

/** 提取工具名：新协议直接用 step.tool；旧数据剥掉 emoji 前缀后取首词。 */
function toolNameOf(s: AgentStep): string {
  if (s.tool) return s.tool;
  const d = (s.detail || "").replace(/^[^\w]+/, "");
  return d.split(/[:(（\s]/)[0].trim();
}

/**
 * 旧数据兼容清洗（仅影响 V48 及更早的历史消息；新协议一个工具本来就只有一条）：
 * - 丢弃 "解析文本工具调用"、"调用 xxx..." 中间态噪音
 * - 合并 "🔧 x(args)" + "✅ x (44ms)" 这种 start/done 冗余对（V48 的合并因
 *   emoji 前缀导致名字对不上而失效，这是当时时间线满屏冗余对的根因）
 */
function normalizeSteps(steps: AgentStep[]): AgentStep[] {
  const out: AgentStep[] = [];
  for (const s of steps) {
    const detail = s.detail || "";
    if (s.node === "tool" && (detail.includes("解析文本工具调用") || /^调用\s/.test(detail))) {
      continue;
    }
    const prev = out[out.length - 1];
    if (
      s.node === "tool" && detail.startsWith("✅") &&
      prev && prev.node === "tool" && (prev.detail || "").startsWith("🔧") &&
      toolNameOf(prev) === toolNameOf(s)
    ) {
      out[out.length - 1] = s;   // 旧格式 start/done 对 → 留 done 一条
    } else {
      out.push(s);
    }
  }
  return out;
}

function fmtMs(ms?: number): string {
  if (ms == null || ms <= 0) return "";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

export function AgentLog({ steps, totalElapsed, visible, onToggle }: Props) {
  const [expandedStep, setExpandedStep] = useState<string | null>(null);

  if (steps.length === 0) return null;

  const view = normalizeSteps(steps);
  const isRunning = view.some(s => s.status === "running");
  const stepCount = view.filter(s => s.node !== "narrate").length;
  const runningStep = [...view].reverse().find(s => s.status === "running");
  const headerText = isRunning
    ? (runningStep && runningStep.node === "tool"
        ? `正在执行 ${toolNameOf(runningStep)}…` : "执行中…")
    : `${stepCount} 个步骤`;

  return (
    <div className="mt-3 mb-1">
      {/* Toggle bar */}
      <button onClick={onToggle}
        className="flex items-center gap-2 text-[11px] font-medium transition-colors"
        style={{ color: "var(--text-tertiary)" }}>
        <Zap size={11} />
        <span>{headerText}</span>
        {!isRunning && totalElapsed != null && totalElapsed > 0 && (
          <span className="font-mono text-[10px]">{fmtMs(totalElapsed)}</span>
        )}
        <ChevronDown size={11} className={`transition-transform ${visible ? "rotate-180" : ""}`} />
      </button>

      {/* Claude 式交错时间线：说明=正文段落，工具=紧凑状态卡片，其他=小行 */}
      {visible && (
        <div className="mt-2 anim-fade-up">
          {view.map((step, i) => {
            const key = step.id || `s-${i}`;

            // ① 模型在工具之间的说明 → 渲染为正文段落（"中间有回答"的核心）
            if (step.node === "narrate") {
              return <div key={key} className="al-narrate">{step.detail}</div>;
            }

            // ② 工具调用 → 一行一卡，状态图标原地更新，失败标红
            if (step.node === "tool") {
              const isExpanded = expandedStep === key;
              const name = toolNameOf(step) || "tool";
              // 去掉旧格式的 emoji/名字前缀，只留参数/结果摘要
              const detail = (step.detail || "")
                .replace(/^[✅🔧\s]*/, "")
                .replace(new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*[:(（]?\\s*`), "");
              return (
                <div key={key}>
                  <div className={`al-tool-card ${step.status === "error" ? "al-error" : ""}`}
                    onClick={() => setExpandedStep(isExpanded ? null : key)}>
                    <StatusIcon status={step.status} />
                    <Wrench size={11} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
                    <span className="al-tool-name">{name}</span>
                    {detail && <span className="al-tool-detail">{detail}</span>}
                    <span className="al-tool-ms">{fmtMs(step.elapsed_ms)}</span>
                  </div>
                  {isExpanded && step.result_preview && (
                    <div className="al-result-preview">{step.result_preview.slice(0, 300)}</div>
                  )}
                </div>
              );
            }

            // ③ 其他节点（意图/检索/思考/完成…）→ 轻量小行
            const meta = NODE_LABELS[step.node] || { Icon: Sparkles, label: step.node };
            return (
              <div key={key} className="al-step-row">
                <StatusIcon status={step.status} size={11} />
                {meta.Icon && <meta.Icon size={11} style={{ color: "var(--text-tertiary)" }} />}
                <span className="font-medium" style={{ color: "var(--text-secondary)" }}>{meta.label}</span>
                {step.detail && (
                  <span className="truncate" style={{ color: "var(--text-tertiary)", minWidth: 0 }}>
                    {step.detail}
                  </span>
                )}
                {step.elapsed_ms != null && step.elapsed_ms > 0 && (
                  <span className="font-mono text-[9px] ml-auto flex-shrink-0"
                    style={{ color: "var(--text-tertiary)" }}>{fmtMs(step.elapsed_ms)}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
