/** test_message-actions.js — 单条消息操作纯逻辑单测。 */
"use strict";
const assert = require("assert");
const M = require("../message-actions.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== extractText ===");
ok("字符串直取", M.extractText("hello") === "hello");
ok("多模态取文本", M.extractText([{ type: "text", text: "看图" }, { type: "image_url" }]) === "看图");
ok("多段文本拼接", M.extractText([{ type: "text", text: "a" }, { type: "text", text: "b" }]) === "a\nb");
ok("空→空串", M.extractText(null) === "");

console.log("=== buildQuote ===");
const q = M.buildQuote({ role: "assistant", content: "这是助手的回答" });
ok("含引用标记", q.includes("> 引用助手"));
ok("内容加 > 前缀", q.includes("> 这是助手的回答"));
ok("以双换行结尾", q.endsWith("\n\n"));
ok("用户角色", M.buildQuote({ role: "user", content: "我的问题" }).includes("引用你"));
ok("过长折叠", M.buildQuote({ role: "assistant", content: "x".repeat(300) }).includes("…"));
ok("空消息→空", M.buildQuote({ role: "user", content: "" }) === "");
ok("多模态可引用", M.buildQuote({ role: "user", content: [{ type: "text", text: "图问" }] }).includes("图问"));

console.log("=== buildShareText ===");
const s = M.buildShareText({ role: "assistant", content: "分享内容" });
ok("含角色", s.includes("助手："));
ok("含内容", s.includes("分享内容"));
ok("带标题", M.buildShareText({ role: "user", content: "x" }, { title: "对话A" }).includes("【对话A】"));
ok("无标题不加", !M.buildShareText({ role: "user", content: "x" }).includes("【"));

console.log("=== copyText ===");
ok("复制取文本", M.copyText({ content: "复制我" }) === "复制我");
ok("多模态复制文本", M.copyText({ content: [{ type: "text", text: "文本部分" }] }) === "文本部分");
ok("空消息→空", M.copyText({ content: null }) === "");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
