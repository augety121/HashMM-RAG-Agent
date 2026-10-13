/** test_notify-core.js — 桌面通知与未读纯逻辑单测。 */
"use strict";
const assert = require("assert");
const N = require("../notify-core.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== shouldNotify ===");
ok("前台(聚焦+可见)→不通知", N.shouldNotify({ enabled: true, focused: true, visible: true, minimized: false }) === false);
ok("未聚焦→通知", N.shouldNotify({ enabled: true, focused: false, visible: true }) === true);
ok("最小化→通知", N.shouldNotify({ enabled: true, focused: true, visible: true, minimized: true }) === true);
ok("不可见→通知", N.shouldNotify({ enabled: true, focused: true, visible: false }) === true);
ok("关闭通知→不通知", N.shouldNotify({ enabled: false, focused: false }) === false);
ok("默认 enabled 视为开", N.shouldNotify({ focused: false }) === true);
ok("空→通知(不在前台)", N.shouldNotify(null) === true);

console.log("=== shouldCountUnread ===");
ok("前台→不计未读", N.shouldCountUnread({ focused: true, visible: true }) === false);
ok("后台→计未读", N.shouldCountUnread({ focused: false }) === true);
ok("忽略 enabled(未读独立于通知开关)", N.shouldCountUnread({ enabled: false, focused: false }) === true);

console.log("=== badgeText ===");
ok("0→空", N.badgeText(0) === "");
ok("5→5", N.badgeText(5) === "5");
ok("99→99", N.badgeText(99) === "99");
ok("100→99+", N.badgeText(100) === "99+");
ok("负→空", N.badgeText(-3) === "");
ok("小数取整", N.badgeText(3.7) === "3");

console.log("=== notificationContent ===");
const c = N.notificationContent("这是助手的一段较长回答内容".repeat(10));
ok("有标题", typeof c.title === "string" && c.title.length > 0);
ok("正文截断", c.body.includes("…"));
ok("自定义标题", N.notificationContent("x", { title: "自定义" }).title === "自定义");
ok("空消息→兜底", N.notificationContent("").body === "新消息");
ok("多空白压缩", !N.notificationContent("a    b\n\nc").body.includes("\n"));

console.log("=== addUnread ===");
ok("累加", N.addUnread(2, 1) === 3);
ok("从0累加", N.addUnread(0, 1) === 1);
ok("减不为负", N.addUnread(1, -5) === 0);
ok("非数字当0", N.addUnread("x", 1) === 1);
ok("清零(delta=-current)", N.addUnread(5, -5) === 0);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
