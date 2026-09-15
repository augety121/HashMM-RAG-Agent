/** test_model_filter.mjs — 模型配置筛选/统计纯逻辑单测（V103.90）。 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "model-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let M;
try {
  const base = ["lib/modelFilter.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  M = await import(url.pathToFileURL(path.join(tmp, "modelFilter.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

const MS = [
  { id: "1", name: "DeepSeek Chat", provider: "deepseek", model_name: "deepseek-chat", base_url: "https://api.deepseek.com/v1", is_default: true },
  { id: "2", name: "Qwen 本地", provider: "qwen", model_name: "qwen2.5", base_url: "http://localhost:8000", is_default: false },
  { id: "3", name: "DeepSeek Coder", provider: "deepseek", model_name: "deepseek-coder", base_url: "https://api.deepseek.com/v1", is_default: false },
];

console.log("=== filterModels ===");
ok("默认全返回", M.filterModels(MS).length === 3);
ok("按服务商", M.filterModels(MS, { provider: "deepseek" }).length === 2);
ok("关键词-配置名", M.filterModels(MS, { query: "qwen" }).length === 1);
ok("关键词-模型名", M.filterModels(MS, { query: "coder" }).length === 1);
ok("关键词-baseurl", M.filterModels(MS, { query: "localhost" }).length === 1);
ok("大小写不敏感", M.filterModels(MS, { query: "DEEPSEEK" }).length === 2);
ok("组合", M.filterModels(MS, { provider: "deepseek", query: "coder" }).length === 1);
ok("空输入安全", M.filterModels(null).length === 0);

console.log("=== distinctProviders ===");
ok("去重排序", JSON.stringify(M.distinctProviders(MS)) === JSON.stringify(["deepseek", "qwen"]));
ok("空→空", M.distinctProviders([]).length === 0);

console.log("=== modelStats ===");
const s = M.modelStats(MS);
ok("总数", s.total === 3);
ok("服务商种类", s.providers === 2);
ok("已设默认", s.hasDefault === true);
ok("无默认", M.modelStats([{ id: "x", provider: "p", is_default: false }]).hasDefault === false);
ok("空→0/false", M.modelStats([]).total === 0 && M.modelStats([]).hasDefault === false);

console.log("=== countByProvider ===");
ok("deepseek 数", M.countByProvider(MS, "deepseek") === 2);
ok("qwen 数", M.countByProvider(MS, "qwen") === 1);
ok("不存在→0", M.countByProvider(MS, "openai") === 0);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
