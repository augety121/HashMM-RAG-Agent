/** test_usage_stats.mjs — 用量可视化纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "usage-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let U;
try {
  const base = ["lib/usageStats.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  U = await import(url.pathToFileURL(path.join(tmp, "usageStats.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

console.log("=== relBars ===");
ok("最大值→100", U.relBars([10, 5, 2])[0] === 100);
ok("按比例", U.relBars([10, 5, 2])[1] === 50);
ok("非零至少 minPct", U.relBars([100, 1])[1] === 3);
ok("零→0", U.relBars([10, 0])[1] === 0);
ok("全0→全0", JSON.stringify(U.relBars([0, 0])) === JSON.stringify([0, 0]));
ok("空→空", U.relBars([]).length === 0);
ok("负值当0", U.relBars([10, -5])[1] === 0);

console.log("=== pctSplit ===");
ok("对半", U.pctSplit(50, 50).aPct === 50 && U.pctSplit(50, 50).bPct === 50);
ok("和为100", (() => { const s = U.pctSplit(33, 67); return s.aPct + s.bPct === 100; })());
ok("总量", U.pctSplit(30, 70).total === 100);
ok("全0→0/0", U.pctSplit(0, 0).aPct === 0 && U.pctSplit(0, 0).bPct === 0);
ok("一边为0", U.pctSplit(100, 0).aPct === 100 && U.pctSplit(100, 0).bPct === 0);

console.log("=== sumTokens ===");
ok("求和", U.sumTokens(10, 20, 30) === 60);
ok("忽略 undefined/null", U.sumTokens(10, undefined, null, 5) === 15);
ok("忽略负值", U.sumTokens(10, -5) === 10);
ok("空→0", U.sumTokens() === 0);

console.log("=== fmtCompact ===");
ok("千", U.fmtCompact(1234) === "1.2k");
ok("百万", U.fmtCompact(1234567) === "1.2M");
ok("整千不带.0", U.fmtCompact(2000) === "2k");
ok("小数字原样", U.fmtCompact(500) === "500");

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
