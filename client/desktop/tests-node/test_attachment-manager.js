/** test_attachment-manager.js — 附件管理纯逻辑单测。 */
"use strict";
const assert = require("assert");
const A = require("../attachment-manager.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== classifyFile ===");
ok("png→image", A.classifyFile({ name: "a.png" }).kind === "image");
ok("jpg→image", A.classifyFile({ name: "photo.JPG" }).kind === "image");
ok("type image/* →image", A.classifyFile({ name: "x", type: "image/png" }).kind === "image");
ok("md→text", A.classifyFile({ name: "readme.md" }).kind === "text");
ok("py→text", A.classifyFile({ name: "main.py" }).kind === "text");
ok("json→text", A.classifyFile({ name: "data.json" }).kind === "text");
ok("type text/* →text", A.classifyFile({ name: "x", type: "text/plain" }).kind === "text");
ok("exe→other", A.classifyFile({ name: "setup.exe" }).kind === "other");
ok("image 可内联", A.classifyFile({ name: "a.png" }).canInline === true);
ok("other 不可内联", A.classifyFile({ name: "a.zip" }).canInline === false);
ok("有类型标识", A.classifyFile({ name: "a.png" }).icon === "IMG");

console.log("=== validateAttachment ===");
ok("小图→ok", A.validateAttachment({ name: "a.png", size: 1024 }).ok === true);
ok("大图→拒", A.validateAttachment({ name: "a.png", size: 20 * 1024 * 1024 }).ok === false);
ok("大图原因", A.validateAttachment({ name: "a.png", size: 20 * 1024 * 1024 }).reason.includes("图片"));
ok("小文本→ok", A.validateAttachment({ name: "a.md", size: 1024 }).ok === true);
ok("大文本→拒", A.validateAttachment({ name: "a.md", size: 1024 * 1024 }).ok === false);
ok("其他类型→拒", A.validateAttachment({ name: "a.zip", size: 100 }).ok === false);
ok("其他类型原因", A.validateAttachment({ name: "a.exe", size: 1 }).reason.includes("不支持"));

console.log("=== humanSize ===");
ok("B", A.humanSize(500) === "500 B");
ok("KB", A.humanSize(2048) === "2.0 KB");
ok("MB", A.humanSize(3 * 1024 * 1024) === "3.0 MB");

console.log("=== attachmentLabel ===");
const lbl = A.attachmentLabel({ name: "report.md", size: 2048 });
ok("含文件名", lbl.name === "report.md");
ok("含大小", lbl.size === "2.0 KB");
ok("含类型标识", lbl.icon === "TXT");
ok("长名截断", A.attachmentLabel({ name: "a".repeat(50) + ".txt", size: 1 }).short.includes("…"));

console.log("=== buildTextContext ===");
const ctx = A.buildTextContext([{ name: "a.md", content: "内容一" }, { name: "b.py", content: "print(1)" }]);
ok("含引导语", ctx.includes("用户附带的文件"));
ok("含文件名", ctx.includes("a.md") && ctx.includes("b.py"));
ok("含内容", ctx.includes("内容一") && ctx.includes("print(1)"));
ok("分隔符", ctx.includes("---"));
ok("超长截断", A.buildTextContext([{ name: "big", content: "x".repeat(20000) }], { maxEach: 100 }).includes("已截断"));
ok("空→空串", A.buildTextContext([]) === "");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
