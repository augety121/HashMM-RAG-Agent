/** test_discovery_filter.mjs — 主动发现筛选/分级统计纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "disc-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let D;
try {
  const base = ["lib/discoveryFilter.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  D = await import(url.pathToFileURL(path.join(tmp, "discoveryFilter.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const F = [
  { priority: "low", title: "低1", action: "导入资料", action_kind: "goto_kb" },
  { priority: "high", title: "高1", action: "构建社区", action_kind: "build_communities" },
  { priority: "medium", title: "中1", action: "建议补充", action_kind: "suggest" },
  { priority: "high", title: "高2", action: "建简报", action_kind: "create_digest" },
  { title: "无优先级", action: "看看" },
];

console.log("=== normPriority ===");
ok("high", D.normPriority("high") === "high");
ok("medium", D.normPriority("medium") === "medium");
ok("缺省→low", D.normPriority(undefined) === "low");
ok("未知→low", D.normPriority("urgent") === "low");

console.log("=== filterFindings ===");
ok("默认全返回", D.filterFindings(F).length === 5);
ok("仅高", D.filterFindings(F, "high").length === 2);
ok("仅中", D.filterFindings(F, "medium").length === 1);
ok("仅低(含无优先级)", D.filterFindings(F, "low").length === 2);
ok("空输入安全", D.filterFindings(null).length === 0);

console.log("=== priorityCounts ===");
const c = D.priorityCounts(F);
ok("总数", c.total === 5);
ok("高", c.high === 2);
ok("中", c.medium === 1);
ok("低(含无优先级)", c.low === 2);
ok("空→全0", D.priorityCounts([]).total === 0);

console.log("=== sortByPriority ===");
const s = D.sortByPriority(F);
ok("高优先置顶", D.normPriority(s[0].priority) === "high" && D.normPriority(s[1].priority) === "high");
ok("同级保持顺序(高1在高2前)", s[0].title === "高1" && s[1].title === "高2");
ok("低在最后", D.normPriority(s[4].priority) === "low");
ok("不改原数组", (() => { const before = F.map(f => f.title).join(); D.sortByPriority(F); return F.map(f => f.title).join() === before; })());

console.log("=== actionable ===");
ok("可执行动作判定", D.isActionable("build_communities") && D.isActionable("create_digest") && D.isActionable("goto_kb"));
ok("非可执行", !D.isActionable("suggest"));
ok("可处理条数", D.actionableCount(F) === 3);
ok("空→0", D.actionableCount([]) === 0);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
