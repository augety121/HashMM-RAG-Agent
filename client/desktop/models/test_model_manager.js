/** test_model_manager.js — 本地模型管理单测（V101，纯逻辑）。
 *  运行：node desktop/models/test_model_manager.js
 */
"use strict";
const assert = require("assert");
const M = require("./model-manager");

let pass = 0;
const ok = (n) => { pass++; console.log("  ✔ " + n); };

// 1. 硬件能力分级
{
  assert.strictEqual(M.detectCapability({ totalVramMB: 24576, hasGpu: true }).tier, "high", "24G 显存 → high");
  assert.strictEqual(M.detectCapability({ totalVramMB: 12288, hasGpu: true }).tier, "mid", "12G 显存 → mid（同 Marvis 4070S 场景）");
  assert.strictEqual(M.detectCapability({ totalVramMB: 0, totalMemMB: 32768, cpuCount: 16 }).tier, "mid", "无独显但内存/核心足 → mid");
  assert.strictEqual(M.detectCapability({ totalVramMB: 0, totalMemMB: 8192, cpuCount: 4 }).tier, "low", "低配 → low");
  const c = M.detectCapability({ totalVramMB: 2048 });
  assert.strictEqual(c.hasGpu, true, "有显存即判定有 GPU");
}
ok("硬件能力分级：high/mid/low + GPU 判定");

// 2. 本地/云端决策（对标 device_match）
{
  const big = { name: "llm-13b", minVramMB: 16384, minMemMB: 0 };
  const ocr = { name: "dbnet", minVramMB: 0, minMemMB: 2048 };
  const cap4070 = M.detectCapability({ totalVramMB: 12288, hasGpu: true, totalMemMB: 32768, cpuCount: 16 });
  // 大模型需 16G 显存，4070S 只有 12G → 云端（正是 Marvis 那个场景）
  const d1 = M.decideExecution(big, cap4070);
  assert.strictEqual(d1.target, "cloud");
  assert.ok(/显存不足/.test(d1.reason));
  // OCR 小模型只需内存 → 本地
  assert.strictEqual(M.decideExecution(ocr, cap4070).target, "local");
  // 无独显跑需要显存的模型 → 云端
  const capNoGpu = M.detectCapability({ totalVramMB: 0, totalMemMB: 16384, cpuCount: 8 });
  assert.strictEqual(M.decideExecution(big, capNoGpu).target, "cloud");
  assert.ok(/无独显/.test(M.decideExecution(big, capNoGpu).reason));
  // 内存不足
  const capLow = M.detectCapability({ totalMemMB: 1024, cpuCount: 2 });
  assert.strictEqual(M.decideExecution(ocr, capLow).target, "cloud");
  // 未知模型
  assert.strictEqual(M.decideExecution(null, cap4070).target, "cloud");
}
ok("本地/云端决策：显存不足/无独显/内存不足→云端，满足→本地");

// 3. 模型注册表 + 默认 OCR
{
  const reg = new M.ModelRegistry();
  M.registerDefaultOcr(reg);
  assert.ok(reg.has("dbnet") && reg.has("crnn"), "默认 OCR 模型已注册");
  assert.strictEqual(reg.get("dbnet").file, "dbnet.onnx");
  assert.strictEqual(reg.get("dbnet").type, "ocr");
  assert.strictEqual(reg.size(), 2);
  reg.register("myllm", { type: "llm", minVramMB: 8192 });
  assert.strictEqual(reg.get("myllm").file, "myllm.onnx", "未给 file 用默认名");
  assert.strictEqual(reg.list().length, 3);
}
ok("模型注册表：默认 OCR + 自定义注册 + 默认文件名");

// 4. 模型文件就绪检查
{
  const reg = new M.ModelRegistry(); M.registerDefaultOcr(reg);
  const dir = M.modelsDir("C:\\Apps\\HashMM\\resources");
  assert.ok(dir.endsWith("models"), "模型目录在 resources/models");
  const present = new Set([require("path").join(dir, "dbnet.onnx")]);
  assert.strictEqual(M.modelFileReady(dir, reg.get("dbnet"), (p) => present.has(p)), true, "存在→就绪");
  assert.strictEqual(M.modelFileReady(dir, reg.get("crnn"), (p) => present.has(p)), false, "缺失→未就绪");
}
ok("模型文件就绪检查：存在/缺失");

console.log(`\ntest_model_manager: ${pass} 项全部通过`);
