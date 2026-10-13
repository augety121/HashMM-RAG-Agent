/** test_cu_actions.js — Computer Use 动作工程冒烟（纯逻辑，沙箱可跑）。
 *  覆盖：归一化坐标往返、动作 schema 校验（缺参/越界/非法键）、危险组合键
 *  强制确认、策略闸（只读/全确认）、回放审计、三平台命令编译正确性、
 *  执行层降级。这是 V99 Computer Use 从"只能看"到"能动手"的核心守护。 */
"use strict";
const assert = require("assert");
const A = require("../modules/cu-actions");
const D = require("../modules/cu-driver");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };
const SCREEN = { width: 1920, height: 1080 };

// ── 1. 坐标归一化往返 ──
{
  const { x, y } = A.denormalize(500, 500, SCREEN);
  assert.strictEqual(x, 960); assert.strictEqual(y, 540);   // 中心
  const { x: x0 } = A.denormalize(0, 0, SCREEN);
  assert.strictEqual(x0, 0);
  const { x: xm } = A.denormalize(1000, 1000, SCREEN);
  assert.strictEqual(xm, 1919);   // clamp 到 width-1
  const back = A.normalize(960, 540, SCREEN);
  assert.ok(Math.abs(back.nx - 500) <= 1 && Math.abs(back.ny - 500) <= 1);
  ok("归一化坐标往返（中心/边界/clamp）");
}

// ── 2. 动作校验：合法 click ──
{
  const r = A.validateAction({ type: "left_click", x: 250, y: 750 }, SCREEN);
  assert.ok(r.ok, r.error);
  assert.strictEqual(r.plan.px, 480); assert.strictEqual(r.plan.py, 810);
  assert.strictEqual(r.plan.write, true);
  assert.strictEqual(r.plan.needsConfirm, false);
  ok("合法 left_click：坐标映射 + 标记为写操作");
}

// ── 3. 校验失败路径 ──
{
  assert.strictEqual(A.validateAction({ type: "frobnicate" }, SCREEN).ok, false);
  assert.strictEqual(A.validateAction({ type: "left_click" }, SCREEN).ok, false);          // 缺坐标
  assert.strictEqual(A.validateAction({ type: "left_click", x: 1500, y: 10 }, SCREEN).ok, false); // 越界
  assert.strictEqual(A.validateAction({ type: "type", text: "" }, SCREEN).ok, false);      // 空文本
  assert.strictEqual(A.validateAction({ type: "type", text: "x".repeat(5000) }, SCREEN).ok, false); // 超长
  assert.strictEqual(A.validateAction({ type: "key", keys: "ctrl+zzz" }, SCREEN).ok, false); // 非法键
  ok("校验失败路径全挡（未知动作/缺参/越界/空文本/超长/非法键）");
}

// ── 4. 按键规范化 ──
{
  assert.deepStrictEqual(A.normalizeKeys("Ctrl+C"), ["ctrl", "c"]);
  assert.deepStrictEqual(A.normalizeKeys(["Control", "Shift", "S"]), ["ctrl", "shift", "s"]);
  assert.deepStrictEqual(A.normalizeKeys("cmd+q"), ["win", "q"]);   // 别名归并
  assert.strictEqual(A.normalizeKeys("a+b+c+d+e+f"), null);          // 超长组合
  assert.strictEqual(A.normalizeKeys(""), null);
  ok("按键规范化（拆分/别名/白名单/长度）");
}

// ── 5. 危险组合键强制确认 ──
{
  const r = A.validateAction({ type: "key", keys: "win+r" }, SCREEN);
  assert.ok(r.ok && r.plan.needsConfirm, "Win+R 应强制确认");
  const r2 = A.validateAction({ type: "key", keys: "alt+f4" }, SCREEN);
  assert.ok(r2.plan.needsConfirm, "Alt+F4 应强制确认");
  const r3 = A.validateAction({ type: "key", keys: "ctrl+c" }, SCREEN);
  assert.ok(!r3.plan.needsConfirm, "Ctrl+C 不该确认");
  ok("危险组合键强制确认（Win+R / Alt+F4），常规键放行");
}

// ── 6. 策略闸 ──
{
  const plan = A.validateAction({ type: "left_click", x: 10, y: 10 }, SCREEN).plan;
  assert.deepStrictEqual(A.applyPolicy(plan, {}), { allow: true, needsConfirm: false, reason: "" });
  assert.strictEqual(A.applyPolicy(plan, { blockWrites: true }).allow, false);     // 只读模式拦写
  assert.strictEqual(A.applyPolicy(plan, { confirmAllWrites: true }).needsConfirm, true); // 高安全全确认
  const readPlan = A.validateAction({ type: "mouse_move", x: 10, y: 10 }, SCREEN).plan;
  assert.strictEqual(A.applyPolicy(readPlan, { blockWrites: true }).allow, true);  // 只读动作不受 blockWrites 影响
  ok("策略闸（只读模式拦写 / 高安全全确认 / 读动作豁免）");
}

// ── 6b. 坐标禁区 ──
{
  // 点击屏幕最底部（任务栏区，ny>976）→ 强制确认
  const taskbar = A.validateAction({ type: "left_click", x: 500, y: 990 }, SCREEN).plan;
  assert.ok(A.applyPolicy(taskbar, {}).needsConfirm, "点击任务栏区应强制确认");
  // 同坐标但 mouse_move（非点击）→ 不触发禁区
  const moveLow = A.validateAction({ type: "mouse_move", x: 500, y: 990 }, SCREEN).plan;
  assert.ok(!A.applyPolicy(moveLow, {}).needsConfirm, "移动到禁区不算点击，不确认");
  // 屏幕中央点击 → 不确认
  const center = A.validateAction({ type: "left_click", x: 500, y: 500 }, SCREEN).plan;
  assert.ok(!A.applyPolicy(center, {}).needsConfirm, "屏幕中央点击不确认");
  // 自定义禁区 + disableZones 关闭
  const custom = A.validateAction({ type: "left_click", x: 100, y: 100 }, SCREEN).plan;
  assert.ok(A.applyPolicy(custom, { noClickZones: [{ name: "测试", x1: 0, y1: 0, x2: 200, y2: 200 }] }).needsConfirm, "自定义禁区生效");
  assert.ok(!A.applyPolicy(taskbar, { disableZones: true }).needsConfirm, "disableZones 可关闭禁区");
  ok("坐标禁区（任务栏区点击确认 / 移动豁免 / 自定义区 / 可关闭）");
}

// ── 7. 回放审计 ──
{
  const rec = new A.ActionRecorder(3);
  rec.record(A.validateAction({ type: "left_click", x: 1, y: 1 }, SCREEN).plan, { ok: true });
  rec.record(A.validateAction({ type: "type", text: "hello world this is long" }, SCREEN).plan, { ok: false, error: "boom" });
  assert.strictEqual(rec.recent().length, 2);
  assert.ok(rec.toText().includes("FAIL: boom"));
  rec.record(A.validateAction({ type: "key", keys: "enter" }, SCREEN).plan, { ok: true });
  rec.record(A.validateAction({ type: "wait", ms: 100 }, SCREEN).plan, { ok: true });
  assert.strictEqual(rec.recent().length, 3, "应按 cap=3 截断最旧");
  ok("回放审计（记录/截断/导出文本）");
}

// ── 8. describeAction 可读摘要 ──
{
  const p = A.validateAction({ type: "double_click", x: 500, y: 500 }, SCREEN).plan;
  assert.ok(A.describeAction(p).includes("双击"));
  ok("动作摘要中文可读");
}

// ── 9. Windows 命令编译 ──
{
  const click = A.validateAction({ type: "left_click", x: 500, y: 500 }, SCREEN).plan;
  const cw = D.compileCommand(click, "win32");
  assert.strictEqual(cw.shell, "powershell.exe");
  assert.ok(cw.args.join(" ").includes("LClick(960,540)"), "应生成 LClick 调用");
  const keyChord = A.validateAction({ type: "key", keys: "ctrl+s" }, SCREEN).plan;
  const ck = D.compileCommand(keyChord, "win32").args.join(" ");
  assert.ok(ck.includes("keybd_event(17"), "ctrl 的 VK=17 应在");
  assert.ok(ck.includes("keybd_event(83"), "S 的 VK=83 应在");
  const typing = A.validateAction({ type: "type", text: "a+b(c)" }, SCREEN).plan;
  const ct = D.compileCommand(typing, "win32").args.join(" ");
  assert.ok(ct.includes("SendKeys") && ct.includes("{+}") && ct.includes("{(}"), "SendKeys 元字符应转义");
  ok("Windows 命令编译（点击/组合键 VK/文本转义）");
}

// ── 10. mac / linux 命令编译 ──
{
  const click = A.validateAction({ type: "left_click", x: 500, y: 500 }, SCREEN).plan;
  const cm = D.compileCommand(click, "darwin");
  assert.strictEqual(cm.shell, "osascript");
  assert.ok(cm.args.join(" ").includes("click at {960, 540}"));
  const cl = D.compileCommand(click, "linux");
  assert.strictEqual(cl.shell, "xdotool");
  assert.deepStrictEqual(cl.args, ["mousemove", "960", "540", "click", "1"]);
  ok("mac(osascript) / linux(xdotool) 命令编译");
}

// ── 11. 执行层：注入假 runner，验证 plan→执行 + 降级 ──
(async () => {
  const click = A.validateAction({ type: "left_click", x: 500, y: 500 }, SCREEN).plan;
  let seen = null;
  const okRunner = async (shell, args) => { seen = { shell, args }; return { ok: true }; };
  const r = await D.executePlan(click, okRunner, "win32");
  assert.ok(r.ok && seen.shell === "powershell.exe");
  const failRunner = async () => ({ ok: false, error: "权限不足" });
  const r2 = await D.executePlan(click, failRunner, "win32");
  assert.ok(!r2.ok && r2.error.includes("权限"));
  // 不支持的平台 → 降级
  const r3 = await D.executePlan(click, okRunner, "sunos");
  assert.ok(!r3.ok && r3.error.includes("不支持"));
  ok("执行层（plan→runner / 失败透传 / 平台降级）");

  console.log(`\ntest_cu_actions: ${pass} 项全部通过`);
})().catch((e) => { console.error("FAIL:", e.message); process.exit(1); });
