/** test_template_sort.mjs — 模板排序纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "tpl-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let T;
try {
  const base = ["lib/templateSort.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  T = await import(url.pathToFileURL(path.join(tmp, "templateSort.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const TPL = [
  { id: "1", name: "Beta", use_count: 5, created_at: 200 },
  { id: "2", name: "Alpha", use_count: 50, created_at: 100 },
  { id: "3", name: "Gamma", use_count: 20, created_at: 300 },
];

console.log("=== sortTemplates ===");
ok("按用量高→低", T.sortTemplates(TPL, "use")[0].id === "2");
ok("用量末位", T.sortTemplates(TPL, "use")[2].id === "1");
ok("按名字字母序", T.sortTemplates(TPL, "name").map(t => t.name).join(",") === "Alpha,Beta,Gamma");
ok("按时间新→旧", T.sortTemplates(TPL, "recent")[0].id === "3");
ok("时间末位", T.sortTemplates(TPL, "recent")[2].id === "2");
ok("default 原序", T.sortTemplates(TPL, "default")[0].id === "1");
ok("不改原数组", (() => { const before = TPL.map(t => t.id).join(); T.sortTemplates(TPL, "use"); return TPL.map(t => t.id).join() === before; })());
ok("空输入安全", T.sortTemplates(null, "use").length === 0);
ok("缺字段当0", T.sortTemplates([{ id: "x" }, { id: "y", use_count: 3 }], "use")[0].id === "y");

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
