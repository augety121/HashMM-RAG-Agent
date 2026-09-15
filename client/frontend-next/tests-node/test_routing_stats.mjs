/** test_routing_stats.mjs — 模型路由分布/筛选/批量纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "route-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let R;
try {
  const base = ["lib/routingStats.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  R = await import(url.pathToFileURL(path.join(tmp, "routingStats.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const TASKS = [
  { task: "rerank", label: "重排", setting: "auto", effective: "local", default: "local" },
  { task: "answer", label: "回答", setting: "auto", effective: "cloud", default: "cloud" },
  { task: "summarize", label: "摘要", setting: "local", effective: "local", default: "cloud" },
];

console.log("=== currentSetting / effectiveBackend ===");
ok("缺编辑取 setting", R.currentSetting(TASKS[2], {}) === "local");
ok("编辑覆盖", R.currentSetting(TASKS[0], { rerank: "cloud" }) === "cloud");
ok("auto→default(local)", R.effectiveBackend(TASKS[0], {}) === "local");
ok("auto→default(cloud)", R.effectiveBackend(TASKS[1], {}) === "cloud");
ok("显式 local", R.effectiveBackend(TASKS[2], {}) === "local");
ok("编辑改云端生效", R.effectiveBackend(TASKS[0], { rerank: "cloud" }) === "cloud");

console.log("=== routingCounts ===");
const c = R.routingCounts(TASKS, {});
ok("总数", c.total === 3);
ok("本地数", c.local === 2);
ok("云端数", c.cloud === 1);
ok("自动数", c.autoCount === 2);
const c2 = R.routingCounts(TASKS, { answer: "local" });
ok("改 answer 为本地后本地数+1", c2.local === 3 && c2.cloud === 0);
ok("空→全0", R.routingCounts([], {}).total === 0);

console.log("=== filterTasks ===");
ok("全部", R.filterTasks(TASKS, {}, "all").length === 3);
ok("仅 auto", R.filterTasks(TASKS, {}, "auto").length === 2);
ok("仅 local", R.filterTasks(TASKS, {}, "local").length === 1);
ok("编辑后过滤", R.filterTasks(TASKS, { rerank: "cloud" }, "cloud").length === 1);
ok("空输入安全", R.filterTasks(null, {}, "all").length === 0);

console.log("=== setAll / hasOverride ===");
const all = R.setAll(TASKS, "local");
ok("批量全本地", all.rerank === "local" && all.answer === "local" && all.summarize === "local");
ok("不改原对象(edits)", (() => { const e = {}; R.setAll(TASKS, "cloud"); return Object.keys(e).length === 0; })());
ok("有显式覆盖", R.hasOverride(TASKS, { rerank: "local" }) === true);
ok("全 auto 无覆盖", R.hasOverride(TASKS, { summarize: "auto" }) === false || R.hasOverride([{ task: "x", label: "x", setting: "auto", effective: "cloud", default: "cloud" }], {}) === false);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
