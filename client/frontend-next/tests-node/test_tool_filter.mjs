/** test_tool_filter.mjs — 工具筛选/排序/用量汇总纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "tools-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let T;
try {
  const base = ["lib/toolFilter.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  T = await import(url.pathToFileURL(path.join(tmp, "toolFilter.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const TS = [
  { name: "search_web", description: "搜索网络", category: "builtin", enabled: true, use_count: 50 },
  { name: "run_code", description: "执行代码", category: "builtin", enabled: false, use_count: 10 },
  { name: "weather_api", description: "查天气", category: "custom", enabled: true, use_count: 5 },
];

console.log("=== filterTools ===");
ok("默认全返回", T.filterTools(TS).length === 3);
ok("仅启用", T.filterTools(TS, { state: "enabled" }).length === 2);
ok("仅禁用", T.filterTools(TS, { state: "disabled" }).length === 1);
ok("关键词-名字", T.filterTools(TS, { query: "search" }).length === 1);
ok("关键词-描述", T.filterTools(TS, { query: "天气" }).length === 1);
ok("关键词大小写不敏感", T.filterTools(TS, { query: "SEARCH" }).length === 1);
ok("组合: 启用+code", T.filterTools(TS, { state: "enabled", query: "search" }).length === 1);
ok("空输入安全", T.filterTools(null).length === 0);

console.log("=== sortTools ===");
ok("按名字", T.sortTools(TS, "name").map(t => t.name).join(",") === "run_code,search_web,weather_api");
ok("按用量降序", T.sortTools(TS, "usage")[0].name === "search_web");
ok("按用量末位", T.sortTools(TS, "usage")[2].name === "weather_api");
ok("按类别", T.sortTools(TS, "category")[0].category === "builtin");
ok("不改原数组", (() => { const before = TS.map(t => t.name).join(); T.sortTools(TS, "usage"); return TS.map(t => t.name).join() === before; })());

console.log("=== toolSummary ===");
const s = T.toolSummary(TS);
ok("总数", s.total === 3);
ok("启用数", s.enabled === 2);
ok("禁用数", s.disabled === 1);
ok("累计调用", s.totalUses === 65);
ok("用量最高", s.topTool === "search_web");
ok("空→topTool null", T.toolSummary([]).topTool === null);
ok("全0用量→topTool null", T.toolSummary([{ name: "x", enabled: true, use_count: 0 }]).topTool === null);

console.log("=== toolsInCategory ===");
ok("取类别工具", JSON.stringify(T.toolsInCategory(TS, "builtin")) === JSON.stringify(["search_web", "run_code"]));
ok("空类别→空", T.toolsInCategory(TS, "nope").length === 0);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
