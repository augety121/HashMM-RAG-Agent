/** test_chat-export.js — 对话导出纯逻辑单测。 */
"use strict";
const assert = require("assert");
const E = require("../chat-export.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

const conv = {
  title: "测试对话",
  createdAt: 1700000000000,
  messages: [
    { role: "system", content: "系统提示" },
    { role: "user", content: "你好" },
    { role: "assistant", content: "你好！有什么可以帮你？" },
    { role: "user", content: [{ type: "text", text: "看这张图" }, { type: "image_url", image_url: {} }] },
  ],
};

console.log("=== toMarkdown ===");
const md = E.toMarkdown(conv);
ok("含标题", md.includes("# 测试对话"));
ok("含用户消息", md.includes("你好") && md.includes("🧑 你"));
ok("含助手消息", md.includes("🤖 助手") && md.includes("有什么可以帮你"));
ok("默认不含 system", !md.includes("系统提示"));
ok("含 system 选项", E.toMarkdown(conv, { includeSystem: true }).includes("系统提示"));
ok("多模态文本提取", md.includes("看这张图"));
ok("多模态图片占位", md.includes("已附截图"));
ok("含导出时间", md.includes("导出时间"));
ok("以换行结尾", md.endsWith("\n"));

console.log("=== toPlainText ===");
const txt = E.toPlainText(conv);
ok("含标题", txt.includes("测试对话"));
ok("角色标注", txt.includes("【你】") && txt.includes("【助手】"));
ok("不含 system", !txt.includes("系统提示"));
ok("多模态文本", txt.includes("看这张图"));

console.log("=== exportFilename ===");
ok("基本", E.exportFilename("我的对话", "md") === "我的对话.md");
ok("去非法字符", !E.exportFilename('a/b:c*d?.md', "md").includes("/"));
ok("空格转下划线", E.exportFilename("a b c", "md") === "a_b_c.md");
ok("空标题→对话", E.exportFilename("", "md") === "对话.md");
ok("超长截断", E.exportFilename("一".repeat(80), "md").length <= 53);
ok("扩展名带点也行", E.exportFilename("x", ".pdf") === "x.pdf");
ok("pdf 扩展名", E.exportFilename("报告", "pdf") === "报告.pdf");

console.log("=== 边界 ===");
ok("空会话不崩", typeof E.toMarkdown(null) === "string");
ok("无消息→只有标题", E.toMarkdown({ title: "空" }).includes("# 空"));
ok("空 messages 数组", typeof E.toPlainText({ messages: [] }) === "string");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
