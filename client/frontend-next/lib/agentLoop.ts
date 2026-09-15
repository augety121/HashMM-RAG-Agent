/** lib/agentLoop.ts — 桌面 Agent Loop 工程 · 纯逻辑核心（V99）。
 *
 *  把 cu.ts 里散落在 for 循环中的"什么时候停、是不是在原地打转"提炼成
 *  一个可单测的状态机，对标 Claude Code / OpenHands 的 agent loop：
 *
 *    计划 → 执行 → 观察 → 反思 → 停机
 *
 *  关键能力（大厂标准，过去 cu.ts 缺失）：
 *  - 结构化停机原因：completed / max_steps / no_progress / stopped / error
 *  - 无进展熔断：同一 (工具名+参数) 连续重复 N 次 → 判定打转，主动停机
 *    （过去只有轮数上限，模型卡在"截屏→看→再截屏"能空跑满 8 轮）
 *  - 工具结果去重提示：连续相同结果时给模型显式信号，促其换策略
 *  - 预算追踪：步数 / 各类工具调用次数
 *
 *  纯 TS、无 React、无 fetch —— test 直接喂事件序列断言停机决策。
 */

export type StopReason = "running" | "completed" | "max_steps" | "no_progress" | "stopped" | "error";

export interface LoopBudget {
  maxSteps: number;        // 最大模型回合
  maxRepeats: number;      // 同一动作连续重复多少次判无进展
  maxToolCalls: number;    // 工具调用总数上限
}

export const DEFAULT_BUDGET: LoopBudget = { maxSteps: 12, maxRepeats: 3, maxToolCalls: 30 };

/**
 * 任务复杂度（effort）估计 —— 对齐后端 subagents 的 effort-scaling 思想
 * （Anthropic 多代理规则：简单任务 1，多步/比较 2-4，复杂更多）。
 * 桌面 Loop 用它做预算自适应：简单任务给小步数（快、省 token），复杂任务给大步数。
 * 纯启发式：连接词/序号/多动词/长度 → 复杂度信号。返回 1..4。
 */
export function estimateEffort(query: string): number {
  const q = String(query || "").trim();
  if (!q) return 1;
  let score = 1;
  // 显式分步/列举信号
  const seps = (q.match(/[、，;；]|\band\b|\bthen\b|然后|接着|再|以及|并且/gi) || []).length;
  if (seps >= 1) score += Math.min(2, seps);
  // 多个动作动词（打开/点击/输入/保存/查找/创建…）
  const verbs = (q.match(/打开|关闭|点击|输入|保存|查找|搜索|创建|新建|删除|修改|复制|粘贴|下载|安装|运行|截图|填写/g) || []).length;
  if (verbs >= 2) score += 1;
  // 长查询通常更复杂
  if (q.length > 60) score += 1;
  return Math.max(1, Math.min(4, score));
}

/** 按 effort 缩放预算：effort 1→8 步，2→12，3→16，4→20（封顶 maxToolCalls 同步放大）。 */
export function budgetForQuery(query: string): LoopBudget {
  const effort = estimateEffort(query);
  const maxSteps = 4 + effort * 4;          // 8 / 12 / 16 / 20
  return { maxSteps, maxRepeats: 3, maxToolCalls: maxSteps * 2 + 6 };
}

export interface LoopState {
  step: number;
  toolCalls: number;
  lastActionKey: string | null;
  repeatCount: number;
  stop: StopReason;
  stopDetail: string;
}

export function initLoopState(): LoopState {
  return { step: 0, toolCalls: 0, lastActionKey: null, repeatCount: 0, stop: "running", stopDetail: "" };
}

/** 规范化一次工具调用为去重键（名字 + 排序后参数）。 */
export function actionKey(name: string, args: unknown): string {
  let a = "";
  try {
    if (args && typeof args === "object") {
      const o = args as Record<string, unknown>;
      a = Object.keys(o).sort().map((k) => `${k}=${JSON.stringify(o[k])}`).join("&");
    } else a = JSON.stringify(args);
  } catch { a = String(args); }
  return `${name}(${a})`;
}

/** Loop 驱动器：封装预算与状态，UI 只管喂事件、读决策。 */
export class AgentLoopController {
  budget: LoopBudget;
  state: LoopState;

  constructor(budget: Partial<LoopBudget> = {}) {
    this.budget = { ...DEFAULT_BUDGET, ...budget };
    this.state = initLoopState();
  }

  /** 回合开始：递增步数，判断是否超步 / 被外部中断。返回 true=继续，false=停。 */
  beginRound(external: boolean): boolean {
    if (external) { this._stop("stopped", "用户已停止"); return false; }
    if (this.state.step >= this.budget.maxSteps) { this._stop("max_steps", `达到最大步数 ${this.budget.maxSteps}`); return false; }
    if (this.state.toolCalls >= this.budget.maxToolCalls) { this._stop("max_steps", `达到工具调用上限 ${this.budget.maxToolCalls}`); return false; }
    this.state.step += 1;
    return true;
  }

  /** 模型本回合未发起任何工具调用 → 视为给出最终答复，正常完成。 */
  markCompleted(): void { this._stop("completed", "模型给出最终答复"); }

  /**
   * 记录一次工具调用，更新无进展计数。返回本次是否触发"无进展"熔断。
   * 触发后调用方应停止并告知模型在打转。
   */
  recordToolCall(name: string, args: unknown): { repeated: boolean; noProgress: boolean; key: string } {
    this.state.toolCalls += 1;
    const key = actionKey(name, args);
    let repeated = false;
    if (key === this.state.lastActionKey) { this.state.repeatCount += 1; repeated = true; }
    else { this.state.lastActionKey = key; this.state.repeatCount = 1; }
    const noProgress = this.state.repeatCount >= this.budget.maxRepeats;
    if (noProgress) this._stop("no_progress", `连续 ${this.state.repeatCount} 次相同操作（${name}），疑似打转`);
    return { repeated, noProgress, key };
  }

  fail(detail: string): void { this._stop("error", detail); }

  get stopped(): boolean { return this.state.stop !== "running"; }
  get reason(): StopReason { return this.state.stop; }

  /** 给用户看的停机说明（中文）。 */
  summary(): string {
    switch (this.state.stop) {
      case "completed": return "任务完成";
      case "max_steps": return `已达上限：${this.state.stopDetail}`;
      case "no_progress": return `已自动停止：${this.state.stopDetail}`;
      case "stopped": return "已停止";
      case "error": return `出错停止：${this.state.stopDetail}`;
      default: return "进行中";
    }
  }

  private _stop(r: StopReason, detail: string) { this.state.stop = r; this.state.stopDetail = detail; }
}

/* ════════════════ 子代理并行（V100 · Loop 工程能力） ════════════════
 *
 * 对标 Anthropic 多代理研究系统：把一个大任务拆成若干工具型子任务，
 * 各自带独立预算并发跑、失败彼此隔离、结果按输入序归并。
 *
 * 工程判断（诚实边界，刻意不做的事）：不把这个自动接到 cu.ts 的
 * GUI 自动化上。GUI 全程只有一个共享光标/焦点，"并行点击两个地方"
 * 在物理上就是错的。所以这是面向**工具型/检索型**子任务的 Loop 能力
 * （每个子任务跑自己的工具链、互不抢占外设），GUI 任务仍走单线 cu.ts。
 */

export interface SubagentTask {
  id: string;
  query: string;
}

export interface SubagentResult<R> {
  id: string;
  ok: boolean;
  result?: R;
  error?: string;
  loop: LoopState;   // 该子任务自己的 Loop 终态（步数/停机原因可观测）
}

/**
 * 并发跑一批子任务，限流 maxParallel，单任务失败隔离，结果按输入顺序返回。
 * 每个子任务用 budgetForQuery(task.query) 拿到独立预算 → 独立 controller，
 * 简单子任务省步数、复杂子任务给足步数（effort 自适应贯穿到子代理层）。
 *
 * @param runner 子任务执行体：拿到 task 与它专属的 controller，返回结果 R。
 *               runner 内部应当用 controller 驱动自己的工具循环（与单任务一致）。
 */
export async function runSubagentsParallel<R>(
  tasks: SubagentTask[],
  runner: (task: SubagentTask, ctl: AgentLoopController) => Promise<R>,
  opts: { maxParallel?: number } = {},
): Promise<SubagentResult<R>[]> {
  const maxParallel = Math.max(1, opts.maxParallel ?? 3);
  const results: SubagentResult<R>[] = new Array(tasks.length);
  let next = 0;

  async function worker(): Promise<void> {
    // 抢占式取下一个未处理任务的下标 —— 简单可靠的并发池
    while (true) {
      const i = next++;
      if (i >= tasks.length) return;
      const task = tasks[i];
      const ctl = new AgentLoopController(budgetForQuery(task.query));
      try {
        const result = await runner(task, ctl);
        if (!ctl.stopped) ctl.markCompleted();
        results[i] = { id: task.id, ok: true, result, loop: ctl.state };
      } catch (e) {
        // 失败隔离：单个子任务抛错不影响其余，记录到它自己的结果槽
        const msg = e instanceof Error ? e.message : String(e);
        ctl.fail(msg);
        results[i] = { id: task.id, ok: false, error: msg, loop: ctl.state };
      }
    }
  }

  const pool = Array.from({ length: Math.min(maxParallel, tasks.length) }, () => worker());
  await Promise.all(pool);
  return results;
}

/** 聚合子任务结果的一句话中文摘要（成功/失败计数 + 失败 id）。 */
export function summarizeSubagents<R>(results: SubagentResult<R>[]): string {
  const okN = results.filter((r) => r.ok).length;
  const bad = results.filter((r) => !r.ok).map((r) => r.id);
  if (bad.length === 0) return `子任务全部完成（${okN}/${results.length}）`;
  return `子任务 ${okN}/${results.length} 完成，失败：${bad.join("、")}`;
}

/**
 * 把一条复合查询启发式拆成多个子任务（仅在出现强分隔信号时才拆）。
 * 谨慎拆分：只认显式的"然后/接着/；/换行/、并"等强分隔，避免把
 * 单一句子误拆。拆不出 ≥2 段就返回原句单元素数组（调用方据此决定
 * 是否走并行）。返回的子任务 id 形如 sub-1/sub-2…
 */
export function decomposeQuery(query: string): SubagentTask[] {
  const q = String(query || "").trim();
  if (!q) return [];
  // 强分隔符：换行、分号、"然后/接着/再然后"、"，并/，同时"
  const parts = q
    .split(/\s*(?:\n+|[；;]|然后|接着|再然后|，并|，同时|,\s*then\b|\bthen\b)\s*/i)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  if (parts.length < 2) return [{ id: "sub-1", query: q }];
  return parts.map((p, i) => ({ id: `sub-${i + 1}`, query: p }));
}
