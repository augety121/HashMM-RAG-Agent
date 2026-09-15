/** test_localllm-setup.js — 本地模型安装向导纯逻辑单测。 */
"use strict";
const assert = require("assert");
const S = require("../localllm-setup.js");

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== verifyModelFile ===");
ok("不存在→拒", S.verifyModelFile({ exists: false }).ok === false);
ok("够大→过", S.verifyModelFile({ exists: true, size: 4.5 * 1024 * 1024 * 1024, name: "m.gguf" }).ok === true);
ok("偏小→拒", S.verifyModelFile({ exists: true, size: 100 * 1024 * 1024 }).ok === false);
ok("偏小给出GB原因", S.verifyModelFile({ exists: true, size: 100 * 1024 * 1024 }).reason.includes("GB"));
ok("非gguf扩展名→拒", S.verifyModelFile({ exists: true, size: 5e9, name: "model.bin" }).ok === false);

console.log("=== buildSetupSteps 四步 ===");
const full = { tier: "high", hw: { totalVramMB: 24576, totalMemMB: 65536, hasGpu: true }, minVramMB: 6000, minMemMB: 16000, runtimeAvailable: true, ready: true, viable: true, modelFile: "m.gguf", modelPath: "/m/m.gguf", modelsDir: "/m", modelSize: 4.5e9 };
let steps = S.buildSetupSteps(full);
ok("四步", steps.length === 4);
ok("步骤顺序", steps.map(s => s.id).join(",") === "hardware,runtime,model,ready");
ok("全就绪→全ok", steps.every(s => s.state === "ok"));
ok("硬件含显存", steps[0].detail.includes("显存"));

console.log("=== 各步骤未就绪状态 ===");
const noRuntime = S.buildSetupSteps(Object.assign({}, full, { runtimeAvailable: false, viable: false }));
ok("无运行时→todo", noRuntime.find(s => s.id === "runtime").state === "todo");
ok("无运行时给指引", !!noRuntime.find(s => s.id === "runtime").action);
ok("无运行时→就绪未达", noRuntime.find(s => s.id === "ready").state === "todo");

const noModel = S.buildSetupSteps(Object.assign({}, full, { ready: false, viable: false }));
ok("无模型→todo", noModel.find(s => s.id === "model").state === "todo");
ok("无模型指引含目录", noModel.find(s => s.id === "model").action.includes("/m"));
ok("无模型指引提 lora-to-gguf", noModel.find(s => s.id === "model").action.includes("gguf"));

console.log("=== 硬件分级 ===");
const cpuOnly = S.buildSetupSteps({ hw: { totalVramMB: 0, totalMemMB: 32768, hasGpu: false }, minVramMB: 6000, minMemMB: 16000, runtimeAvailable: true, ready: true, viable: true, modelSize: 5e9 });
ok("无GPU但内存够→warn(CPU路径)", cpuOnly.find(s => s.id === "hardware").state === "warn");
ok("CPU路径提示较慢", cpuOnly.find(s => s.id === "hardware").detail.includes("CPU"));
const weak = S.buildSetupSteps({ hw: { totalVramMB: 0, totalMemMB: 4096, hasGpu: false }, minVramMB: 6000, minMemMB: 16000, runtimeAvailable: true, ready: true, viable: true });
ok("硬件太弱→blocked", weak.find(s => s.id === "hardware").state === "blocked");

console.log("=== overallState ===");
ok("全就绪→ready", S.overallState(full).ready === true);
ok("缺运行时→未就绪+计数", S.overallState(Object.assign({}, full, { runtimeAvailable: false, viable: false })).ready === false);
ok("缺两步→pending", S.overallState(Object.assign({}, full, { runtimeAvailable: false, ready: false, viable: false })).pending >= 1);
ok("硬件不达标→blocked", S.overallState(weak.length ? { hw: { totalMemMB: 2048, hasGpu: false }, minMemMB: 16000, runtimeAvailable: false, ready: false } : {}).blocked === true);
ok("summary 文案", typeof S.overallState(full).summary === "string");
ok("空状态不崩", typeof S.overallState({}).summary === "string");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
