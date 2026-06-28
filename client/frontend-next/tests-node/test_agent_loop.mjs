/** test_agent_loop.mjs — 桌面 Loop 工程单测（V99）。
 *  用 tsc 现编译 lib/agentLoop.ts 到临时 JS 再 import，测真源码而非镜像。
 *  覆盖：正常完成、超步停机、无进展熔断（连续相同动作）、工具调用上限、
 *  外部中断、去重键规范化、中文停机摘要。
 *  运行：node frontend-next/tests-node/test_agent_loop.mjs
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";
import assert from "node:assert";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const SRC = path.join(FE, "lib", "agentLoop.ts");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// ── 用 tsc 现编译到临时目录 ──
const out = mkdtempSync(path.join(tmpdir(), "hmloop-"));
try {
  execFileSync(path.join(FE, "node_modules", ".bin", "tsc"),
    [SRC, "--outDir", out, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"],
    { stdio: "pipe" });
} catch (e) {
  console.error("tsc 编译失败：", (e.stdout || e.stderr || e).toString());
  process.exit(1);
}
const mod = await import(url.pathToFileURL(path.join(out, "agentLoop.js")).href);
const { AgentLoopController, actionKey, DEFAULT_BUDGET } = mod;

try {
  // 1. 去重键规范化（参数顺序无关）
  assert.strictEqual(actionKey("left_click", { y: 2, x: 1 }), actionKey("left_click", { x: 1, y: 2 }));
  assert.notStrictEqual(actionKey("type", { text: "a" }), actionKey("type", { text: "b" }));
  ok("actionKey 规范化（键排序、值敏感）");

  // 2. 正常完成：模型不再调工具
  {
    const L = new AgentLoopController();
    assert.ok(L.beginRound(false));
    L.recordToolCall("capture_screen", {});
    assert.ok(L.beginRound(false));
    L.markCompleted();
    assert.ok(L.stopped && L.reason === "completed");
    assert.strictEqual(L.summary(), "任务完成");
  }
  ok("正常完成（markCompleted → completed）");

  // 3. 超步停机
  {
    const L = new AgentLoopController({ maxSteps: 3 });
    let rounds = 0;
    while (L.beginRound(false)) { rounds++; L.recordToolCall("x", { n: rounds }); }   // 每轮不同动作，不触发无进展
    assert.strictEqual(rounds, 3);
    assert.strictEqual(L.reason, "max_steps");
    assert.ok(L.summary().includes("上限"));
  }
  ok("超步停机（maxSteps）");

  // 4. 无进展熔断：连续相同动作
  {
    const L = new AgentLoopController({ maxSteps: 20, maxRepeats: 3 });
    L.beginRound(false);
    let r = L.recordToolCall("capture_screen", {});
    assert.ok(!r.noProgress && !r.repeated);
    L.beginRound(false); r = L.recordToolCall("capture_screen", {});
    assert.ok(r.repeated && !r.noProgress);
    L.beginRound(false); r = L.recordToolCall("capture_screen", {});
    assert.ok(r.noProgress, "第 3 次相同动作应熔断");
    assert.strictEqual(L.reason, "no_progress");
    assert.ok(L.summary().includes("自动停止"));
  }
  ok("无进展熔断（连续 maxRepeats 次相同动作）");

  // 5. 不同动作穿插重置重复计数
  {
    const L = new AgentLoopController({ maxRepeats: 2 });
    L.beginRound(false); L.recordToolCall("a", {});
    L.beginRound(false); const r1 = L.recordToolCall("a", {});
    assert.ok(r1.noProgress, "a,a 应在 maxRepeats=2 时熔断");
    const L2 = new AgentLoopController({ maxRepeats: 2 });
    L2.beginRound(false); L2.recordToolCall("a", {});
    L2.beginRound(false); const r2 = L2.recordToolCall("b", {});   // 换动作
    assert.ok(!r2.noProgress, "换动作应重置计数");
  }
  ok("穿插不同动作重置重复计数");

  // 6. 工具调用上限
  {
    const L = new AgentLoopController({ maxSteps: 100, maxToolCalls: 2 });
    L.beginRound(false); L.recordToolCall("a", { i: 1 });
    L.beginRound(false); L.recordToolCall("b", { i: 2 });
    assert.strictEqual(L.beginRound(false), false, "达工具上限应停止下一轮");
    assert.strictEqual(L.reason, "max_steps");
  }
  ok("工具调用总数上限");

  // 7. 外部中断
  {
    const L = new AgentLoopController();
    assert.strictEqual(L.beginRound(true), false);
    assert.strictEqual(L.reason, "stopped");
    assert.strictEqual(L.summary(), "已停止");
  }
  ok("外部中断（用户停止）");

  // 8. 出错停机
  {
    const L = new AgentLoopController();
    L.beginRound(false);
    L.fail("模型调用失败");
    assert.ok(L.stopped && L.reason === "error" && L.summary().includes("出错"));
  }
  ok("出错停机（fail）");

  // 9. 默认预算合理
  assert.ok(DEFAULT_BUDGET.maxSteps >= 8 && DEFAULT_BUDGET.maxRepeats >= 2);
  ok("默认预算合理");

  // 10. effort 估计：简单任务低，复杂任务高
  {
    const { estimateEffort, budgetForQuery } = mod;
    assert.strictEqual(estimateEffort("现在几点"), 1, "简单单步任务 effort=1");
    assert.ok(estimateEffort("打开记事本，输入今天的待办，然后保存到桌面") >= 3, "多步多动词任务 effort 应较高");
    assert.ok(estimateEffort("") === 1, "空查询兜底 1");
    // 预算随 effort 放大
    const simple = budgetForQuery("现在几点");
    const complex = budgetForQuery("打开浏览器，搜索天气，截图，然后把结果保存到文件并打开它");
    assert.ok(complex.maxSteps > simple.maxSteps, "复杂任务步数预算应更大");
    assert.ok(simple.maxSteps >= 8 && complex.maxSteps <= 20, "预算应在 8..20 区间");
    assert.ok(complex.maxToolCalls > complex.maxSteps, "工具上限应大于步数");
  }
  ok("effort 估计 + 预算自适应（简单 8 步 / 复杂至多 20 步）");

  // 11. 子任务并行：确实并发（限流内 1 个时间窗能跑完多个）
  {
    const { runSubagentsParallel } = mod;
    const tasks = [
      { id: "a", query: "查天气" },
      { id: "b", query: "查股价" },
      { id: "c", query: "查新闻" },
    ];
    let active = 0, peak = 0;
    const runner = async (task, ctl) => {
      active++; peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 20));
      active--;
      ctl.recordToolCall("fetch", { q: task.query });
      return task.id.toUpperCase();
    };
    const res = await runSubagentsParallel(tasks, runner, { maxParallel: 3 });
    assert.ok(peak >= 2, `应真正并发（峰值并发 ${peak} 应 ≥2）`);
    assert.deepStrictEqual(res.map((r) => r.result), ["A", "B", "C"], "结果应按输入序返回");
    assert.ok(res.every((r) => r.ok), "全部应成功");
  }
  ok("子任务并行：真并发 + 结果按输入序");

  // 12. 失败隔离：一个子任务抛错不影响其余
  {
    const { runSubagentsParallel } = mod;
    const tasks = [{ id: "ok1", query: "x" }, { id: "bad", query: "y" }, { id: "ok2", query: "z" }];
    const runner = async (task) => {
      if (task.id === "bad") throw new Error("boom");
      return task.id;
    };
    const res = await runSubagentsParallel(tasks, runner, { maxParallel: 2 });
    assert.strictEqual(res[0].ok, true);
    assert.strictEqual(res[1].ok, false, "失败子任务应标记 ok=false");
    assert.ok(res[1].error.includes("boom"), "应捕获错误信息");
    assert.strictEqual(res[1].loop.stop, "error", "失败子任务 Loop 应为 error 终态");
    assert.strictEqual(res[2].ok, true, "失败不应影响后续子任务");
  }
  ok("子任务失败隔离（单个抛错不波及其余）");

  // 13. 预算隔离：各子任务按自身 query 复杂度拿独立预算
  {
    const { runSubagentsParallel } = mod;
    const tasks = [
      { id: "simple", query: "现在几点" },
      { id: "complex", query: "打开浏览器，搜索天气，截图，然后保存到文件并打开它" },
    ];
    const budgets = {};
    const runner = async (task, ctl) => { budgets[task.id] = ctl.budget.maxSteps; return null; };
    await runSubagentsParallel(tasks, runner, { maxParallel: 2 });
    assert.ok(budgets.complex > budgets.simple, "复杂子任务应分到更大步数预算");
  }
  ok("子任务预算隔离（effort 自适应贯穿到子代理层）");

  // 14. decomposeQuery：强分隔才拆，否则单元素
  {
    const { decomposeQuery, summarizeSubagents } = mod;
    assert.strictEqual(decomposeQuery("查一下今天的天气").length, 1, "单一句子不拆");
    const multi = decomposeQuery("打开记事本，然后输入待办；最后保存到桌面");
    assert.ok(multi.length >= 2, "含'然后/；'的复合查询应拆出多段");
    assert.ok(multi.every((t) => t.id.startsWith("sub-")), "子任务 id 规范");
    assert.strictEqual(decomposeQuery("").length, 0, "空查询返回空数组");
    // 摘要
    const s = summarizeSubagents([{ id: "a", ok: true, loop: {} }, { id: "b", ok: false, loop: {} }]);
    assert.ok(s.includes("失败：b"), "摘要应点名失败子任务");
  }
  ok("decomposeQuery 谨慎拆分 + 结果摘要");

  console.log(`\ntest_agent_loop: ${pass} 项全部通过`);
} finally {
  rmSync(out, { recursive: true, force: true });
}
