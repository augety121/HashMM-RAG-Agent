/** test_tabs.mjs — 多标签会话纯逻辑单测（V103.90）。
 *  用 tsc 现编译 lib/tabs.ts 到临时 JS 再 import，测真源码。
 *  运行：node frontend-next/tests-node/test_tabs.mjs
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "tabs-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let T;
try {
  const base = ["lib/tabs.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  T = await import(url.pathToFileURL(path.join(tmp, "tabs.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

console.log("=== addTab ===");
ok("加新标签", eq(T.addTab([], "a"), ["a"]));
ok("追加到末尾", eq(T.addTab(["a"], "b"), ["a", "b"]));
ok("已存在不重复", eq(T.addTab(["a", "b"], "a"), ["a", "b"]));
ok("空 id 不动", eq(T.addTab(["a"], ""), ["a"]));
ok("超上限淘汰最旧", eq(T.addTab(["a", "b", "c"], "d", 3), ["b", "c", "d"]));
ok("不改原数组", (() => { const orig = ["a"]; T.addTab(orig, "b"); return eq(orig, ["a"]); })());

console.log("=== closeTab ===");
ok("关非激活→激活不变", eq(T.closeTab(["a", "b", "c"], "a", "b"), { tabs: ["b", "c"], nextActive: "b" }));
ok("关激活→激活右邻", eq(T.closeTab(["a", "b", "c"], "b", "b"), { tabs: ["a", "c"], nextActive: "c" }));
ok("关激活末个→激活左邻", eq(T.closeTab(["a", "b", "c"], "c", "c"), { tabs: ["a", "b"], nextActive: "b" }));
ok("关最后一个→激活null", eq(T.closeTab(["a"], "a", "a"), { tabs: [], nextActive: null }));
ok("关不存在的→不动", eq(T.closeTab(["a", "b"], "x", "a"), { tabs: ["a", "b"], nextActive: "a" }));
ok("关首个激活→激活新首(原右邻)", eq(T.closeTab(["a", "b", "c"], "a", "a"), { tabs: ["b", "c"], nextActive: "b" }));

console.log("=== adjacentTab 环绕 ===");
ok("右切", T.adjacentTab(["a", "b", "c"], "a", 1) === "b");
ok("右末环绕", T.adjacentTab(["a", "b", "c"], "c", 1) === "a");
ok("左首环绕", T.adjacentTab(["a", "b", "c"], "a", -1) === "c");
ok("无激活→首个", T.adjacentTab(["a", "b"], null, 1) === "a");
ok("空标签→null", T.adjacentTab([], "a", 1) === null);

console.log("=== ensureTab ===");
ok("sid 非空→开标签", eq(T.ensureTab(["a"], "b"), ["a", "b"]));
ok("sid 已在→不动", eq(T.ensureTab(["a", "b"], "a"), ["a", "b"]));
ok("sid null→不动", eq(T.ensureTab(["a"], null), ["a"]));

console.log("=== pruneTab（删会话连带摘标签）===");
ok("删激活会话→摘标签并切相邻", eq(T.pruneTab(["a", "b"], "a", "a"), { tabs: ["b"], nextActive: "b" }));
ok("删非激活→激活不变", eq(T.pruneTab(["a", "b"], "b", "a"), { tabs: ["a"], nextActive: "a" }));

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
