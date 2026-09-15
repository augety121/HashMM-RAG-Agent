/** test_run_stats.mjs — 运行轨迹筛选/汇总纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "runs-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let R;
try {
  const base = ["lib/runStats.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  R = await import(url.pathToFileURL(path.join(tmp, "runStats.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const RUNS = [
  { query: "讲讲 BM25", status: "done", elapsed_s: 2, iterations: 3, stop_reason: "done", usage: { total_tokens: 100 } },
  { query: "写个爬虫", status: "error", elapsed_s: 10, iterations: 8, stop_reason: "budget_exceeded", usage: { prompt_tokens: 50, completion_tokens: 30 } },
  { query: "分析数据", status: "done", elapsed_s: 4, iterations: 5, stop_reason: "max_iterations", usage: { total_tokens: 200 } },
];

console.log("=== runTokens / runFailed ===");
ok("total 优先", R.runTokens(RUNS[0]) === 100);
ok("prompt+completion", R.runTokens(RUNS[1]) === 80);
ok("无 usage→0", R.runTokens({}) === 0);
ok("done 不算失败", R.runFailed(RUNS[0]) === false);
ok("error 算失败", R.runFailed(RUNS[1]) === true);
ok("无 status 不算失败", R.runFailed({}) === false);

console.log("=== filterRuns ===");
ok("默认全返回", R.filterRuns(RUNS).length === 3);
ok("仅成功", R.filterRuns(RUNS, { status: "ok" }).length === 2);
ok("仅失败", R.filterRuns(RUNS, { status: "failed" }).length === 1);
ok("按停止理由", R.filterRuns(RUNS, { stopReason: "budget_exceeded" }).length === 1);
ok("关键词", R.filterRuns(RUNS, { query: "爬虫" }).length === 1);
ok("组合: 成功+done", R.filterRuns(RUNS, { status: "ok", stopReason: "done" }).length === 1);
ok("空输入安全", R.filterRuns(null).length === 0);

console.log("=== runSummary ===");
const s = R.runSummary(RUNS);
ok("总数", s.total === 3);
ok("成功数", s.ok === 2);
ok("失败数", s.failed === 1);
ok("成功率", s.successRate === 67);
ok("平均轮数", s.avgIterations === 5.3);
ok("总 token", s.totalTokens === 380);
ok("平均耗时", s.avgElapsed === 5.3);
ok("空→全0", R.runSummary([]).total === 0 && R.runSummary([]).successRate === 0);

console.log("=== distinctStopReasons ===");
ok("去重排序", JSON.stringify(R.distinctStopReasons(RUNS)) === JSON.stringify(["budget_exceeded", "done", "max_iterations"]));
ok("空→空", R.distinctStopReasons([]).length === 0);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
