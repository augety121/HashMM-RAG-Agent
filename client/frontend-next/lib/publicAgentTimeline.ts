import type { HookRun } from "@/lib/types";

export interface AgentTimelineEvent {
  kind?: string;
  node: string;
  detail?: string;
  tool?: string;
  // Older persisted runs contain provider/tool specific states such as
  // "failed", "denied" and "completed".  Accept them at the trust boundary
  // and normalize below instead of forcing every caller to cast unsafe data.
  status?: string;
  elapsed_ms?: number;
  id?: string;
  hooks?: HookRun[];
}

export interface PublicAgentTimelineEvent {
  kind: string;
  node: string;
  detail: string;
  tool?: string;
  status: "done" | "running" | "error";
  elapsed_ms?: number;
  id: string;
  hooks?: HookRun[];
}

/*
 * Transport/lifecycle events are useful in the administrator inspector, but
 * they are not user-facing work.  In particular, they must never be presented
 * as the model's chain of thought.
 */
const HIDDEN_PROTOCOL_NODES = new Set([
  "turn_admitted",
  "context_assembled",
  "iteration_started",
  "iteration_finished",
  "turn_finished",
  "context_checkpoint",
  "model",
  "persona",
  "runtime",
]);

const PUBLIC_NODE_COPY: Record<string, { node: string; detail: string }> = {
  analysis: { node: "classify", detail: "正在理解目标并选择下一步" },
  classify: { node: "classify", detail: "已理解目标、范围与交付要求" },
  intent: { node: "classify", detail: "已确认本轮工作目标" },
  skill_match: { node: "skill_match", detail: "已选择适合本任务的工作方法" },
  memory_recall: { node: "memory_recall", detail: "已读取与本轮有关的已授权上下文" },
  retrieve: { node: "retrieve", detail: "正在检索与任务直接相关的资料" },
  retrieval_adapt: { node: "retrieval_adapt", detail: "候选质量不足，已调整检索条件" },
  rerank: { node: "rerank", detail: "正在筛选更可靠、更相关的候选" },
  kg: { node: "kg", detail: "正在核对资料之间的关系" },
  context_build: { node: "context_build", detail: "已整理本轮需要的来源与约束" },
  decompose: { node: "decompose", detail: "已将复杂目标拆分为可验收步骤" },
  sub_agent: { node: "sub_agent", detail: "协作成员正在处理分工任务" },
  safety_check: { node: "safety_check", detail: "已检查权限、范围与潜在风险" },
  permission: { node: "permission", detail: "正在确认本次操作所需权限" },
  compact: { node: "compact", detail: "已整理较早上下文，保留目标、事实和未完成事项" },
  generate: { node: "generate", detail: "正在整理结果与交付物" },
  dod: { node: "dod", detail: "正在逐项核对任务清单与交付要求" },
  acceptance: { node: "dod", detail: "正在检查交付是否完整" },
  citation: { node: "citation", detail: "正在核对引用编号与来源" },
  faithfulness: { node: "citation", detail: "正在核对事实主张与证据" },
  verify: { node: "verify", detail: "正在验证生成结果" },
  error_recover: { node: "error_recover", detail: "上一步未成功，正在换用可验证的方法重试" },
  delivery: { node: "generate", detail: "正在生成并检查交付文件" },
  done: { node: "done", detail: "任务已完成" },
  error: { node: "error_recover", detail: "任务未完成，请查看错误说明" },
};

const PUBLIC_TOOL_COPY: Record<string, { tool: string; detail: string }> = {
  web_search: { tool: "联网搜索", detail: "正在发现候选来源" },
  browser_search: { tool: "联网搜索", detail: "正在发现候选来源" },
  fetch_url: { tool: "打开来源", detail: "正在打开原始页面核验标题、时间与关键事实" },
  browser_open: { tool: "打开来源", detail: "正在打开原始页面核验内容" },
  browser_read: { tool: "阅读页面", detail: "正在读取原始页面正文" },
  kb_search: { tool: "资料检索", detail: "正在检索本轮明确选择的资料" },
  kg_query: { tool: "关系核对", detail: "正在核对资料之间的关系" },
  create_file: { tool: "生成文件", detail: "正在生成并校验交付文件" },
  create_document: { tool: "生成文档", detail: "正在生成并校验交付文档" },
  create_presentation: { tool: "生成演示文稿", detail: "正在生成并校验演示文稿" },
  execute_code: { tool: "运行验证", detail: "正在运行可复现的检查" },
  read_file_range: { tool: "读取文件", detail: "正在读取与任务直接相关的文件" },
  str_replace: { tool: "编辑文件", detail: "正在更新工作文件" },
};

function normalizeStatus(value?: string): "done" | "running" | "error" {
  if (value === "running") return "running";
  if (value === "error" || value === "denied" || value === "failed") return "error";
  return "done";
}

function publicToolName(raw?: string): string {
  const key = String(raw || "").trim().toLowerCase();
  return PUBLIC_TOOL_COPY[key]?.tool || String(raw || "执行工具").replace(/[_-]+/g, " ");
}

function publicToolDetail(raw?: string): string {
  const key = String(raw || "").trim().toLowerCase();
  return PUBLIC_TOOL_COPY[key]?.detail || "正在执行本任务所需的操作";
}

function oneEvent(event: AgentTimelineEvent, index: number): PublicAgentTimelineEvent | null {
  const node = String(event.node || "").trim().toLowerCase();
  if (!node || HIDDEN_PROTOCOL_NODES.has(node)) return null;

  if (node === "narrate" || node === "think" || node === "reasoning") {
    return {
      kind: "status",
      node: "analysis",
      detail: "正在推进任务并检查下一步",
      status: normalizeStatus(event.status),
      id: event.id || `public-analysis-${index}`,
    };
  }

  if (node === "tool") {
    return {
      kind: "tool",
      node: "tool",
      tool: publicToolName(event.tool),
      detail: publicToolDetail(event.tool),
      status: normalizeStatus(event.status),
      elapsed_ms: event.elapsed_ms,
      id: event.id || `public-tool-${index}`,
      hooks: event.hooks,
    };
  }

  const copy = PUBLIC_NODE_COPY[node];
  if (!copy) return null;
  return {
    kind: event.kind || "trace",
    node: copy.node,
    detail: copy.detail,
    status: normalizeStatus(event.status),
    elapsed_ms: event.elapsed_ms,
    id: event.id || `public-trace-${index}`,
    hooks: event.hooks,
  };
}

/**
 * Convert the append-only internal event stream into a compact public work
 * timeline.  Consecutive repeated operations are folded into one row; the
 * administrator/audit views continue to receive the untouched source events.
 */
export function toPublicAgentTimeline(events: AgentTimelineEvent[] | undefined | null): PublicAgentTimelineEvent[] {
  const result: PublicAgentTimelineEvent[] = [];
  for (const [index, source] of (events || []).entries()) {
    const event = oneEvent(source, index);
    if (!event) continue;
    const previous = result[result.length - 1];
    const sameOperation = previous
      && previous.node === event.node
      && (previous.tool || "") === (event.tool || "")
      && previous.detail === event.detail;
    if (sameOperation) {
      result[result.length - 1] = {
        ...previous,
        ...event,
        id: previous.id,
        elapsed_ms: event.elapsed_ms ?? previous.elapsed_ms,
      };
    } else {
      result.push(event);
    }
  }
  return result;
}
