/** test_cu_guard.js — 桌面 Harness 工具守卫链冒烟（纯逻辑，沙箱可跑）。
 *  覆盖：安全命令放行、危险 shell 确认、文件写确认、GUI 危险动作确认、
 *  只读模式拒绝写工具（含 computer 只读动作豁免）、守卫顺序（deny 先于 confirm）、
 *  守卫异常 fail-closed（写类拒绝 / 只读放行，V306 修 DESK-P0-01）。
 *  注入真实 cu-actions 的校验/策略函数，端到端验证链路。 */
"use strict";
const assert = require("assert");
const { createGuardChain } = require("../modules/cu-guard");
const A = require("../modules/cu-actions");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };
const SCREEN = { width: 1920, height: 1080 };

// 真实依赖注入（与 main.js 一致）
const assessCommand = (cmd) => {
  const s = String(cmd || "");
  if (/\brm\s+-[a-z]*r/i.test(s)) return { danger: true, reason: "rm -rf" };
  if (/\bshutdown\b/i.test(s)) return { danger: true, reason: "关机命令" };
  return { danger: false, reason: "" };
};
const chain = createGuardChain({
  assessCommand,
  validateAction: A.validateAction,
  applyPolicy: A.applyPolicy,
  describeAction: A.describeAction,
});
const plan = (action) => A.validateAction(action, SCREEN).plan;

// 1. 安全命令放行
{
  const v = chain.decide({ name: "run_shell", args: { command: "ls -la" } });
  assert.strictEqual(v.decision, "allow");
  ok("安全 shell 命令 → allow");
}

// 2. 危险 shell 确认
{
  const v = chain.decide({ name: "run_shell", args: { command: "rm -rf /tmp/x" } });
  assert.strictEqual(v.decision, "confirm");
  assert.ok(v.detail.includes("rm -rf"));
  ok("危险 shell 命令 → confirm（带原因）");
}

// 3. 文件写确认
{
  const v = chain.decide({ name: "write_file", args: { path: "C:/x.txt", content: "hello" } });
  assert.strictEqual(v.decision, "confirm");
  assert.ok(v.detail.includes("C:/x.txt") && v.detail.includes("5 字"));
  ok("文件写入 → confirm（带路径+字数）");
}

// 4. 只读工具放行（read_file / list_dir / capture_screen / get_system_info）
{
  for (const n of ["read_file", "list_dir", "capture_screen", "get_system_info"]) {
    assert.strictEqual(chain.decide({ name: n, args: {} }).decision, "allow", `${n} 应放行`);
  }
  ok("只读工具 → allow");
}

// 5. GUI 普通点击放行
{
  const v = chain.decide({ name: "computer", args: { action: "left_click", x: 500, y: 500 }, plan: plan({ type: "left_click", x: 500, y: 500 }) });
  assert.strictEqual(v.decision, "allow");
  ok("GUI 普通点击 → allow");
}

// 6. GUI 危险组合键确认
{
  const p = plan({ type: "key", keys: "win+r" });
  const v = chain.decide({ name: "computer", args: {}, plan: p });
  assert.strictEqual(v.decision, "confirm");
  assert.ok(v.detail.includes("win+r"));
  ok("GUI 危险组合键 Win+R → confirm");
}

// 7. GUI 坐标禁区确认
{
  const p = plan({ type: "left_click", x: 500, y: 990 });   // 任务栏带
  const v = chain.decide({ name: "computer", args: {}, plan: p });
  assert.strictEqual(v.decision, "confirm");
  assert.ok(v.reason.includes("敏感区"));
  ok("GUI 坐标禁区点击 → confirm");
}

// 8. 只读模式：写工具全拒绝
{
  const pol = { cuReadOnly: true };
  assert.strictEqual(chain.decide({ name: "run_shell", args: { command: "ls" }, policy: pol }).decision, "deny");
  assert.strictEqual(chain.decide({ name: "write_file", args: { path: "x" }, policy: pol }).decision, "deny");
  const clickP = plan({ type: "left_click", x: 10, y: 10 });
  assert.strictEqual(chain.decide({ name: "computer", args: {}, plan: clickP, policy: pol }).decision, "deny");
  ok("只读模式 → 写工具全 deny（shell/file/GUI 点击）");
}

// 9. 只读模式：computer 只读动作（move/screenshot）豁免
{
  const pol = { cuReadOnly: true };
  const moveP = plan({ type: "mouse_move", x: 10, y: 10 });
  assert.strictEqual(chain.decide({ name: "computer", args: {}, plan: moveP, policy: pol }).decision, "allow", "只读模式下移动光标应放行");
  assert.strictEqual(chain.decide({ name: "read_file", args: {}, policy: pol }).decision, "allow", "只读模式下读文件放行");
  ok("只读模式 → computer 只读动作 + 读工具豁免");
}

// 10. 高安全模式：所有写动作确认
{
  const pol = { cuConfirmAllWrites: true };
  const clickP = plan({ type: "left_click", x: 500, y: 500 });
  const v = chain.decide({ name: "computer", args: {}, plan: clickP, policy: pol });
  assert.strictEqual(v.decision, "confirm");
  ok("高安全模式 → 普通点击也 confirm");
}

// 11. 守卫顺序：deny 优先于 confirm（只读 + 危险命令 → deny 而非 confirm）
{
  const v = chain.decide({ name: "run_shell", args: { command: "rm -rf /" }, policy: { cuReadOnly: true } });
  assert.strictEqual(v.decision, "deny", "只读模式下危险命令应 deny（readOnlyGuard 先于 shellDangerGuard）");
  ok("守卫顺序：deny 优先于 confirm");
}

// 12. 守卫异常 fail-closed：写类工具拒绝，只读工具不受影响（V306，修 DESK-P0-01）
{
  const badChain = createGuardChain({
    assessCommand: () => { throw new Error("boom"); },
    validateAction: A.validateAction, applyPolicy: A.applyPolicy, describeAction: A.describeAction,
  });
  // 写类：shell 在守卫异常时必须拒绝（策略层故障 ≠ 放行）
  const vShell = badChain.decide({ name: "run_shell", args: { command: "anything" } });
  assert.strictEqual(vShell.decision, "deny", "守卫异常时 run_shell 必须 fail-closed 拒绝");
  assert.ok(vShell.failClosed === true, "拒绝裁决应带 failClosed 标记（可观测）");
  assert.ok(String(vShell.reason).includes("fail-closed"), "拒绝原因应说明是守卫异常兜底");
  // 写类：GUI 策略闸异常 → computer 写动作同样拒绝
  const badGui = createGuardChain({
    assessCommand, validateAction: A.validateAction,
    applyPolicy: () => { throw new Error("policy down"); }, describeAction: A.describeAction,
  });
  const clickP = plan({ type: "left_click", x: 500, y: 500 });
  assert.strictEqual(badGui.decide({ name: "computer", args: {}, plan: clickP }).decision, "deny",
    "GUI 策略闸异常时写动作必须拒绝");
  // 只读：守卫异常不拦读操作（read_file 放行、computer 只读动作放行）
  assert.strictEqual(badChain.decide({ name: "read_file", args: {} }).decision, "allow",
    "守卫异常不应拦只读工具");
  const moveP = plan({ type: "mouse_move", x: 10, y: 10 });
  assert.strictEqual(badGui.decide({ name: "computer", args: {}, plan: moveP }).decision, "allow",
    "守卫异常不应拦 computer 只读动作（mouse_move）");
  ok("守卫异常 → 写类 fail-closed 拒绝 · 只读不受影响（策略故障不再放行）");
}

console.log(`\ntest_cu_guard: ${pass} 项全部通过`);
