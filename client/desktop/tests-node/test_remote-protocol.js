/** test_remote-protocol.js — 远控控制协议单测（纯逻辑）。运行：node desktop/tests-node/test_remote-protocol.js */
"use strict";
const assert = require("assert");
const P = require("../remote-protocol.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== 信封编解码 ===");
const enc = P.encode(P.T.INPUT, { action: "left_click", x: 500, y: 300 });
ok("编码产出字符串", typeof enc === "string");
const dec = P.decode(enc);
ok("解码 ok", dec.ok === true);
ok("版本=1", dec.v === P.PROTO_VERSION);
ok("类型=in", dec.t === P.T.INPUT);
ok("数据完整", dec.d.action === "left_click" && dec.d.x === 500);
ok("非法类型编码→null", P.encode("不存在的类型", {}) === null);
ok("带 seq", JSON.parse(P.encode(P.T.PING, {}, 7)).seq === 7);

console.log("=== 解码鲁棒性 ===");
ok("坏 JSON→error", P.decode("{不是json").ok === false);
ok("非对象→error", P.decode(123).ok === false);
ok("未知类型→error", P.decode(JSON.stringify({ v: 1, t: "zzz", d: {} })).ok === false);
ok("版本不符→error", P.decode(JSON.stringify({ v: 99, t: "in", d: {} })).ok === false);

console.log("=== 向后兼容旧报文 ===");
const leg = P.decode(JSON.stringify({ type: "input", action: "left_click", x: 1, y: 2 }));
ok("旧 input 升级为 INPUT", leg.ok && leg.t === P.T.INPUT && leg.legacy === true);
ok("旧报文 data 去掉 type", leg.d.type === undefined && leg.d.action === "left_click");
const legUnknown = P.decode(JSON.stringify({ type: "somethingNew", foo: 1 }));
ok("未映射旧类型原样透传(legacy)", legUnknown.ok && legUnknown.legacy === true && legUnknown.t === "somethingNew");

console.log("=== makeInput 构造 + 坐标裁剪 ===");
ok("合法动作产出对象", !!P.makeInput("left_click", { x: 100, y: 200 }));
ok("非法动作→null", P.makeInput("飞天遁地", {}) === null);
const clamp = P.makeInput("mouse_move", { x: 99999, y: -50 });
ok("x 上裁剪到1000", clamp.x === 1000);
ok("y 下裁剪到0", clamp.y === 0);
ok("text 截断到4096", P.makeInput("type", { text: "a".repeat(99999) }).text.length === 4096);
ok("keys 数组限长", P.makeInput("key", { keys: new Array(99).fill("ctrl") }).keys.length === 8);
ok("scroll_amount 裁剪", P.makeInput("scroll", { scroll_direction: "down", scroll_amount: 99999 }).scroll_amount === 100);

console.log("=== validateInput 校验 ===");
ok("合法点击通过", P.validateInput({ action: "left_click", x: 1, y: 2 }).ok === true);
ok("点击缺坐标→拒", P.validateInput({ action: "left_click" }).ok === false);
ok("坐标越界→拒", P.validateInput({ action: "left_click", x: 5000, y: 1 }).ok === false);
ok("拖拽缺终点→拒", P.validateInput({ action: "left_click_drag", x: 1, y: 2 }).ok === false);
ok("type 缺文本→拒", P.validateInput({ action: "type" }).ok === false);
ok("key 缺键→拒", P.validateInput({ action: "key" }).ok === false);
ok("无数据→拒", P.validateInput(null).ok === false);

console.log("=== toDriverAction 字段直通 ===");
const da = P.toDriverAction({ action: "scroll", scroll_direction: "up", scroll_amount: 3 });
ok("type=动作名", da.type === "scroll");
ok("滚动字段直通", da.scroll_direction === "up" && da.scroll_amount === 3);

console.log("=== 采集配置 / 设备控制 ===");
const cap = P.makeCaptureConfig({ fps: 999, width: 1920, height: 1080, bitrateKbps: 999999 });
ok("fps 裁剪到144", cap.fps === 144);
ok("bitrate 裁剪到80000", cap.bitrateKbps === 80000);
ok("分辨率保留", cap.width === 1920 && cap.height === 1080);
ok("合法设备动作", P.makeDeviceAction("lock").action === "lock");
ok("非法设备动作→null", P.makeDeviceAction("格式化硬盘") === null);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
