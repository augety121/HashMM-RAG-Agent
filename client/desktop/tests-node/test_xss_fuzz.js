/** test_xss_fuzz.js — 渲染前净化的 property-based 模糊测试（V306）。
 *
 * 用 seeded PRNG（可复现）随机生成上万个 XSS 向量打 stripDangerousHtml，断言不变量：
 *   · 净化后【绝无】浏览器可执行的残留（真危险标签作为标签出现 / 有非空值的事件处理器 /
 *     javascript:vbscript: 作为 URL 值）——分隔符覆盖空白与 `/`（浏览器都接受）；
 *   · 良性 markdown/表格/链接不被误伤。
 *
 * 本测试**已抓到并驱动修复**一个真实绕过：`<img/onerror=alert(1)>`（`/` 作属性分隔，
 * 旧净化只认空白分隔而漏掉）。seeded 保证 CI 确定性；改 SEED 可扩大搜索面。
 */
"use strict";
const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ESB = ["/tmp/esb/node_modules/.bin/esbuild", "node_modules/.bin/esbuild",
  path.join(__dirname, "../../frontend-next/node_modules/.bin/esbuild")].find(p => fs.existsSync(p));
const SRC = path.join(__dirname, "../../frontend-next/lib/sanitize-html.ts");
if (!ESB || !fs.existsSync(SRC)) {
  console.log("  ⏭ 跳过：esbuild/sanitize-html.ts 不可用（CI 装 esbuild 后真跑）");
  console.log("test_xss_fuzz: SKIP");
  process.exit(0);
}
const OUT = path.join(require("os").tmpdir(), "sanitize_fuzz.js");
execSync(`"${ESB}" "${SRC}" --format=cjs --platform=node --outfile="${OUT}" --log-level=error`);
const { stripDangerousHtml } = require(OUT);

// ── seeded PRNG（mulberry32）——确定性可复现 ──
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const SEED = 0x9E3779B9;
const rnd = mulberry32(SEED);
const pick = (a) => a[Math.floor(rnd() * a.length)];

// ── 精确 oracle：只判浏览器真会执行的残留 ──
function findRealLeak(out) {
  const s = out.toLowerCase();
  if (/<(script|iframe|object|embed|svg|style|form|math|link|meta|base|noscript|template|frame|frameset|applet)[\s/>]/.test(s)) return "危险标签作为标签出现";
  if (/[\s/]on[a-z0-9_-]+\s*=\s*("[^"]+"|'[^']+'|[^\s>"'][^\s>]*)/.test(s)) return "事件处理器有非空值";
  if (/(href|src|xlink:href|action|formaction)\s*=\s*("|')?\s*(javascript|vbscript):[^\s"'>]/.test(s)) return "危险协议作为 URL 值";
  return null;
}

const tags = ["script", "iframe", "object", "embed", "svg", "style", "form", "math", "img", "div", "a", "td", "span", "body", "input", "link", "base"];
const evts = ["onclick", "onerror", "onload", "onmouseover", "onfocus", "onanimationstart", "ontoggle", "onpointerrawupdate", "onbegin", "onwheel"];
const seps = [" ", "\t", "\n", "\r", "/", "//", " / ", "\f", "/ /"];
const protos = ["javascript:", "vbscript:", "JaVaScRiPt:", " javascript:", "java\tscript:"];

function genVector() {
  const m = Math.floor(rnd() * 8);
  const t = pick(tags), e = pick(evts), sep = pick(seps), pr = pick(protos);
  switch (m) {
    case 0: return `<${t}${sep}${e}=alert(1)>x`;
    case 1: return `<${t}${sep}${e}="alert(1)">`;
    case 2: return `<${t}${sep}${e}='alert(1)'>`;
    case 3: return `<a${sep}href=${pr}alert(1)>l</a>`;
    case 4: return `<img${sep}src=x${sep}${e}=alert(1)>`;
    case 5: return `<${t}${sep}${e}\t=\nalert(1)>`;
    case 6: return `<${t.toUpperCase()}${sep}${e.toUpperCase()}=alert(1)>`;
    default: return `<${t}${sep}${e}="x"${sep}${pick(evts)}='y'>`;   // 链式双处理器
  }
}

const N = 20000;
const leaks = [];
for (let i = 0; i < N; i++) {
  const v = genVector();
  const out = stripDangerousHtml(v);
  const leak = findRealLeak(out);
  if (leak) leaks.push({ v, out, leak });
}

if (leaks.length > 0) {
  console.error(`  ✗ property-fuzz 发现 ${leaks.length} 个真绕过（示例）:`);
  for (const l of leaks.slice(0, 8)) {
    console.error("    IN :", JSON.stringify(l.v));
    console.error("    OUT:", JSON.stringify(l.out), "→", l.leak);
  }
  console.error(`\ntest_xss_fuzz: FAIL（SEED=${SEED} 可复现）`);
  process.exit(1);
}
console.log(`  ✔ ${N} 个随机 XSS 向量（seeded，含 /-分隔/大小写/链式/危险协议）→ 零可执行残留`);

// 良性内容不被误伤
const benign = [
  ["正常 **加粗** 文本", "**加粗**"],
  ["# 标题\n- 列表\n> 引用", "标题"],
  ["[链接](https://example.com)", "example.com"],
  ["<table><tr><td>格子里有 onclick 字样但没标签</td></tr></table>", "格子"],
  ["价格 x = 5，开关 on = off", "开关"],
];
let broke = 0;
for (const [inp, must] of benign) {
  if (!stripDangerousHtml(inp).includes(must)) { broke++; console.error("    良性被破坏:", JSON.stringify(inp)); }
}
if (broke > 0) { console.error(`test_xss_fuzz: FAIL（误伤良性内容 ${broke} 处）`); process.exit(1); }
console.log("  ✔ 良性 markdown/表格/链接未被误伤");

console.log("\ntest_xss_fuzz: 2 项全部通过");
