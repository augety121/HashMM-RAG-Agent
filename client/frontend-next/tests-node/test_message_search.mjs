/** test_message_search.mjs — 会话内搜索纯逻辑单测（V103.90）。
 *  用 tsc 现编译 lib/messageSearch.ts 到临时 JS 再 import，测真源码。
 *  运行：node frontend-next/tests-node/test_message_search.mjs
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import url from "node:url";
import assert from "node:assert";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const FE = path.resolve(__dirname, "..");

const tmp = mkdtempSync(path.join(tmpdir(), "msgsearch-"));
const localTsc = path.join(FE, "node_modules", ".bin", "tsc");
const useLocal = existsSync(localTsc);
let S;
try {
  const base = ["lib/messageSearch.ts", "lib/types.ts", "--outDir", tmp, "--module", "es2020", "--target", "es2020", "--moduleResolution", "node", "--skipLibCheck"];
  if (useLocal) {
    execFileSync(localTsc, base, { cwd: FE, stdio: "inherit" });
  } else {
    // 沙箱/无本地依赖：用全局 tsc，新版需 --ignoreConfig 跳过 tsconfig、--ignoreDeprecations 静音弃用
    execFileSync("tsc", [...base, "--ignoreConfig", "--ignoreDeprecations", "6.0"], { cwd: FE, stdio: "inherit" });
  }
  S = await import(url.pathToFileURL(path.join(tmp, "messageSearch.js")).href);
} catch (e) {
  console.error("编译/导入失败：", e.message);
  rmSync(tmp, { recursive: true, force: true });
  process.exit(1);
}

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

const msgs = [
  { role: "user", content: "请讲讲 BM25 检索", ts: 1 },
  { role: "assistant", content: "BM25 是词法检索算法，BM25 很经典", ts: 2 },
  { role: "user", content: "📎 file.txt\n那语义检索呢", ts: 3 },
  { role: "assistant", content: "语义检索用向量", ts: 4 },
];

console.log("=== findMatches ===");
ok("单次命中", S.findMatches("hello world", "world").length === 1);
ok("多次命中", S.findMatches("aXaXa", "a").length === 3);
ok("大小写不敏感", S.findMatches("Hello HELLO", "hello").length === 2);
ok("不重叠", S.findMatches("aaaa", "aa").length === 2);
ok("无命中→空", S.findMatches("abc", "xyz").length === 0);
ok("空查询→空", S.findMatches("abc", "").length === 0);

console.log("=== searchMessages ===");
const r = S.searchMessages(msgs, "BM25");
ok("命中消息数", r.hits.length === 2);
ok("总匹配数(含重复)", r.totalMatches === 3);
ok("结果含次数", r.hits[1].count === 2);
ok("结果含片段", r.hits[0].snippet.includes("BM25"));
ok("flat 扁平列表", r.flat.length === 3);
const r2 = S.searchMessages(msgs, "语义检索");
ok("跨消息命中", r2.hits.length === 2);
ok("附件占位行被剔除(不搜到 file.txt)", S.searchMessages(msgs, "file.txt").totalMatches === 0);
ok("空查询→空", S.searchMessages(msgs, "").totalMatches === 0);
ok("无命中→空", S.searchMessages(msgs, "zzzz").totalMatches === 0);

console.log("=== snippet ===");
ok("命中居中", S.snippet("x".repeat(60) + "目标" + "y".repeat(60), "目标").includes("目标"));
ok("带省略号", S.snippet("a".repeat(60) + "目标" + "b".repeat(60), "目标").includes("…"));
ok("换行压成空格", !S.snippet("line1\ntarget\nline2", "target").includes("\n"));

console.log("=== stepHit 环绕 ===");
ok("下一个", S.stepHit(0, 1, 3) === 1);
ok("末尾环绕", S.stepHit(2, 1, 3) === 0);
ok("首部回退环绕", S.stepHit(0, -1, 3) === 2);
ok("空→-1", S.stepHit(0, 1, 0) === -1);

rmSync(tmp, { recursive: true, force: true });
console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
