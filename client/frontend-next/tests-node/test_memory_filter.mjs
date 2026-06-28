/** test_memory_filter.mjs — 记忆中心筛选/排序/统计纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "mem-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let M;
try {
  const base = ["lib/memoryFilter.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  M = await import(url.pathToFileURL(path.join(tmp, "memoryFilter.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const G = {
  "偏好": [
    { id: "1", key: "注释语言", value: "中文注释", confidence: 0.9, last_used: 300 },
    { id: "2", key: "研究领域", value: "跨模态哈希", confidence: 0.6, last_used: 100 },
  ],
  "事实": [
    { id: "3", key: "时区", value: "北京时间", confidence: 0.75, last_used: 200 },
  ],
};

console.log("=== filterGroups ===");
ok("默认全返回(2组)", Object.keys(M.filterGroups(G)).length === 2);
ok("按类别", Object.keys(M.filterGroups(G, { category: "事实" })).length === 1);
ok("关键词-内容", M.countItems(M.filterGroups(G, { query: "哈希" })) === 1);
ok("关键词-标签", M.countItems(M.filterGroups(G, { query: "时区" })) === 1);
ok("关键词大小写不敏感", M.countItems(M.filterGroups(G, { query: "中文" })) === 1);
ok("空组被移除", !("事实" in M.filterGroups(G, { query: "哈希" })));
ok("组合: 偏好+注释", M.countItems(M.filterGroups(G, { category: "偏好", query: "注释" })) === 1);
ok("空输入安全", Object.keys(M.filterGroups(null)).length === 0);

console.log("=== sortGroupItems ===");
ok("按置信度高→低", M.sortGroupItems(G, "confidence")["偏好"][0].id === "1");
ok("置信度末位", M.sortGroupItems(G, "confidence")["偏好"][1].id === "2");
ok("按最近使用", M.sortGroupItems(G, "recent")["偏好"][0].id === "1");
ok("default 保持原序", M.sortGroupItems(G, "default")["偏好"][0].id === "1");
ok("不改原数组", (() => { const before = G["偏好"].map(m => m.id).join(); M.sortGroupItems(G, "confidence"); return G["偏好"].map(m => m.id).join() === before; })());

console.log("=== memoryStats ===");
const s = M.memoryStats(G);
ok("总数", s.total === 3);
ok("平均置信度", s.avgConfidence === Math.round(((0.9 + 0.6 + 0.75) / 3) * 100));
ok("类别数", s.categories === 2);
ok("空→0", M.memoryStats({}).total === 0 && M.memoryStats({}).avgConfidence === 0);

console.log("=== memoryCategories / countItems ===");
ok("类别按条数降序", JSON.stringify(M.memoryCategories(G)) === JSON.stringify(["偏好", "事实"]));
ok("总条数", M.countItems(G) === 3);
ok("空→空/0", M.memoryCategories({}).length === 0 && M.countItems({}) === 0);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
