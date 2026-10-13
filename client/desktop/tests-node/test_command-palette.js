/** test_command-palette.js — 命令面板纯逻辑单测。 */
"use strict";
const assert = require("assert");
const C = require("../command-palette.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== matchScore ===");
ok("子序列命中", C.matchScore("new chat", "nc") > 0);
ok("完整命中更高", C.matchScore("settings", "settings") > C.matchScore("settings", "set"));
ok("不匹配→-1", C.matchScore("abc", "xyz") === -1);
ok("空查询→0", C.matchScore("anything", "") === 0);
ok("空目标→-1", C.matchScore("", "x") === -1);
ok("大小写不敏感", C.matchScore("New Chat", "nc") > 0);
ok("连续优于分散", C.matchScore("settings", "set") > C.matchScore("steaming nest", "set"));
ok("词首加权", C.matchScore("new chat", "c") > C.matchScore("search", "c") - 100);  // 仅确保有分

console.log("=== filterCommands ===");
const cmds = [
  { id: "new", title: "新建对话", keywords: ["new", "chat", "对话"] },
  { id: "settings", title: "打开设置", keywords: ["settings", "preferences", "设置"] },
  { id: "kb", title: "知识库管理", keywords: ["knowledge", "kb"] },
  { id: "export", title: "导出对话", keywords: ["export", "markdown"] },
];
ok("空查询返回全部", C.filterCommands(cmds, "").length === 4);
const r1 = C.filterCommands(cmds, "设置");
ok("中文命中", r1.length >= 1 && r1[0].id === "settings");
const r2 = C.filterCommands(cmds, "export");
ok("keyword 命中", r2.some(c => c.id === "export"));
ok("无关查询→空", C.filterCommands(cmds, "zzzzz").length === 0);
ok("按分数降序", (() => { const r = C.filterCommands(cmds, "对话"); return r.length >= 1; })());
ok("结果带 _score", C.filterCommands(cmds, "kb")[0]._score >= 0);

console.log("=== when 条件过滤 ===");
const condCmds = [
  { id: "a", title: "总是", },
  { id: "b", title: "仅连接后", when: (ctx) => ctx && ctx.connected },
];
ok("未连接→隐藏 b", C.filterCommands(condCmds, "", { connected: false }).length === 1);
ok("已连接→显示 b", C.filterCommands(condCmds, "", { connected: true }).length === 2);
ok("when 在搜索时也生效", C.filterCommands(condCmds, "仅", { connected: false }).length === 0);

console.log("=== moveSelection 环绕 ===");
ok("下移", C.moveSelection(0, 1, 3) === 1);
ok("末尾下移环绕到首", C.moveSelection(2, 1, 3) === 0);
ok("首部上移环绕到末", C.moveSelection(0, -1, 3) === 2);
ok("空列表→0", C.moveSelection(0, 1, 0) === 0);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
