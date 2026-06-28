/** tests-node/test_agent-loop.js — 沙箱可跑（node tests-node/test_agent-loop.js）。
 * 用假模型 + 假执行器 + 假确认，验证真实多步循环：只读免确认、写操作确认后执行、
 * 危险 shell 确认被拒则不执行并回填、视觉工具补图、无 tool_calls 即终态、达上限即停。 */
"use strict";
const assert = require("assert");
const path = require("path");
const { AgentLoop } = require(path.join(__dirname, "..", "agent-loop.js"));

let passed = 0;
const ok = (n) => { passed++; console.log("  ok -", n); };

// 造一个 OpenAI 风格的助手消息（带 tool_calls）
function asst(content, calls) {
  return { role: "assistant", content: content || "", tool_calls: (calls || []).map((c, i) => ({
    id: "call_" + i, type: "function",
    function: { name: c.name, arguments: JSON.stringify(c.args || {}) },
  })) };
}

// ---- 用例 1：完整多步（列目录 → 确认写 → 危险 shell 被拒 → 截屏 → 终态）----
(async function () {
  // 脚本化假模型：按调用次数返回不同响应
  const script = [
    asst("先看看目录", [{ name: "list_dir", args: { path: "/proj" } }]),
    asst("写个文件", [{ name: "write_file", args: { path: "/proj/a.txt", content: "hi" } }]),
    asst("清理一下", [{ name: "run_shell", args: { command: "rm -rf /proj/tmp" } }]),  // 危险
    asst("看下屏幕", [{ name: "capture_screen", args: { reason: "确认结果" } }]),
    asst("全部完成 ✔", []),   // 无 tool_calls = 终态
  ];
  let modelCalls = 0;
  const seenToolsByModel = [];

  const execed = [];
  const events = [];
  const confirmAsks = [];

  const loop = new AgentLoop({
    callModel: async (messages, tools) => {
      // 记录模型每次看到的最后一条消息角色（验证结果确实回填了）
      seenToolsByModel.push(messages[messages.length - 1].role);
      return { ok: true, message: script[modelCalls++] };
    },
    execTool: async ({ name, args }) => {
      execed.push(name);
      if (name === "list_dir") return { ok: true, output: "[D] tmp\n    a.txt" };
      if (name === "write_file") return { ok: true, output: "已写入 " + args.path };
      if (name === "capture_screen") return { ok: true, output: "已截屏", vision: true, image: "data:image/png;base64,AAAA" };
      return { ok: true, output: "ok" };
    },
    confirm: async ({ name, reason }) => {
      confirmAsks.push({ name, reason });
      return name === "write_file";   // 批准写文件，拒绝危险 shell
    },
    onEvent: (ev) => events.push(ev),
    maxSteps: 16,
  });

  const r = await loop.run({ goal: "整理 /proj", system: "你是助手" });

  assert.strictEqual(r.ok, true, "loop ok");
  assert.strictEqual(r.finalText, "全部完成 ✔", "终态文本");
  assert.strictEqual(r.steps, 5, "应跑 5 步（4 次工具轮 + 1 次终态）, 实际 " + r.steps);

  // 只读 list_dir 未触发确认；write_file 与 run_shell 各触发一次确认
  assert.deepStrictEqual(confirmAsks.map(c => c.name), ["write_file", "run_shell"], "确认仅这两个");

  // 危险 shell 被拒 → 没有真的执行
  assert.ok(execed.includes("list_dir") && execed.includes("write_file") && execed.includes("capture_screen"));
  assert.ok(!execed.includes("run_shell"), "被拒的危险 shell 不应执行");
  ok("多步循环：只读免确认 / 写确认后执行 / 危险 shell 拒则不执行");

  // 被拒后有一条 tool 消息回填了"用户拒绝"，模型下一轮能看到
  const denied = r.messages.find(m => m.role === "tool" && /用户拒绝/.test(m.content || ""));
  assert.ok(denied, "应有'用户拒绝'的 tool 回填");
  assert.ok(denied.tool_call_id, "tool 回填带 tool_call_id");
  ok("拒绝结果按 tool 消息正确回填（带 tool_call_id）");

  // 截屏的视觉图以 user 视觉消息补充
  const visionMsg = r.messages.find(m => m.role === "user" && Array.isArray(m.content) &&
    m.content.some(p => p.type === "image_url"));
  assert.ok(visionMsg, "视觉工具应补一条 user 图片消息");
  ok("视觉工具结果补图（OpenAI 视觉消息格式）");

  // 事件流齐全
  const types = events.map(e => e.type);
  for (const t of ["start", "assistant", "tool_call", "confirm_needed", "tool_result", "done"])
    assert.ok(types.includes(t), "事件流应含 " + t);
  ok("事件流完整（start/assistant/tool_call/confirm_needed/tool_result/done）");
})();

// ---- 用例 2：达到最大步数即停（模型永远调工具）----
(async function () {
  let n = 0;
  const loop = new AgentLoop({
    callModel: async () => ({ ok: true, message: asst("继续", [{ name: "list_dir", args: { path: "/" } }]) }),
    execTool: async () => { n++; return { ok: true, output: "..." }; },
    confirm: async () => true,
    maxSteps: 4,
  });
  const r = await loop.run({ goal: "停不下来" });
  assert.strictEqual(r.stopped, true, "应因上限停止");
  assert.strictEqual(r.steps, 4, "正好 4 步");
  assert.ok(/最大步数/.test(r.error || ""), "错误信息提示上限");
  ok("达到最大步数即停（防失控）");
})();

// ---- 用例 3：模型调用失败 → 安全返回 ----
(async function () {
  const loop = new AgentLoop({
    callModel: async () => ({ ok: false, error: "401 未授权" }),
    execTool: async () => ({ ok: true }),
  });
  const r = await loop.run({ goal: "x" });
  assert.strictEqual(r.ok, false);
  assert.ok(/401/.test(r.error));
  ok("模型失败安全返回（不崩）");
})();

// ---- 用例 4：execTool 抛异常 → 作为错误回填，循环继续 ----
(async function () {
  let modelCalls = 0;
  const script = [
    asst("读个不存在的文件", [{ name: "read_file", args: { path: "/nope" } }]),
    asst("那算了，完成", []),
  ];
  const loop = new AgentLoop({
    callModel: async () => ({ ok: true, message: script[modelCalls++] }),
    execTool: async () => { throw new Error("ENOENT"); },
    confirm: async () => true,
    maxSteps: 6,
  });
  const r = await loop.run({ goal: "x" });
  assert.strictEqual(r.ok, true, "异常不应中断整轮");
  const errMsg = r.messages.find(m => m.role === "tool" && /ENOENT/.test(m.content || ""));
  assert.ok(errMsg, "执行异常应作为 tool 错误回填");
  ok("execTool 异常被捕获回填，循环继续");
})();

setTimeout(() => console.log(`\n=== agent-loop: ${passed} assertions/groups passed ===`), 60);
