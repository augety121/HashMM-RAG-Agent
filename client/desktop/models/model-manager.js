/**
 * desktop/models/model-manager.js — 本地模型管理（V101，对标 Marvis 的 models/ + /v3/llm_device_match）。
 *
 * Marvis 的做法：本地放 onnx 模型（OCR 的 dbnet/crnn、可选本地 LLM），用 /v3/llm_device_match
 * 按 GPU 显存等硬件能力判断"能不能跑本地"，不够则回退云端（RTX 4070S 12G < 16G → 云端）。
 * 这里同款落地：硬件能力检测 + 模型注册表（每个模型声明最低显存/内存需求）+ 本地/云端决策。
 *
 * 决策/能力分级是纯函数，可单测；真正的 onnx 推理需 onnxruntime + 模型文件（真机），此处
 * 只到"决策 + 文件就绪检查 + 加载入口"，诚实不假装在沙箱跑推理。
 */
"use strict";
const path = require("path");

const MODELS_DIR = "models"; // 安装目录/资源目录下的模型夹（对标 Marvis models/）

/**
 * 硬件能力检测：把原始硬件信息归一成能力档。
 * @param {object} hw { totalVramMB, cpuCount, totalMemMB, hasGpu }
 * @returns {{ tier:"high"|"mid"|"low", totalVramMB, cpuCount, totalMemMB, hasGpu }}
 */
function detectCapability(hw = {}) {
  const totalVramMB = Number(hw.totalVramMB) || 0;
  const cpuCount = Number(hw.cpuCount) || 0;
  const totalMemMB = Number(hw.totalMemMB) || 0;
  const hasGpu = !!hw.hasGpu || totalVramMB > 0;
  let tier = "low";
  if (hasGpu && totalVramMB >= 16384) tier = "high";        // ≥16G 显存：能跑较大本地模型
  else if (hasGpu && totalVramMB >= 6144) tier = "mid";     // ≥6G：能跑 OCR / 小模型
  else if (totalMemMB >= 16384 && cpuCount >= 8) tier = "mid"; // 无独显但内存/核心足：CPU 跑小模型
  return { tier, totalVramMB, cpuCount, totalMemMB, hasGpu };
}

class ModelRegistry {
  constructor() { this._models = new Map(); }
  /** 注册模型。def: { file, type:"ocr"|"llm"|"embed", minVramMB, minMemMB, runtime } */
  register(name, def) {
    if (!name) throw new Error("模型名必填");
    this._models.set(name, {
      name,
      file: def.file || name + ".onnx",
      type: def.type || "ocr",
      minVramMB: def.minVramMB || 0,
      minMemMB: def.minMemMB || 0,
      runtime: def.runtime || "onnx",
    });
    return this;
  }
  has(name) { return this._models.has(name); }
  get(name) { return this._models.get(name); }
  list() { return Array.from(this._models.values()); }
  size() { return this._models.size; }
}

/**
 * 本地/云端决策（对标 device_match）：硬件满足模型最低需求 → 本地；否则 → 云端。
 * @returns {{ target:"local"|"cloud", reason:string }}
 */
function decideExecution(model, capability) {
  if (!model) return { target: "cloud", reason: "未知模型，回退云端" };
  const cap = capability || {};
  const needV = model.minVramMB || 0;
  const needM = model.minMemMB || 0;
  // 需要显存但没有/不足 → 云端
  if (needV > 0) {
    if (!cap.hasGpu) return { target: "cloud", reason: `模型需 ${needV}MB 显存，本机无独显` };
    if ((cap.totalVramMB || 0) < needV) return { target: "cloud", reason: `显存不足（需 ${needV}MB，有 ${cap.totalVramMB || 0}MB）` };
  }
  if (needM > 0 && (cap.totalMemMB || 0) < needM) {
    return { target: "cloud", reason: `内存不足（需 ${needM}MB，有 ${cap.totalMemMB || 0}MB）` };
  }
  return { target: "local", reason: "硬件满足，使用本地模型" };
}

/** 模型文件是否就绪（存在于 modelsDir）。existsFn 由调用方注入。 */
function modelFileReady(modelsDir, model, existsFn) {
  if (!model) return false;
  return !!existsFn(path.join(modelsDir, model.file));
}

/** 模型目录路径（resourcesPath 下的 models/）。 */
function modelsDir(basePath) { return path.join(String(basePath || ""), MODELS_DIR); }

/** 内置 OCR 模型集（对标 Marvis 的 dbnet/crnn）。需求按 OCR 小模型设较低门槛。 */
function registerDefaultOcr(registry) {
  registry.register("dbnet", { file: "dbnet.onnx", type: "ocr", minVramMB: 0, minMemMB: 2048 });   // 文本检测
  registry.register("crnn", { file: "crnn_lite_lstm.onnx", type: "ocr", minVramMB: 0, minMemMB: 2048 }); // 文本识别
  return registry;
}

module.exports = {
  MODELS_DIR, detectCapability, ModelRegistry, decideExecution,
  modelFileReady, modelsDir, registerDefaultOcr,
};
