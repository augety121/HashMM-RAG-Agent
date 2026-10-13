/** test_logger.js — 结构化日志系统单测（V100）。
 *  纯逻辑：脱敏（递归/循环引用）、JSON 行格式、轮转判定。
 *  真实 IO：写临时目录、触发文件轮转、环形缓冲。
 *  运行：node desktop/logging/test_logger.js
 */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const os = require("os");
const L = require("../logging/logger");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// 1. 脱敏（含嵌套、数组、循环引用、key 大小写/下划线归一）
{
  const r = L.redact({ user: "amy", token: "abc", nested: { Password: "p", ok: 1 }, list: [{ api_key: "k" }] });
  assert.strictEqual(r.token, "[REDACTED]");
  assert.strictEqual(r.nested.Password, "[REDACTED]", "大小写不敏感");
  assert.strictEqual(r.nested.ok, 1, "非敏感字段保留");
  assert.strictEqual(r.list[0].api_key, "[REDACTED]", "数组内嵌也脱敏");
  assert.strictEqual(r.user, "amy");
  const circ = { a: 1 }; circ.self = circ;
  assert.strictEqual(L.redact(circ).self, "[Circular]", "循环引用不爆栈");
}
ok("脱敏：嵌套/数组/大小写不敏感/循环引用/保留非敏感字段");

// 2. JSON 行格式
{
  const line = L.formatLine("info", "mcp", "started", { port: 8080, token: "x" });
  const rec = JSON.parse(line);
  assert.strictEqual(rec.level, "info");
  assert.strictEqual(rec.name, "mcp");
  assert.strictEqual(rec.msg, "started");
  assert.strictEqual(rec.port, 8080);
  assert.strictEqual(rec.token, "[REDACTED]", "字段脱敏后落行");
  assert.ok(rec.ts && /\dT\d/.test(rec.ts), "含 ISO 时间戳");
}
ok("JSON 行：含 ts/level/name/msg + 字段脱敏");

// 3. 轮转判定
assert.strictEqual(L.shouldRotate(900, 200, 1000), true, "超上限应轮转");
assert.strictEqual(L.shouldRotate(700, 200, 1000), false, "未超不轮转");
assert.strictEqual(L.shouldRotate(900, 200, 0), false, "上限 0 表示不轮转");
ok("轮转判定：超上限/未超/禁用");

// 4. level 过滤
{
  const logs = [];
  const origLog = console.log, origErr = console.error;
  console.log = (s) => logs.push(s); console.error = (s) => logs.push(s);
  try {
    const log = new L.Logger({ name: "t", level: L.LEVELS.warn });
    log.info("hidden"); log.warn("shown"); log.debug("hidden2");
    assert.strictEqual(logs.length, 1, "低于 warn 的被过滤");
    assert.ok(logs[0].includes("shown"));
  } finally { console.log = origLog; console.error = origErr; }
}
ok("level 过滤：低于阈值不输出");

// 5. 真实文件轮转 + 子 logger + 环形缓冲
{
  const orig = console.log, origErr = console.error; console.log = () => {}; console.error = () => {};
  try {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hmlog-"));
    const log = new L.Logger({ name: "app", dir, file: "hashmm.log", level: L.LEVELS.debug, maxSizeBytes: 400, maxFiles: 3, ringMax: 10 });
    for (let i = 0; i < 40; i++) log.info("entry-" + i, { i });
    // 应发生轮转，产生 hashmm.log + hashmm.log.1 等，且份数 ≤ maxFiles
    const files = fs.readdirSync(dir).filter((f) => f.startsWith("hashmm.log"));
    assert.ok(files.includes("hashmm.log"), "主日志在");
    assert.ok(files.length >= 2, "应已轮转出多份");
    assert.ok(files.length <= 3, "保留份数不超 maxFiles");
    // 子 logger 携带上下文
    const child = log.child({ module: "mcp" });
    child.info("child-msg");
    const last = JSON.parse(child.recent(1)[0]);
    assert.strictEqual(last.module, "mcp", "子 logger 携带上下文字段");
    // 环形缓冲上限
    assert.ok(log.recent().length <= 10, "环形缓冲不超上限");
    fs.rmSync(dir, { recursive: true, force: true });
  } finally { console.log = orig; console.error = origErr; }
}
ok("文件轮转(份数受限) + 子 logger 上下文 + 环形缓冲上限");

// 6. Error 对象提取
{
  const orig = console.error; const lines = []; console.error = (s) => lines.push(s);
  try {
    const log = new L.Logger({ name: "t", level: L.LEVELS.debug });
    log.error(new Error("boom"));
    const rec = JSON.parse(lines[lines.length - 1]);
    assert.strictEqual(rec.msg, "boom");
    assert.ok(rec.stack && rec.stack.includes("boom"), "Error 的 stack 应入库");
  } finally { console.error = orig; }
}
ok("Error 对象：提取 message + stack");

console.log(`\ntest_logger: ${pass} 项全部通过`);
