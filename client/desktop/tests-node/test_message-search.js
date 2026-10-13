/** test_message-search.js — 会话内消息搜索纯逻辑单测。 */
"use strict";
const assert = require("assert");
const S = require("../message-search.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== findMatches ===");
ok("单次命中", S.findMatches("hello world", "world").length === 1);
ok("多次命中", S.findMatches("aXaXa", "a").length === 3);
ok("大小写不敏感", S.findMatches("Hello HELLO", "hello").length === 2);
ok("不重叠", S.findMatches("aaaa", "aa").length === 2);
ok("无命中→空", S.findMatches("abc", "xyz").length === 0);
ok("空查询→空", S.findMatches("abc", "").length === 0);
ok("位置正确", S.findMatches("xxhixx", "hi")[0].start === 2);

console.log("=== highlightHtml（含 XSS）===");
ok("命中包 mark", S.highlightHtml("find me here", "me").includes("<mark"));
ok("无命中也转义", S.highlightHtml("<b>x</b>", "zzz").includes("&lt;b&gt;"));
ok("命中内容转义(无真标签)", !S.highlightHtml("<script>alert(1)</script>", "alert").includes("<script>") && S.highlightHtml("<script>", "script").includes("&lt;"));
ok("不产生真标签", !S.highlightHtml("<img onerror=x>", "img").includes("<img"));
ok("active 高亮", S.highlightHtml("a a a", "a", 1).includes('class="search-hit active"'));
ok("非 active 普通", (S.highlightHtml("a a", "a", 0).match(/search-hit"/g) || []).length >= 1);

console.log("=== snippet ===");
ok("命中居中带省略", S.snippet("x".repeat(100) + "目标" + "y".repeat(100), "目标").includes("目标"));
ok("命中带省略号", S.snippet("a".repeat(60) + "目标" + "b".repeat(60), "目标").includes("…"));
ok("无命中取开头", typeof S.snippet("abc", "zzz") === "string");

console.log("=== searchMessages ===");
const msgs = [
  { role: "user", content: "请讲讲 BM25 检索" },
  { role: "assistant", content: "BM25 是一种词法检索算法，BM25 很经典" },
  { role: "user", content: [{ type: "text", text: "那语义检索呢" }, { type: "image_url" }] },
  { role: "assistant", content: "语义检索用向量" },
];
const r = S.searchMessages(msgs, "BM25");
ok("命中消息数", r.results.length === 2);
ok("总匹配数(含重复)", r.totalMatches === 3);   // 第1条1次 + 第2条2次
ok("结果含角色", r.results[0].role === "user");
ok("结果含次数", r.results[1].count === 2);
ok("结果含片段", r.results[0].snippet.includes("BM25"));
ok("hits 扁平列表", r.hits.length === 3);
ok("hits 带消息索引", r.hits[0].msgIndex === 0);
const r2 = S.searchMessages(msgs, "语义检索");
ok("多模态文本可搜", r2.results.some(x => x.index === 2));
ok("空查询→空", S.searchMessages(msgs, "").totalMatches === 0);
ok("无命中→空", S.searchMessages(msgs, "zzzz").totalMatches === 0);

console.log("=== stepHit 环绕 ===");
ok("下一个", S.stepHit(0, 1, 3) === 1);
ok("末尾环绕", S.stepHit(2, 1, 3) === 0);
ok("首部回退环绕", S.stepHit(0, -1, 3) === 2);
ok("空→-1", S.stepHit(0, 1, 0) === -1);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
