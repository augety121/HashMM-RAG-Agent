/** test_semantic-status.js — 语义检索设置状态纯逻辑单测。 */
"use strict";
const assert = require("assert");
const S = require("../semantic-status.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== coverageInfo ===");
ok("已同步", S.coverageInfo(100, 100).synced === true && S.coverageInfo(100, 100).pct === 100);
ok("超量也算同步", S.coverageInfo(120, 100).synced === true);
ok("部分同步→stale", S.coverageInfo(50, 100).stale === true && S.coverageInfo(50, 100).pct === 50);
ok("未建索引", S.coverageInfo(0, 100).vectors === 0 && S.coverageInfo(0, 100).label.includes("未建索引"));
ok("KB为空+有向量", S.coverageInfo(10, 0).label.includes("10"));
ok("全空不崩", typeof S.coverageInfo(0, 0).label === "string");

console.log("=== buildSemanticSteps 五步 ===");
const full = { deviceOk: true, memGB: 16, cores: 8, ortInstalled: true, modelDownloaded: true, enabled: true, vectors: 100, kbChunks: 100 };
let steps = S.buildSemanticSteps(full);
ok("五步", steps.length === 5);
ok("步骤顺序", steps.map(s => s.id).join(",") === "device,runtime,model,enabled,index");
ok("全就绪→全ok", steps.every(s => s.state === "ok"));

console.log("=== 各未就绪状态 ===");
const noOrt = S.buildSemanticSteps(Object.assign({}, full, { ortInstalled: false, enabled: false }));
ok("无ONNX→runtime blocked", noOrt.find(s => s.id === "runtime").state === "blocked");
ok("无ONNX→model todo", noOrt.find(s => s.id === "model").state === "todo");
const noModel = S.buildSemanticSteps(Object.assign({}, full, { modelDownloaded: false, enabled: false }));
ok("无模型→todo", noModel.find(s => s.id === "model").state === "todo");
ok("无模型给下载指引", !!noModel.find(s => s.id === "model").action);
const lowDev = S.buildSemanticSteps(Object.assign({}, full, { deviceOk: false }));
ok("低配设备→warn", lowDev.find(s => s.id === "device").state === "warn");

console.log("=== 向量索引步骤 ===");
const stale = S.buildSemanticSteps(Object.assign({}, full, { vectors: 50, kbChunks: 100 }));
ok("索引落后→warn", stale.find(s => s.id === "index").state === "warn");
ok("落后给重建指引", stale.find(s => s.id === "index").action.includes("重建"));
const noIndex = S.buildSemanticSteps(Object.assign({}, full, { vectors: 0, kbChunks: 100 }));
ok("启用但无索引→todo", noIndex.find(s => s.id === "index").state === "todo");
const notEnabled = S.buildSemanticSteps(Object.assign({}, full, { enabled: false }));
ok("未启用→索引 todo", notEnabled.find(s => s.id === "index").state === "todo");

console.log("=== overallSemantic ===");
ok("全就绪→active+synced", S.overallSemantic(full).active === true && S.overallSemantic(full).synced === true);
ok("无ONNX→blocked", S.overallSemantic(Object.assign({}, full, { ortInstalled: false })).blocked === true);
ok("未启用→提示BM25", S.overallSemantic(Object.assign({}, full, { enabled: false })).summary.includes("BM25"));
ok("启用但索引落后→提示待补齐", S.overallSemantic(Object.assign({}, full, { vectors: 10, kbChunks: 100 })).summary.includes("待补齐"));
ok("空状态不崩", typeof S.overallSemantic({}).summary === "string");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
