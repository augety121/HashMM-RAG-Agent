/**
 * desktop/models/image-ops.js — 纯 JS 图像操作（V101，CRNN 识别段预处理）。
 *
 * 用**纯 JS 实现裁剪/缩放/灰度**，不引带原生二进制的图像库（如 sharp）——契合"不污染系统
 * 环境"的隔离约束，app 完全自包含。供 CRNN 识别：检测框 → 裁剪 → 缩放到 (32,W) → 灰度归一化。
 *
 * 像素布局：RGB 扁平数组（length=w*h*3，0..255）。全部纯函数，可单测。
 */
"use strict";

/** 裁剪矩形区域 → 新 RGB 像素数组。越界自动夹取到图像内。 */
function cropBox(pixels, width, height, box) {
  const x0 = Math.max(0, Math.floor(box.x));
  const y0 = Math.max(0, Math.floor(box.y));
  const x1 = Math.min(width, Math.ceil(box.x + box.w));
  const y1 = Math.min(height, Math.ceil(box.y + box.h));
  const cw = Math.max(0, x1 - x0), ch = Math.max(0, y1 - y0);
  const out = new Uint8Array(cw * ch * 3);
  for (let y = 0; y < ch; y++) {
    for (let x = 0; x < cw; x++) {
      const si = ((y0 + y) * width + (x0 + x)) * 3;
      const di = (y * cw + x) * 3;
      out[di] = pixels[si]; out[di + 1] = pixels[si + 1]; out[di + 2] = pixels[si + 2];
    }
  }
  return { pixels: out, width: cw, height: ch };
}

/**
 * 双线性缩放到 (dstW, dstH) → 新 RGB 像素数组。源/目标空尺寸时安全返回空。
 */
function resizeBilinear(pixels, width, height, dstW, dstH) {
  dstW = Math.max(0, Math.floor(dstW)); dstH = Math.max(0, Math.floor(dstH));
  const out = new Uint8Array(dstW * dstH * 3);
  if (!width || !height || !dstW || !dstH) return { pixels: out, width: dstW, height: dstH };
  const sx = width / dstW, sy = height / dstH;
  for (let dy = 0; dy < dstH; dy++) {
    const fy = (dy + 0.5) * sy - 0.5;
    const y0 = Math.max(0, Math.floor(fy)), y1 = Math.min(height - 1, y0 + 1);
    const wy = fy - y0;
    for (let dx = 0; dx < dstW; dx++) {
      const fx = (dx + 0.5) * sx - 0.5;
      const x0 = Math.max(0, Math.floor(fx)), x1 = Math.min(width - 1, x0 + 1);
      const wx = fx - x0;
      const di = (dy * dstW + dx) * 3;
      for (let c = 0; c < 3; c++) {
        const p00 = pixels[(y0 * width + x0) * 3 + c];
        const p01 = pixels[(y0 * width + x1) * 3 + c];
        const p10 = pixels[(y1 * width + x0) * 3 + c];
        const p11 = pixels[(y1 * width + x1) * 3 + c];
        const top = p00 + (p01 - p00) * wx;
        const bot = p10 + (p11 - p10) * wx;
        out[di + c] = Math.round(top + (bot - top) * wy);
      }
    }
  }
  return { pixels: out, width: dstW, height: dstH };
}

/** RGB → 灰度（Rec.601 加权），返回长度 w*h 的 Uint8Array。 */
function toGrayscale(pixels, width, height) {
  const out = new Uint8Array(width * height);
  for (let i = 0; i < width * height; i++) {
    const r = pixels[i * 3], g = pixels[i * 3 + 1], b = pixels[i * 3 + 2];
    out[i] = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
  }
  return out;
}

/**
 * CRNN 识别预处理：裁剪框 → 等高 32 等比缩放（宽随比例，至少 1）→ 灰度 → 归一化到 [-1,1]。
 * 返回 { data:Float32Array(1*1*32*W), width:W, height:32 }，可直接喂 CRNN（NCHW，单通道）。
 */
function preprocessForCrnn(pixels, width, height, box, targetH = 32) {
  const crop = cropBox(pixels, width, height, box);
  if (!crop.width || !crop.height) return { data: new Float32Array(0), width: 0, height: targetH };
  const ratio = crop.width / crop.height;
  const dstW = Math.max(1, Math.round(targetH * ratio));
  const resized = resizeBilinear(crop.pixels, crop.width, crop.height, dstW, targetH);
  const gray = toGrayscale(resized.pixels, dstW, targetH);
  const data = new Float32Array(dstW * targetH);
  for (let i = 0; i < gray.length; i++) data[i] = (gray[i] / 255 - 0.5) / 0.5; // → [-1,1]
  return { data, width: dstW, height: targetH };
}

module.exports = { cropBox, resizeBilinear, toGrayscale, preprocessForCrnn };
