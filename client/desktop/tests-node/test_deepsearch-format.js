/** test_deepsearch-format.js — 深度检索结果结构化展示纯逻辑单测。 */
"use strict";
const assert = require("assert");
const F = require("../deepsearch-format.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== extractCitations ===");
ok("单引用", JSON.stringify(F.extractCitations("答案见 [1]。")) === JSON.stringify([1]));
ok("多引用合并去重", JSON.stringify(F.extractCitations("[1] 和 [2] 还有 [1]")) === JSON.stringify([1, 2]));
ok("[1,2] 写法", JSON.stringify(F.extractCitations("见 [1,2]")) === JSON.stringify([1, 2]));
ok("[1, 2] 带空格", JSON.stringify(F.extractCitations("见 [1, 2, 3]")) === JSON.stringify([1, 2, 3]));
ok("升序", JSON.stringify(F.extractCitations("[3] [1] [2]")) === JSON.stringify([1, 2, 3]));
ok("无引用→空", F.extractCitations("没有引用").length === 0);
ok("空文本→空", F.extractCitations("").length === 0);

console.log("=== normalizeSources 字段容错 ===");
const srcs = F.normalizeSources([
  { filename: "a.pdf", page: 3, text: "片段一", score: 0.9 },
  { file: "b.md", content: "片段二" },
  { source: "c.txt", page_no: 7, snippet: "片段三", relevance: 0.5 },
]);
ok("编号 1-indexed", srcs[0].n === 1 && srcs[2].n === 3);
ok("filename 字段", srcs[0].file === "a.pdf");
ok("file 字段", srcs[1].file === "b.md");
ok("source 字段", srcs[2].file === "c.txt");
ok("page 直取", srcs[0].page === 3);
ok("page_no 兜底", srcs[2].page === 7);
ok("无 page→null", srcs[1].page === null);
ok("text/content/snippet 都认", srcs[0].snippet === "片段一" && srcs[1].snippet === "片段二" && srcs[2].snippet === "片段三");
ok("score/relevance 都认", srcs[0].score === 0.9 && srcs[2].score === 0.5);
ok("片段截断", F.normalizeSources([{ file: "x", text: "y".repeat(999) }])[0].snippet.includes("…"));
ok("空来源→空数组", F.normalizeSources(null).length === 0);
ok("缺文件名→未知来源", F.normalizeSources([{}])[0].file === "未知来源");

console.log("=== buildDisplay 组装 ===");
const disp = F.buildDisplay({
  answer: "结论是这样的 [1]，另见 [2]。", degraded: false,
  sources: [{ filename: "a.pdf", page: 1, text: "s1" }, { filename: "b.pdf", text: "s2" }],
  grounded: true, confidence: 0.83, rounds: 2,
});
ok("答案透传", disp.answer.includes("结论是这样的"));
ok("来源规范化", disp.sources.length === 2 && disp.sources[0].n === 1);
ok("引用编号", JSON.stringify(disp.citedNumbers) === JSON.stringify([1, 2]));
ok("meta.grounded", disp.meta.grounded === true);
ok("meta.confidence 百分比", disp.meta.confidence === 83);
ok("meta.rounds", disp.meta.rounds === 2);
ok("meta.sourceCount", disp.meta.sourceCount === 2);
ok("meta.citedCount", disp.meta.citedCount === 2);
const deg = F.buildDisplay({ answer: "简单答案", degraded: true });
ok("degraded 透传", deg.degraded === true);
ok("无来源→sourceCount 0", deg.meta.sourceCount === 0);
ok("grounded 缺失→null", deg.meta.grounded === null);

console.log("=== 子问题/多跳容错 ===");
ok("sub_questions", F.buildDisplay({ answer: "", sub_questions: ["q1", "q2"] }).subQuestions.length === 2);
ok("subqueries", F.buildDisplay({ answer: "", subqueries: ["q1"] }).subQuestions[0] === "q1");
ok("hops[].query", F.buildDisplay({ answer: "", hops: [{ query: "h1" }, { query: "h2" }] }).subQuestions.length === 2);
ok("无子问题→空", F.buildDisplay({ answer: "" }).subQuestions.length === 0);

console.log("=== metaSummary ===");
ok("通过自评文案", F.metaSummary({ grounded: true, sourceCount: 3 }).includes("通过自评"));
ok("资料不足文案", F.metaSummary({ grounded: false, sourceCount: 1 }).includes("资料可能不足"));
ok("含置信度", F.metaSummary({ confidence: 80, sourceCount: 2 }).includes("80%"));
ok("含引用数", F.metaSummary({ sourceCount: 2, citedCount: 3 }).includes("引用 3"));
ok("空 meta 不崩", typeof F.metaSummary(null) === "string");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
