/** test_ipc_contract.js — Electron 主进程 IPC 契约测试（V306）。
 *
 * Electron 的真 ipcMain 需要 electron 运行时，纯 Node 起不了。但 IPC 守卫的【wrapping 逻辑】
 * 已抽成 ipc-guard.installGuard(ipcMain, ctxProvider)，可用一个 mock ipcMain 端到端验证契约：
 *   · installGuard 后，敏感通道的 handler 被 sender 校验包裹；
 *   · 可信 sender（app 目录 file:// / 受信 origin）→ handler 正常执行、拿到返回值；
 *   · 不可信 sender（远程页）→ handler【不执行】、调用方拿到统一错误、denied 计数 +1；
 *   · 非敏感通道 handler 完全不受影响（透传）；
 *   · handle 与 on 两种注册都被治理。
 * 这是对"真起 ipcMain 测通道"在无 electron 环境下最接近的契约级验证。
 */
"use strict";
const assert = require("assert");
const G = require("../modules/ipc-guard");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// ── mock ipcMain：记录 handle/on 注册的 handler，可按 channel 触发 ──
function makeMockIpc() {
  const handlers = {};   // channel -> fn (handle)
  const listeners = {};  // channel -> fn (on)
  return {
    handle(channel, fn) { handlers[channel] = fn; },
    on(channel, fn) { listeners[channel] = fn; },
    _invokeHandle(channel, event, ...args) { return handlers[channel](event, ...args); },
    _invokeOn(channel, event, ...args) { return listeners[channel](event, ...args); },
    _hasHandle(channel) { return channel in handlers; },
  };
}

const APPDIR = "/opt/hashmm/desktop";
const ctx = () => ({ appDir: APPDIR, trustedOrigins: ["http://127.0.0.1:17680"] });
const ev = (url) => ({ senderFrame: { url } });
const trusted = ev("file:///opt/hashmm/desktop/app.html");
const trustedShell = ev("http://127.0.0.1:17680/");
const evil = ev("https://evil.com/x");
const silent = { error() {} };   // 静默日志，避免污染测试输出

// 1. 敏感通道 + 可信 sender → handler 执行，返回值透传
{
  const ipc = makeMockIpc();
  const guard = G.installGuard(ipc, ctx, silent);
  let ran = false;
  ipc.handle("cu:exec", (_e, arg) => { ran = true; return { ok: true, echo: arg }; });
  const res = ipc._invokeHandle("cu:exec", trusted, "payload");
  assert.strictEqual(ran, true, "可信 sender 应执行 handler");
  assert.deepStrictEqual(res, { ok: true, echo: "payload" }, "返回值应透传");
  assert.strictEqual(guard.deniedCount(), 0);
  ok("敏感通道 + 可信 sender(app file://) → 执行且返回值透传");
}

// 2. 敏感通道 + 受信 origin sender → 执行
{
  const ipc = makeMockIpc();
  G.installGuard(ipc, ctx, silent);
  let ran = false;
  ipc.handle("shell:info", () => { ran = true; return { ok: true }; });
  ipc._invokeHandle("shell:info", trustedShell);
  assert.strictEqual(ran, true, "受信 origin sender 应执行");
  ok("敏感通道 + 受信 origin sender → 执行");
}

// 3. 敏感通道 + 不可信 sender → handler 不执行、返回统一错误、denied+1
{
  const ipc = makeMockIpc();
  const guard = G.installGuard(ipc, ctx, silent);
  let ran = false;
  ipc.handle("files:save", () => { ran = true; return { ok: true }; });
  const res = ipc._invokeHandle("files:save", evil, { path: "/etc/x" });
  assert.strictEqual(ran, false, "不可信 sender 绝不能执行敏感 handler");
  assert.strictEqual(res.ok, false, "应返回统一失败结果");
  assert.ok(String(res.error).includes("未通过安全校验"), "应带安全校验失败说明");
  assert.strictEqual(guard.deniedCount(), 1, "denied 计数应 +1");
  ok("敏感通道 + 不可信远程 sender → 拒绝执行 + 统一错误 + 计数");
}

// 4. 非敏感通道 → 完全透传（不受 sender 影响，即便 evil）
{
  const ipc = makeMockIpc();
  const guard = G.installGuard(ipc, ctx, silent);
  let ran = false;
  ipc.handle("theme:get", () => { ran = true; return { theme: "dark" }; });
  const res = ipc._invokeHandle("theme:get", evil);
  assert.strictEqual(ran, true, "非敏感通道应透传执行");
  assert.deepStrictEqual(res, { theme: "dark" });
  assert.strictEqual(guard.deniedCount(), 0, "非敏感通道不应计入 denied");
  ok("非敏感通道 → 完全透传（即便不可信 sender）");
}

// 5. on 注册的敏感通道同样被治理
{
  const ipc = makeMockIpc();
  const guard = G.installGuard(ipc, ctx, silent);
  let ran = 0;
  ipc.on("term:input", () => { ran++; });
  ipc._invokeOn("term:input", trusted);   // 可信 → 执行
  ipc._invokeOn("term:input", evil);       // 不可信 → 不执行
  assert.strictEqual(ran, 1, "on 敏感通道：仅可信 sender 执行");
  assert.strictEqual(guard.deniedCount(), 1);
  ok("on 注册的敏感通道：可信执行、不可信拒绝");
}

// 6. V308 fail-closed：sender 无法判定（无 senderFrame）→ 敏感通道【拒绝】（原为放行）
{
  const ipc = makeMockIpc();
  const guard = G.installGuard(ipc, ctx, silent);
  let ran = false;
  ipc.handle("cu:capture", () => { ran = true; return { ok: true }; });
  const r = ipc._invokeHandle("cu:capture", {});   // 无 senderFrame
  assert.strictEqual(ran, false, "sender 未知的敏感通道必须拒绝执行（fail-closed）");
  assert.strictEqual(guard.deniedCount(), 1);
  assert.ok(r && r.ok === false, "拒绝时应返回 {ok:false} 错误");
  ok("sender 无法判定 → 拒绝敏感通道（fail-closed）");
}

// 7. V339 browser:* 包含站点策略和历史证据，必须归为敏感 IPC
{
  assert.strictEqual(G.isSensitiveChannel("browser:state"), true);
  assert.strictEqual(G.isSensitiveChannel("browser:setPolicy"), true);
  ok("browser:* 站点策略/历史证据通道被敏感分类");
}

// 8. 目录穿越 file:// sender 视为不可信
{
  const ipc = makeMockIpc();
  const guard = G.installGuard(ipc, ctx, silent);
  let ran = false;
  ipc.handle("local:write", () => { ran = true; });
  ipc._invokeHandle("local:write", ev("file:///opt/hashmm/desktop-evil/x.html"));
  assert.strictEqual(ran, false, "越界 file:// sender 应被拒");
  assert.strictEqual(guard.deniedCount(), 1);
  ok("目录穿越/兄弟目录 file:// sender → 拒绝");
}

console.log(`\ntest_ipc_contract: ${pass} 项全部通过`);
