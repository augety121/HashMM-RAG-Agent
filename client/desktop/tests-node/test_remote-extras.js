/** test_remote-extras.js — 双向剪贴板同步 + 会话录制控制器单测（纯逻辑）。 */
"use strict";
const assert = require("assert");
const X = require("../remote-extras.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== hashText ===");
ok("同输入同哈希", X.hashText("abc") === X.hashText("abc"));
ok("异输入异哈希", X.hashText("abc") !== X.hashText("abd"));
ok("空安全", typeof X.hashText("") === "string");
ok("null 安全", typeof X.hashText(null) === "string");

console.log("=== 剪贴板同步：防回环（核心）===");
let cs = new X.ClipboardSync({ minIntervalMs: 0 });
const s1 = cs.onLocalChange("hello", 1000);
ok("本地新内容→发送", s1.send === true && s1.payload.text === "hello");
const r1 = cs.onRemoteData({ text: "from-peer" });
ok("收到对端→写入本地", r1.apply === true && r1.text === "from-peer");
const loop = cs.onLocalChange("from-peer", 2000);
ok("写入后同内容本地变更→判回环不回传", loop.send === false && loop.reason === "echo_from_peer");

console.log("=== 剪贴板同步：去重 / 防抖 / 截断 ===");
cs = new X.ClipboardSync({ minIntervalMs: 0 });
cs.onLocalChange("dup", 1000);
ok("相同内容→去重", cs.onLocalChange("dup", 2000).send === false);
cs = new X.ClipboardSync({ minIntervalMs: 500 });
cs.onLocalChange("a", 1000);
ok("间隔过短→防抖丢弃", cs.onLocalChange("b", 1200).send === false);
ok("间隔足够→放行", cs.onLocalChange("c", 2000).send === true);
const big = new X.ClipboardSync({ maxBytes: 10, minIntervalMs: 0 });
const bigR = big.onLocalChange("0123456789ABCDEF", 1000);
ok("超限→截断并标记", bigR.send === true && bigR.payload.truncated === true && bigR.payload.text.length <= 10);
ok("空内容→不发", new X.ClipboardSync().onLocalChange("", 1).send === false);
ok("对端空内容→不写", new X.ClipboardSync().onRemoteData({ text: "" }).apply === false);
const cs2 = new X.ClipboardSync();
cs2.onRemoteData({ text: "x", hash: X.hashText("x") });
ok("对端重复内容→不重复写", cs2.onRemoteData({ text: "x", hash: X.hashText("x") }).apply === false);

console.log("=== 录制：容器选择 ===");
ok("优先 VP9", X.chooseMimeType(["video/webm;codecs=vp9", "video/webm", "video/mp4"]) === "video/webm;codecs=vp9");
ok("退 VP8", X.chooseMimeType(["video/webm;codecs=vp8", "video/mp4"]) === "video/webm;codecs=vp8");
ok("退 mp4", X.chooseMimeType(["video/mp4"]) === "video/mp4");
ok("函数式判定", X.chooseMimeType((m) => m === "video/webm") === "video/webm");
ok("都不支持→null", X.chooseMimeType([]) === null);

console.log("=== 录制：文件名 ===");
const fn = X.makeRecordingFilename("sess", "video/webm;codecs=vp9", new Date(2026, 5, 20, 9, 8, 7));
ok("webm 扩展名", fn.endsWith(".webm"));
ok("含时间戳", /20260620-090807/.test(fn));
ok("mp4→mp4 扩展名", X.makeRecordingFilename("s", "video/mp4", Date.now()).endsWith(".mp4"));

console.log("=== 录制：状态机 + 计账 ===");
const rc = new X.RecordingController();
ok("初始 idle", rc.state === "idle" && !rc.isRecording());
const st = rc.start("video/webm;codecs=vp9", 1000);
ok("开始→recording", st.ok && rc.isRecording());
ok("开始带文件名", typeof st.filename === "string" && st.filename.endsWith(".webm"));
ok("重复开始→拒", rc.start("video/webm", 1100).ok === false);
rc.onChunk(5000); rc.onChunk(3000); rc.onChunk(0);
const fin = rc.stop(6000);
ok("停止汇总字节", fin.bytes === 8000);
ok("停止汇总块数(0字节块不计)", fin.chunks === 2);
ok("停止汇总时长", fin.durationMs === 5000);
ok("停止后非录制态", !rc.isRecording() && rc.state === "stopped");
ok("非录制态再停→拒", rc.stop(7000).ok === false);
ok("非录制态 onChunk→忽略", new X.RecordingController().onChunk(100) === false);
const sm = rc.summary(6000);
ok("summary 含状态/时长/文件名", sm.state === "stopped" && sm.durationMs === 5000 && !!sm.filename);

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
