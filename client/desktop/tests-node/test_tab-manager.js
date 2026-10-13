/** test_tab-manager.js — 多标签会话纯逻辑单测。 */
"use strict";
const assert = require("assert");
const T = require("../tab-manager.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== openTab ===");
let s = { tabs: [], activeId: null };
s = T.openTab(s, { id: "a", title: "对话A" });
ok("打开第1个", s.tabs.length === 1 && s.activeId === "a");
s = T.openTab(s, { id: "b", title: "对话B" });
ok("打开第2个并激活", s.tabs.length === 2 && s.activeId === "b");
s = T.openTab(s, { id: "a" });
ok("重开已存在→仅激活", s.tabs.length === 2 && s.activeId === "a");
ok("不重复添加", s.tabs.filter(t => t.id === "a").length === 1);
s = T.openTab(s, { id: "a", title: "改名A" });
ok("重开可更新标题", s.tabs.find(t => t.id === "a").title === "改名A");

console.log("=== 上限 ===");
let big = { tabs: [], activeId: null };
for (let i = 0; i < T.MAX_TABS + 3; i++) big = T.openTab(big, { id: "t" + i });
ok("不超过上限", big.tabs.length === T.MAX_TABS);
ok("溢出标记", big.overflow === true);

console.log("=== closeTab + 相邻选择 ===");
let c = { tabs: [{ id: "x" }, { id: "y" }, { id: "z" }], activeId: "y" };
c = T.closeTab(c, "y");
ok("关激活→选左邻", c.activeId === "x" && c.tabs.length === 2);
let c2 = { tabs: [{ id: "x" }, { id: "y" }, { id: "z" }], activeId: "x" };
c2 = T.closeTab(c2, "x");
ok("关首个激活→选右邻", c2.activeId === "y");
let c3 = { tabs: [{ id: "x" }, { id: "y" }], activeId: "x" };
c3 = T.closeTab(c3, "y");
ok("关非激活→激活不变", c3.activeId === "x" && c3.tabs.length === 1);
let c4 = { tabs: [{ id: "only" }], activeId: "only" };
c4 = T.closeTab(c4, "only");
ok("关最后一个→activeId null", c4.activeId === null && c4.tabs.length === 0);
ok("关不存在→不变", T.closeTab({ tabs: [{ id: "x" }], activeId: "x" }, "nope").tabs.length === 1);

console.log("=== setActive / rename / getActive ===");
let d = { tabs: [{ id: "a" }, { id: "b" }], activeId: "a" };
ok("激活存在的", T.setActive(d, "b").activeId === "b");
ok("激活不存在→不变", T.setActive(d, "nope").activeId === "a");
ok("重命名", T.renameTab(d, "a", "新名").tabs.find(t => t.id === "a").title === "新名");
ok("重命名空→保留原", T.renameTab({ tabs: [{ id: "a", title: "原" }], activeId: "a" }, "a", "  ").tabs[0].title === "原");
ok("getActive", T.getActive(d).id === "a");
ok("getActive 无→null", T.getActive({ tabs: [], activeId: null }) === null);

console.log("=== activateAdjacent 环绕 ===");
let e = { tabs: [{ id: "a" }, { id: "b" }, { id: "c" }], activeId: "a" };
ok("右移", T.activateAdjacent(e, 1).activeId === "b");
ok("左移环绕到末", T.activateAdjacent(e, -1).activeId === "c");
let e2 = { tabs: [{ id: "a" }, { id: "b" }, { id: "c" }], activeId: "c" };
ok("末尾右移环绕到首", T.activateAdjacent(e2, 1).activeId === "a");
ok("空列表不崩", T.activateAdjacent({ tabs: [], activeId: null }, 1).activeId === null);

console.log("=== 纯函数（不改原状态）===");
const orig = { tabs: [{ id: "a" }], activeId: "a" };
T.openTab(orig, { id: "b" });
ok("openTab 不改原", orig.tabs.length === 1);
T.closeTab(orig, "a");
ok("closeTab 不改原", orig.tabs.length === 1);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
