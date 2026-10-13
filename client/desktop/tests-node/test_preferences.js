/** test_preferences.js — 偏好设置纯逻辑单测。 */
"use strict";
const assert = require("assert");
const P = require("../preferences.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== normalize 默认+校验 ===");
ok("空→全默认", JSON.stringify(P.normalize({})) === JSON.stringify(P.DEFAULTS));
ok("null→全默认", P.normalize(null).theme === "dark");
ok("非法主题→默认", P.normalize({ theme: "rainbow" }).theme === "dark");
ok("合法主题保留", P.normalize({ theme: "light" }).theme === "light");
ok("字号下裁剪", P.normalize({ fontSize: 5 }).fontSize === P.FONT_MIN);
ok("字号上裁剪", P.normalize({ fontSize: 99 }).fontSize === P.FONT_MAX);
ok("字号合法保留", P.normalize({ fontSize: 16 }).fontSize === 16);
ok("非数字字号→默认", P.normalize({ fontSize: "big" }).fontSize === P.DEFAULTS.fontSize);
ok("非法密度→默认", P.normalize({ density: "x" }).density === "comfortable");
ok("sendOnEnter 布尔化", P.normalize({ sendOnEnter: 0 }).sendOnEnter === false);
ok("renderMarkdown 默认true", P.normalize({}).renderMarkdown === true);

console.log("=== effectiveTheme ===");
ok("dark→dark", P.effectiveTheme("dark", false) === "dark");
ok("light→light", P.effectiveTheme("light", true) === "light");
ok("auto+系统暗→dark", P.effectiveTheme("auto", true) === "dark");
ok("auto+系统亮→light", P.effectiveTheme("auto", false) === "light");
ok("非法→dark", P.effectiveTheme("xxx", false) === "dark");

console.log("=== cssVariables ===");
const dark = P.cssVariables({ theme: "dark", fontSize: 14 }, false);
ok("暗色背景", dark.vars["--bg-primary"] === "#1a1a1e");
ok("有效主题=dark", dark.effectiveTheme === "dark");
ok("字号变量", dark.vars["--app-font-size"] === "14px");
const light = P.cssVariables({ theme: "light", fontSize: 16 }, false);
ok("亮色背景", light.vars["--bg-primary"] === "#ffffff");
ok("亮色文字深色", light.vars["--text-primary"] === "#1a1a1e");
ok("有效主题=light", light.effectiveTheme === "light");
ok("字号16", light.vars["--app-font-size"] === "16px");
const auto = P.cssVariables({ theme: "auto" }, true);
ok("auto 跟随系统暗", auto.effectiveTheme === "dark" && auto.vars["--bg-primary"] === "#1a1a1e");
const compact = P.cssVariables({ density: "compact" }, false);
ok("紧凑行距", compact.vars["--app-line-height"] === "1.5");
ok("舒适行距", P.cssVariables({ density: "comfortable" }, false).vars["--app-line-height"] === "1.75");

console.log("=== update 单项 ===");
ok("改主题", P.update({}, "theme", "light").theme === "light");
ok("改字号", P.update({}, "fontSize", 18).fontSize === 18);
ok("改字号越界裁剪", P.update({}, "fontSize", 999).fontSize === P.FONT_MAX);
ok("非法 key 忽略", JSON.stringify(P.update({}, "hacker", "x")) === JSON.stringify(P.DEFAULTS));
ok("保留其他项", P.update({ theme: "light" }, "fontSize", 16).theme === "light");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
