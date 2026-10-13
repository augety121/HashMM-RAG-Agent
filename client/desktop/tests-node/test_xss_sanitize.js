/** test_xss_sanitize.js — 渲染前 HTML 危险内容净化（DESK-P0-03）。
 *  transpile frontend-next/lib/sanitize-html.ts via esbuild(若可用)，否则跳过。 */
"use strict";
const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ESB = ["/tmp/esb/node_modules/.bin/esbuild", "node_modules/.bin/esbuild",
  path.join(__dirname, "../../frontend-next/node_modules/.bin/esbuild")].find(p => fs.existsSync(p));
const SRC = path.join(__dirname, "../../frontend-next/lib/sanitize-html.ts");
if (!ESB || !fs.existsSync(SRC)) {
  console.log("  ⏭ 跳过：esbuild 或 sanitize-html.ts 不可用（CI 装 esbuild 后真跑）");
  console.log("test_xss_sanitize: SKIP");
  process.exit(0);
}
const OUT = path.join(require("os").tmpdir(), "sanitize_test.js");
execSync(`"${ESB}" "${SRC}" --format=cjs --platform=node --outfile="${OUT}" --log-level=error`);
const { stripDangerousHtml } = require(OUT);
const assert = require("assert");
let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

assert.ok(!/script/i.test(stripDangerousHtml("<script>alert(1)</script>hi")), "script 未剥离");
assert.ok(stripDangerousHtml("<script>x</script>hi").includes("hi"), "正文被误删");
ok("<script> 连内容整体删除");

for (const [inp, attr] of [["<div onclick=\"a()\">x</div>", "onclick"],
  ["<td onmouseover='b()'>c</td>", "onmouseover"], ["<img src=x onerror=c()>", "onerror"]]) {
  assert.ok(!new RegExp(attr, "i").test(stripDangerousHtml(inp)), `${attr} 未剥离`);
}
ok("事件处理器属性(onclick/onmouseover/onerror)剥离");

for (const tag of ["iframe", "object", "embed", "svg", "form", "style"]) {
  assert.ok(!new RegExp("<" + tag, "i").test(stripDangerousHtml(`<${tag}>x</${tag}>y`)), `${tag} 未剥离`);
}
ok("危险标签(iframe/object/embed/svg/form/style)剥离");

assert.ok(!/href\s*=\s*["']?javascript:alert/i.test(stripDangerousHtml("<a href=\"javascript:alert(1)\">l</a>")), "javascript: 未中和");
assert.ok(!/vbscript:msgbox/i.test(stripDangerousHtml("<a href='vbscript:msgbox(1)'>h</a>")), "vbscript: 未中和");
ok("危险协议(javascript:/vbscript:)中和");

// 良性内容不破坏
const benign = "正常 **加粗** 与表格 <table><tr><td>格子</td></tr></table> 与引用 [1]";
const out = stripDangerousHtml(benign);
assert.ok(out.includes("**加粗**") && out.includes("格子") && out.includes("[1]"), "良性内容被误伤");
ok("良性 markdown/表格/引用不受影响");

console.log(`\ntest_xss_sanitize: ${pass} 项全部通过`);
