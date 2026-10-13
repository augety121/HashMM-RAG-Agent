/** test_audit_filter.mjs — 审计筛选/统计/CSV 纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "audit-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let A;
try {
  const base = ["lib/auditFilter.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  A = await import(url.pathToFileURL(path.join(tmp, "auditFilter.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const E = [
  { ts: 1700000000, actor: "alice", tenant: "t1", tool: "search_web", risk: "normal", ok: true, latency_ms: 100, args: { q: "x" } },
  { ts: 1700000100, actor: "bob", tenant: "t1", tool: "delete_file", risk: "high", ok: false, latency_ms: 300, args: { path: "/a,b" } },
  { ts: 1700000200, actor: "alice", tenant: "t2", tool: "run_code", risk: "high", ok: true, latency_ms: 200 },
];

console.log("=== filterAuditEntries ===");
ok("默认全返回", A.filterAuditEntries(E).length === 3);
ok("仅高危", A.filterAuditEntries(E, { risk: "high" }).length === 2);
ok("仅普通", A.filterAuditEntries(E, { risk: "normal" }).length === 1);
ok("仅失败", A.filterAuditEntries(E, { status: "failed" }).length === 1);
ok("仅成功", A.filterAuditEntries(E, { status: "ok" }).length === 2);
ok("关键词-工具名", A.filterAuditEntries(E, { query: "delete" }).length === 1);
ok("关键词-执行者", A.filterAuditEntries(E, { query: "alice" }).length === 2);
ok("关键词大小写不敏感", A.filterAuditEntries(E, { query: "ALICE" }).length === 2);
ok("组合: 高危+失败", A.filterAuditEntries(E, { risk: "high", status: "failed" }).length === 1);
ok("空输入安全", A.filterAuditEntries(null).length === 0);

console.log("=== auditStats ===");
const s = A.auditStats(E);
ok("总数", s.total === 3);
ok("成功数", s.ok === 2);
ok("失败数", s.failed === 1);
ok("高危数", s.highRisk === 2);
ok("成功率", s.successRate === 67);
ok("平均耗时", s.avgLatency === 200);
ok("空→0", A.auditStats([]).total === 0 && A.auditStats([]).successRate === 0);

console.log("=== auditToCsv ===");
const csv = A.auditToCsv(E);
const lines = csv.split("\n");
ok("含表头", lines[0] === "time,actor,tenant,tool,risk,ok,latency_ms,args");
ok("行数=头+数据", lines.length === 4);
ok("含工具名", csv.includes("delete_file"));
ok("含 ok true/false", csv.includes("true") && csv.includes("false"));
ok("逗号字段被引号包裹", csv.includes('"') && /\{""path""/.test(csv));
ok("ISO 时间", /\d{4}-\d{2}-\d{2}T/.test(csv));
ok("空→仅表头", A.auditToCsv([]).split("\n").length === 1);

console.log("=== distinctTools ===");
ok("去重排序", JSON.stringify(A.distinctTools(E)) === JSON.stringify(["delete_file", "run_code", "search_web"]));
ok("空→空", A.distinctTools([]).length === 0);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
