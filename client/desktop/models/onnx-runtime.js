/**
 * desktop/models/onnx-runtime.js — ONNX 运行时封装（V101，真集成 onnxruntime-node）。
 *
 * 封装 onnxruntime-node 的会话加载与推理。**优雅降级**：onnxruntime-node 未安装、或模型
 * 文件缺失时返回 null/false，绝不抛崩——上层据此回退云端（见 model-manager.decideExecution）。
 *
 * 沙箱已实测：onnxruntime-node 可在进程内真跑 ONNX 推理（见 test_ocr.js 的 tiny.onnx 用例）。
 */
"use strict";
const fs = require("fs");

let _ort; // undefined=未试 / false=不可用 / 模块对象=可用
function getOrt() {
  if (_ort !== undefined) return _ort;
  try { _ort = require("onnxruntime-node"); }
  catch (_) { _ort = false; }
  return _ort;
}

/** onnxruntime-node 是否可用。 */
function isAvailable() { return !!getOrt(); }

/** 构造张量（type 如 "float32"；data 为 TypedArray/Array；dims 形状）。失败返回 null。 */
function makeTensor(type, data, dims) {
  const ort = getOrt();
  if (!ort) return null;
  try { return new ort.Tensor(type, data, dims); } catch (_) { return null; }
}

/**
 * 加载模型会话。模型文件不存在或 ort 不可用 → 返回 null（不抛）。
 * @param {string} modelPath
 * @param {function} existsFn 默认 fs.existsSync
 */
async function loadSession(modelPath, existsFn) {
  const ort = getOrt();
  if (!ort) return null;
  const exists = existsFn || ((p) => { try { return fs.existsSync(p); } catch (_) { return false; } });
  if (!modelPath || !exists(modelPath)) return null;
  try { return await ort.InferenceSession.create(modelPath); }
  catch (_) { return null; }
}

/** 跑一次推理。feeds: { inputName: Tensor }。失败返回 null。 */
async function run(session, feeds) {
  if (!session) return null;
  try { return await session.run(feeds); }
  catch (_) { return null; }
}

module.exports = { isAvailable, makeTensor, loadSession, run, getOrt };
