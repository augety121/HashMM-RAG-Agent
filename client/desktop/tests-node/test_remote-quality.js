/** test_remote-quality.js — 远控自适应质量/编码偏好单测（纯逻辑）。运行：node desktop/tests-node/test_remote-quality.js */
"use strict";
const assert = require("assert");
const Q = require("../remote-quality.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== 老卡 H265 quirk（对标 UU）===");
ok("GTX 960 控制端禁H265", Q.shouldDisableH265Decode("NVIDIA GeForce GTX 960", "controller") === true);
ok("GTX 760 控制端禁H265", Q.shouldDisableH265Decode("GeForce GTX 760", "controller") === true);
ok("GTX 980 Ti 禁H265", Q.shouldDisableH265Decode("NVIDIA GeForce GTX 980 Ti", "controller") === true);
ok("Quadro 禁H265", Q.shouldDisableH265Decode("NVIDIA Quadro P2000", "controller") === true);
ok("GTX 1660 不禁(新卡)", Q.shouldDisableH265Decode("NVIDIA GeForce GTX 1660", "controller") === false);
ok("RTX 3080 不禁", Q.shouldDisableH265Decode("NVIDIA GeForce RTX 3080", "controller") === false);
ok("被控端(编码侧)不受限", Q.shouldDisableH265Decode("GTX 960", "host") === false);
ok("空 GPU 名不禁", Q.shouldDisableH265Decode("", "controller") === false);

console.log("=== chooseCodecPreference 排序 ===");
const caps = [
  { mimeType: "video/VP8" }, { mimeType: "video/H264" }, { mimeType: "video/VP9" },
  { mimeType: "video/H265" }, { mimeType: "video/rtx" },
];
const pref = Q.chooseCodecPreference(caps, { disableH265Decode: true });
ok("H264 优先(首位)", pref[0].mimeType === "video/H264");
ok("禁用时 H265 不在前排", pref.slice(0, 3).every(c => c.mimeType !== "video/H265"));
ok("辅助 codec(rtx) 保留", pref.some(c => c.mimeType === "video/rtx"));
const pref2 = Q.chooseCodecPreference(caps, { disableH265Decode: false, peerSupportsH265: true });
ok("两端支持且非老卡→H265 提前", pref2[0].mimeType === "video/H265");
ok("空输入→空数组", Q.chooseCodecPreference(null, {}).length === 0);

console.log("=== adaptQuality 自适应升降档 ===");
const down = Q.adaptQuality({ packetLossPct: 8, rttMs: 50 }, 3);
ok("丢包高→降档", down.changed && down.direction === "down" && down.tierIndex === 2);
ok("降档原因含丢包", down.reason.includes("丢包"));
const downRtt = Q.adaptQuality({ packetLossPct: 0.1, rttMs: 400 }, 3);
ok("RTT高→降档", downRtt.direction === "down");
const up = Q.adaptQuality({ packetLossPct: 0.1, rttMs: 40, availableOutgoingBitrateKbps: 20000 }, 2);
ok("网络好+带宽足→升档", up.changed && up.direction === "up" && up.tierIndex === 3);
const noUp = Q.adaptQuality({ packetLossPct: 0.1, rttMs: 40, availableOutgoingBitrateKbps: 1000 }, 2);
ok("网络好但带宽不足→不升(防抖)", !noUp.changed && noUp.direction === "hold");
const hold = Q.adaptQuality({ packetLossPct: 2, rttMs: 150 }, 2);
ok("中间区→保持", !hold.changed);
ok("最低档不再降", Q.adaptQuality({ packetLossPct: 50, rttMs: 999 }, 0).tierIndex === 0);
ok("最高档不再升", Q.adaptQuality({ packetLossPct: 0, rttMs: 10, availableOutgoingBitrateKbps: 99999 }, Q.tierCount() - 1).tierIndex === Q.tierCount() - 1);
ok("缺统计→保持不崩", typeof Q.adaptQuality({}, 2).tierIndex === "number");
ok("返回带码率/帧率", Number.isFinite(up.bitrateKbps) && Number.isFinite(up.maxFps));

console.log("=== extractStats 解析 RTCStatsReport ===");
const entries = [
  { type: "remote-inbound-rtp", roundTripTime: 0.08, fractionLost: 0.03 },
  { type: "outbound-rtp", kind: "video", framesPerSecond: 58 },
  { type: "candidate-pair", nominated: true, availableOutgoingBitrate: 6000000, currentRoundTripTime: 0.09 },
];
const st = Q.extractStats(entries);
ok("RTT 解析(ms)", Math.abs(st.rttMs - 80) < 1e-6);
ok("丢包率解析(%)", Math.abs(st.packetLossPct - 3) < 1e-6);
ok("可用带宽解析(kbps)", Math.abs(st.availableOutgoingBitrateKbps - 6000) < 1e-6);
ok("帧率解析", st.framesPerSecond === 58);
ok("空报告不崩", typeof Q.extractStats([]).rttMs === "number");

console.log("=== 端到端：解析→自适应 ===");
const st2 = Q.extractStats([{ type: "remote-inbound-rtp", roundTripTime: 0.3, fractionLost: 0.1 }]);
const decided = Q.adaptQuality(st2, 3);
ok("差网络解析后触发降档", decided.direction === "down");

console.log("=== 有界跨端质量事实 ===");
const telemetry = Q.buildQualityTelemetry(st2, decided, { mode: "account", generation: 7, sampledAt: 1234 });
ok("质量事实 schema 稳定", telemetry.schema === "hashmm.remote-quality.v1");
ok("质量事实保留代次但不含原始 RTCStats", telemetry.generation === 7 && !("report" in telemetry));
ok("差网络明确标为受限", telemetry.health.grade === "poor" && telemetry.health.label === "连接受限");
ok("非法/无穷指标被清空", Q.buildQualityTelemetry({ rttMs: Infinity, packetLossPct: -8 }, {}, {}).metrics.rttMs === null);
ok("遥测不暴露候选地址或 SDP", !JSON.stringify(telemetry).includes("candidate") && !JSON.stringify(telemetry).includes("sdp"));

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
