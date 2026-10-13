/** test_i18n.mjs — 多语言引擎纯函数单测（V103.90）。
 *  用 tsc 现编译 lib/i18n.ts 到临时 JS 再 import，测真源码。
 *  运行：node frontend-next/tests-node/test_i18n.mjs
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");
const tmp = mkdtempSync(path.join(tmpdir(), "i18n-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let I;
try {
  const base = ["lib/i18n.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  else execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  I = await import(url.pathToFileURL(path.join(tmp, "i18n.js")).href);
} catch (e) { console.error("编译失败：", e.message); rmSync(tmp, { recursive: true, force: true }); process.exit(1); }

let pass = 0;
function ok(name, cond) { if (cond) { pass++; console.log("  ✓ " + name); } else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; } }

console.log("=== translate ===");
ok("中文返回原文", I.translate("工作台", "zh") === "工作台");
ok("英文查词典", I.translate("工作台", "en") === "Workbench");
ok("英文缺失回退原文", I.translate("某个还没翻译的词", "en") === "某个还没翻译的词");
ok("英文-知识库", I.translate("知识库", "en") === "Knowledge Base");
ok("英文-新对话", I.translate("新对话", "en") === "New Chat");
ok("中文-任意词原样", I.translate("随便什么", "zh") === "随便什么");

console.log("=== interpolate ===");
ok("替换参数", I.interpolate("你好 {name}", { name: "世界" }) === "你好 世界");
ok("多参数", I.interpolate("{a}-{b}", { a: "1", b: "2" }) === "1-2");
ok("缺参数保留占位", I.interpolate("hi {x}", {}) === "hi {x}");
ok("无参数原样", I.interpolate("plain") === "plain");
ok("translate 带插值", I.translate("hi {x}", "zh", { x: "Y" }) === "hi Y");

console.log("=== isLocale ===");
ok("zh 是", I.isLocale("zh") === true);
ok("en 是", I.isLocale("en") === true);
ok("fr 否", I.isLocale("fr") === false);
ok("空 否", I.isLocale(undefined) === false);

console.log("=== 词典一致性 ===");
ok("LOCALES 含 zh/en", I.LOCALES.length === 2 && I.LOCALES.some(l => l.code === "zh") && I.LOCALES.some(l => l.code === "en"));
ok("词典非空", Object.keys(I.EN_DICT).length > 20);
ok("词典值非空串", Object.values(I.EN_DICT).every(v => typeof v === "string" && v.length > 0));

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
