/** test_packaging_integrity.js — V99 打包完整性守卫（纯 node，沙箱可跑）。
 *
 *  钉死 V98 真机事故：main.js `require("./modules/semantic-serve")` 但
 *  electron-builder.yml 的 files 白名单没收录 modules/ → app.asar 缺文件 →
 *  装机一打开就崩（图3 的 "Cannot find module './modules/semantic-serve'"）。
 *
 *  做法（大厂打包冒烟的等价物）：静态扫 main.js / shellserver.js / backendmgr.js
 *  里所有相对 require，逐个确认其目标文件被 files 白名单某条 glob 覆盖。
 *  任何运行时相对依赖漏进白名单，这里立刻红。
 *
 *  运行：node desktop/tests-node/test_packaging_integrity.js
 */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const DESK = path.join(__dirname, "..");
let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// ── 解析 electron-builder.yml 的 files 白名单（轻量 YAML：只取 files: 下的 "- xxx"） ──
function parseFilesAllowlist() {
  const yml = fs.readFileSync(path.join(DESK, "electron-builder.yml"), "utf8").split(/\r?\n/);
  const out = [];
  let inFiles = false;
  for (const raw of yml) {
    if (/^files:\s*$/.test(raw)) { inFiles = true; continue; }
    if (inFiles) {
      if (/^\s*-\s+/.test(raw)) {
        out.push(raw.replace(/^\s*-\s+/, "").replace(/\s+#.*$/, "").replace(/^["']|["']$/g, "").trim());
      } else if (/^\S/.test(raw)) { break; }   // 顶格新键 → files 段结束
    }
  }
  return out;
}

// glob → 该相对路径是否被覆盖（够用即可：精确名、dir/**、**/x）
function covered(rel, globs) {
  rel = rel.replace(/\\/g, "/");
  return globs.some((g) => {
    if (g === rel) return true;
    if (g.endsWith("/**")) { const base = g.slice(0, -3); return rel === base || rel.startsWith(base + "/"); }
    if (g.endsWith("/*")) { const base = g.slice(0, -2); return path.posix.dirname(rel) === base; }
    if (g === "**" || g === "**/*") return true;
    return false;
  });
}

// 扫一个入口文件里的相对 require/import，递归跟进（停在 node_modules / 原生 .node）
function collectRelDeps(entryRel, seen) {
  const abs = path.join(DESK, entryRel);
  if (seen.has(entryRel) || !fs.existsSync(abs)) return;
  seen.add(entryRel);
  const src = fs.readFileSync(abs, "utf8");
  const re = /require\(\s*["'](\.[^"']+)["']\s*\)/g;
  let m;
  while ((m = re.exec(src))) {
    let dep = m[1];
    // 解析成相对 desktop/ 的真实文件路径（补 .js / index.js）
    let depAbs = path.resolve(path.dirname(abs), dep);
    let depFile = null;
    for (const cand of [depAbs, depAbs + ".js", path.join(depAbs, "index.js")]) {
      if (fs.existsSync(cand) && fs.statSync(cand).isFile()) { depFile = cand; break; }
    }
    if (!depFile) continue;   // 解析不到（动态/平台分支）→ 跳过，不误报
    const depRel = path.relative(DESK, depFile).replace(/\\/g, "/");
    if (depRel.includes("node_modules/")) continue;
    seen.add(depRel);
    if (depFile.endsWith(".js")) collectRelDeps(depRel, seen);
  }
}

const globs = parseFilesAllowlist();
assert.ok(globs.length > 0, "未能解析出 files 白名单");
ok(`解析 files 白名单（${globs.length} 条）`);

// main.js 必须在白名单
assert.ok(covered("main.js", globs), "main.js 不在 files 白名单");

// 收集 main 入口可达的全部相对依赖
const deps = new Set();
collectRelDeps("main.js", deps);

const missing = [];
for (const rel of deps) {
  if (rel === "main.js") continue;
  if (!covered(rel, globs)) missing.push(rel);
}
assert.strictEqual(missing.length, 0,
  "以下运行时相对依赖未被 files 白名单覆盖（装机后 app.asar 内 require 会崩）:\n  " + missing.join("\n  "));
ok(`main.js 可达的 ${deps.size - 1} 个相对依赖全部在白名单内`);

// 专项钉死 V98 事故点
assert.ok(deps.has("modules/semantic-serve.js"), "应扫到 modules/semantic-serve.js 依赖");
assert.ok(covered("modules/semantic-serve.js", globs), "modules/semantic-serve.js 必须在白名单（V98 崩溃根因）");
ok("V98 事故点 modules/semantic-serve.js 已在白名单（回归钉死）");

// 主进程必须有"前置"全局异常网：uncaughtException 监听器应出现在首个业务 require 之前
const mainSrc = fs.readFileSync(path.join(DESK, "main.js"), "utf8");
const idxGuard = mainSrc.indexOf('process.on("uncaughtException"');
const idxBizRequire = mainSrc.search(/require\(\s*["']\.\/(modules|shellserver|backendmgr|localrag|semantic|computeruse)/);
assert.ok(idxGuard > -1, "main.js 缺 uncaughtException 全局异常网");
assert.ok(idxBizRequire === -1 || idxGuard < idxBizRequire,
  "uncaughtException 监听器必须前置于业务 require（否则顶层 require 崩溃兜不住，正是 V98 现象）");
ok("全局异常网前置于业务 require（顶层加载崩溃可被中文框兜住）");

console.log(`\ntest_packaging_integrity: ${pass} 项全部通过`);
