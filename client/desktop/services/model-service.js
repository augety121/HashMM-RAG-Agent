/**
 * desktop/services/model-service.js — 模型服务（V101，服务层）。
 *
 * 把 models/model-manager 的纯逻辑包成一个服务（注册进 ServiceRegistry）：对外提供
 * 能力检测、模型可用性、本地/云端推荐（对标 Marvis 的 device_match 调用点）。
 * 真正的 onnx 推理需 onnxruntime + 模型文件（真机）；此服务到"决策 + 就绪查询"。
 */
"use strict";
const fs = require("fs");
const path = require("path");
const MM = require("../models/model-manager");
const RT = require("../models/onnx-runtime");
const OCR = require("../models/ocr-pipeline");
const IMG = require("../models/image-ops");

class ModelService {
  /** @param {object} opts { modelsBasePath, hardware, logger, charset } */
  constructor(opts = {}) {
    this.name = "model";
    this.modelsBasePath = opts.modelsBasePath || "";
    this.log = opts.logger || { info() {}, warn() {} };
    this.registry = MM.registerDefaultOcr(new MM.ModelRegistry());
    this.capability = MM.detectCapability(opts.hardware || {});
    this.charset = opts.charset || null;   // CRNN 字符集（真机随模型提供）
    this._det = null; this._rec = null;    // 缓存的 onnx 会话
  }

  /** 服务生命周期：记录能力档。 */
  async start() { this.log.info("model-service 启动", { tier: this.capability.tier, onnx: RT.isAvailable() }); }

  /** 刷新硬件能力（如运行时探测到 GPU 信息）。 */
  setHardware(hw) { this.capability = MM.detectCapability(hw || {}); return this.capability; }

  /** 列出模型 + 是否就绪 + 本地/云端推荐。 */
  available() {
    const dir = MM.modelsDir(this.modelsBasePath);
    return this.registry.list().map((m) => ({
      name: m.name, type: m.type, file: m.file,
      ready: MM.modelFileReady(dir, m, (p) => { try { return fs.existsSync(p); } catch (_) { return false; } }),
      decision: MM.decideExecution(m, this.capability),
    }));
  }

  /** 对某模型给出本地/云端推荐（device_match 等价物）。 */
  recommend(modelName) {
    const m = this.registry.get(modelName);
    return MM.decideExecution(m, this.capability);
  }

  getCapability() { return this.capability; }

  /** 本地 OCR 是否可用：onnxruntime-node 在 + dbnet/crnn 模型文件都就绪。 */
  ocrReady() {
    if (!RT.isAvailable()) return false;
    const dir = MM.modelsDir(this.modelsBasePath);
    const ex = (p) => { try { return fs.existsSync(p); } catch (_) { return false; } };
    return MM.modelFileReady(dir, this.registry.get("dbnet"), ex) &&
           MM.modelFileReady(dir, this.registry.get("crnn"), ex);
  }

  /**
   * 本地 OCR 端到端编排（检测 dbnet → 取框 → 逐框识别 crnn → 拼文本）。
   * input: { pixels, width, height }（RGB 扁平像素，由调用方解码图片得到）。
   * 不就绪（无 ort / 缺模型 / 无字符集）→ { ok:false, fallback:"cloud", reason }，上层走云端。
   * 真机放入 dbnet.onnx/crnn_lite_lstm.onnx + 字符集后即本地运行；沙箱无大模型 → 降级。
   */
  async runOcr(input) {
    const injected = !!(this._det && this._rec);          // 测试可预注入会话
    if (!injected && !this.ocrReady()) return { ok: false, fallback: "cloud", reason: "本地 OCR 未就绪（缺 onnxruntime-node 或模型文件）" };
    if (!this.charset) return { ok: false, fallback: "cloud", reason: "缺 CRNN 字符集" };
    const { pixels, width, height } = input || {};
    if (!pixels || !width || !height) return { ok: false, reason: "缺图像像素数据" };
    try {
      const dir = MM.modelsDir(this.modelsBasePath);
      if (!this._det) this._det = await RT.loadSession(path.join(dir, this.registry.get("dbnet").file));
      if (!this._rec) this._rec = await RT.loadSession(path.join(dir, this.registry.get("crnn").file));
      if (!this._det || !this._rec) return { ok: false, fallback: "cloud", reason: "模型会话加载失败" };

      // 1) 检测：归一化 → dbnet → 概率图 → 二值化 → 连通域取框
      const inT = RT.makeTensor("float32", OCR.normalizeToCHW(pixels, width, height), [1, 3, height, width]);
      const detOut = await RT.run(this._det, { x: inT });
      const probMap = detOut && (detOut.sigmoid || detOut.out || Object.values(detOut)[0]);
      if (!probMap) return { ok: false, fallback: "cloud", reason: "检测无输出" };
      const bin = OCR.binarize(probMap.data, 0.3);
      const boxes = OCR.extractBoxes(bin, width, height, 8);

      // 2) 识别：逐框裁剪→等高32等比缩放→灰度归一化（纯 JS，已跑通）→ CRNN → CTC 解码
      const rows = OCR.groupByRows(boxes);
      const lines = [];
      for (const row of rows) {
        const parts = [];
        for (const box of row) {
          const pre = IMG.preprocessForCrnn(pixels, width, height, box, 32);
          if (!pre.width) continue;
          const recT = RT.makeTensor("float32", pre.data, [1, 1, pre.height, pre.width]);
          const recOut = await RT.run(this._rec, { x: recT });
          const logits = recOut && (recOut.out || Object.values(recOut)[0]);
          if (!logits) continue;
          // logits 形状约定 [1, T, C] → 还原成 T×C 喂 CTC
          const T = logits.dims[logits.dims.length - 2];
          const C = logits.dims[logits.dims.length - 1];
          const byT = [];
          for (let t = 0; t < T; t++) byT.push(Array.from(logits.data.subarray(t * C, t * C + C)));
          parts.push(OCR.ctcGreedyDecode(byT, this.charset));
        }
        if (parts.length) lines.push(parts.join(" "));
      }
      return { ok: true, boxes, rows, text: lines.join("\n") };
    } catch (e) {
      return { ok: false, fallback: "cloud", reason: "OCR 异常：" + ((e && e.message) || e) };
    }
  }
}

module.exports = { ModelService };
