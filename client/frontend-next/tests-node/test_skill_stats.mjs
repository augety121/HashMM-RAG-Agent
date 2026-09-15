/** test_skill_stats.mjs — 自我进化技能排序/汇总 + 经验筛选纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "skill-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let S;
try {
  const base = ["lib/skillStats.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  S = await import(url.pathToFileURL(path.join(tmp, "skillStats.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const SK = [
  { id: "a", name: "检索流程", quality_score: 0.5, use_count: 20 },
  { id: "b", name: "总结流程", quality_score: 0.9, use_count: 5 },
  { id: "c", name: "代码流程", quality_score: 0.3, use_count: 50 },
];
const EP = [
  { id: "1", outcome: "success" }, { id: "2", outcome: "fail" },
  { id: "3", outcome: "ok" }, { id: "4", outcome: "weird" }, { id: "5" },
];

console.log("=== sortSkills ===");
ok("按质量高→低", S.sortSkills(SK, "quality")[0].id === "b");
ok("质量末位", S.sortSkills(SK, "quality")[2].id === "c");
ok("按用量高→低", S.sortSkills(SK, "usage")[0].id === "c");
ok("用量末位", S.sortSkills(SK, "usage")[2].id === "b");
ok("default 原序", S.sortSkills(SK, "default")[0].id === "a");
ok("不改原数组", (() => { const before = SK.map(s => s.id).join(); S.sortSkills(SK, "quality"); return SK.map(s => s.id).join() === before; })());

console.log("=== filterSkills ===");
ok("空查询全返回", S.filterSkills(SK, "").length === 3);
ok("按名字", S.filterSkills(SK, "检索").length === 1);
ok("大小写不敏感", S.filterSkills([{ id: "x", name: "SearchFlow" }], "search").length === 1);
ok("按触发词", S.filterSkills([{ id: "y", name: "z", trigger_patterns: ["翻译", "总结"] }], "翻译").length === 1);
ok("无匹配", S.filterSkills(SK, "不存在的词").length === 0);
ok("空输入安全", S.filterSkills(null, "x").length === 0);

console.log("=== skillsSummary ===");
const sm = S.skillsSummary(SK);
ok("总数", sm.total === 3);
ok("平均质量", sm.avgQuality === Math.round(((0.5 + 0.9 + 0.3) / 3) * 100));
ok("累计调用", sm.totalUses === 75);
ok("空→0", S.skillsSummary([]).total === 0 && S.skillsSummary([]).avgQuality === 0);

console.log("=== classifyOutcome ===");
ok("success", S.classifyOutcome("success") === "success");
ok("ok→success", S.classifyOutcome("ok") === "success");
ok("fail", S.classifyOutcome("fail") === "fail");
ok("error→fail", S.classifyOutcome("error") === "fail");
ok("未知", S.classifyOutcome("weird") === "unknown");
ok("缺省→未知", S.classifyOutcome(undefined) === "unknown");

console.log("=== filterEpisodes / counts ===");
ok("全部", S.filterEpisodes(EP, "all").length === 5);
ok("仅成功(success+ok)", S.filterEpisodes(EP, "success").length === 2);
ok("仅失败", S.filterEpisodes(EP, "fail").length === 1);
ok("仅未知(weird+缺省)", S.filterEpisodes(EP, "unknown").length === 2);
ok("空输入安全", S.filterEpisodes(null, "all").length === 0);
const oc = S.episodeOutcomeCounts(EP);
ok("分布: 成功", oc.success === 2);
ok("分布: 失败", oc.fail === 1);
ok("分布: 未知", oc.unknown === 2);
ok("分布: 总数", oc.total === 5);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
