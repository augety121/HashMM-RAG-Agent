/** test_cu-describe.js — Computer Use 动作描述/风险分级单测（纯逻辑）。 */
"use strict";
const assert = require("assert");
const D = require("../cu-describe.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== describeAction 各动作 ===");
const click = D.describeAction({ type: "left_click", px: 320, py: 450, write: true });
ok("左键点击含坐标", click.text.includes("左键点击") && click.text.includes("320, 450"));
ok("点击=写入风险", click.risk === "write" && click.riskLabel === "写入");
ok("截屏=只读", D.describeAction({ type: "screenshot" }).risk === "read");
ok("移动鼠标=只读", D.describeAction({ type: "mouse_move", x: 1, y: 2 }).risk === "read");
ok("输入文本截断", D.describeAction({ type: "type", text: "x".repeat(100) }).text.includes("…"));
ok("按键拼接", D.describeAction({ type: "key", keys: ["ctrl", "c"] }).text.includes("ctrl+c"));
ok("拖拽含起止", D.describeAction({ type: "left_click_drag", px: 1, py: 2, px2: 3, py2: 4 }).text.includes("→"));
const scroll = D.describeAction({ type: "scroll", scroll_direction: "down", scroll_amount: 3, x: 5, y: 6 });
ok("滚动方向中文", scroll.text.includes("下"));
ok("双击", D.describeAction({ type: "double_click", x: 1, y: 1 }).text.includes("双击"));
ok("等待", D.describeAction({ type: "wait", ms: 500 }).text.includes("500"));

console.log("=== 字段名容错 ===");
ok("px/py 与 x/y 都认", D.describeAction({ type: "left_click", x: 9, y: 9 }).text.includes("9, 9"));
ok("scrollDir 驼峰也认", D.describeAction({ type: "scroll", scrollDir: "up", x: 1, y: 1 }).text.includes("上"));

console.log("=== 命令/文件 风险 ===");
const cmd = D.describeAction({ type: "run_command", command: "ls -la" });
ok("命令=写入", cmd.risk === "write" && cmd.text.includes("ls -la"));
const danger = D.describeAction({ type: "run_command", command: "rm -rf /", danger: true });
ok("危险命令=danger", danger.risk === "danger" && danger.riskLabel === "危险");
ok("写文件=写入", D.describeAction({ type: "write_file", path: "/etc/x" }).risk === "write");

console.log("=== 未知/空动作 ===");
ok("未知动作不崩", typeof D.describeAction({ type: "foobar" }).text === "string");
ok("空动作不崩", typeof D.describeAction(null).text === "string");
ok("plan.risk=danger 透传", D.describeAction({ type: "left_click", x: 1, y: 1, risk: "danger" }).risk === "danger");

console.log("=== describeEvent 包裹 ===");
const ev = D.describeEvent({ plan: { type: "left_click", px: 1, py: 2, write: true, needsConfirm: true, reason: "写操作" }, result: { ok: false, error: "用户拒绝" }, ts: 123 });
ok("事件取 plan 描述", ev.text.includes("左键点击"));
ok("事件结果 ok=false", ev.ok === false);
ok("事件 error 透传", ev.error === "用户拒绝");
ok("事件 ts 透传", ev.ts === 123);
ok("事件 confirm 标记", ev.confirm === true);
const flatEv = D.describeEvent({ type: "screenshot", ok: true, ts: 9 });
ok("扁平事件也认", flatEv.text.includes("截屏") && flatEv.ok === true);

console.log("=== summarizeSession 汇总 ===");
const sum = D.summarizeSession([
  { plan: { type: "screenshot" }, result: { ok: true } },
  { plan: { type: "left_click", write: true }, result: { ok: true } },
  { plan: { type: "type", write: true }, result: { ok: false, error: "x" } },
  { plan: { type: "run_command", command: "rm -rf /", risk: "danger" }, result: { ok: true } },
]);
ok("总数", sum.total === 4);
ok("只读计数", sum.read === 1);
ok("写入计数", sum.write === 2);
ok("危险计数", sum.danger === 1);
ok("成功/失败计数", sum.ok === 3 && sum.failed === 1);
ok("空会话→全0", D.summarizeSession([]).total === 0);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
