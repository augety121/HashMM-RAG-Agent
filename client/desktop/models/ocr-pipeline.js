/**
 * desktop/models/ocr-pipeline.js — 本地 OCR 流水线算法（V101）。
 *
 * 真实 DBNet（文本检测）+ CRNN（文本识别）两段式 OCR 的**确定性算法部分**，纯函数可测：
 *   - 预处理：像素 → 归一化 CHW 张量（减均值除方差）
 *   - DBNet 后处理：概率图 → 二值化 → 连通域 → 文本框
 *   - CRNN 后处理：逐帧字符 logits → CTC 贪心解码（argmax→合并连续重复→去 blank→映射字符集）
 *
 * 真正的张量推理在 onnx-runtime.js（onnxruntime-node，沙箱已实测可跑）。这里是把模型
 * 原始输出变成可用结果的关键算法，与模型文件解耦，可在 CI 用合成张量验证正确性。
 */
"use strict";

/**
 * 图像归一化为 CHW float 张量。pixels 为 RGB 扁平数组（长度 w*h*3，0..255）。
 * mean/std 为长度 3 的通道均值/方差。返回 Float32Array（C,H,W 顺序）。
 */
function normalizeToCHW(pixels, w, h, mean = [0.485, 0.456, 0.406], std = [0.229, 0.224, 0.225]) {
  const out = new Float32Array(3 * w * h);
  const plane = w * h;
  for (let i = 0; i < plane; i++) {
    for (let c = 0; c < 3; c++) {
      const v = pixels[i * 3 + c] / 255;
      out[c * plane + i] = (v - mean[c]) / std[c];
    }
  }
  return out;
}

/** 概率图二值化：prob[i] >= thresh → 1，否则 0。返回 Uint8Array。 */
function binarize(probMap, thresh = 0.3) {
  const out = new Uint8Array(probMap.length);
  for (let i = 0; i < probMap.length; i++) out[i] = probMap[i] >= thresh ? 1 : 0;
  return out;
}

/**
 * 连通域取框（4 邻域 BFS）：二值图 → 各连通块的外接矩形。
 * 过滤面积 < minArea 的噪点。返回 [{x, y, w, h, area}]，按从上到下、从左到右排序。
 */
function extractBoxes(bin, width, height, minArea = 3) {
  const visited = new Uint8Array(bin.length);
  const boxes = [];
  const idx = (x, y) => y * width + x;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const start = idx(x, y);
      if (!bin[start] || visited[start]) continue;
      // BFS 一个连通块
      let minX = x, maxX = x, minY = y, maxY = y, area = 0;
      const queue = [[x, y]]; visited[start] = 1;
      while (queue.length) {
        const [cx, cy] = queue.pop();
        area++;
        if (cx < minX) minX = cx; if (cx > maxX) maxX = cx;
        if (cy < minY) minY = cy; if (cy > maxY) maxY = cy;
        const neigh = [[cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]];
        for (const [nx, ny] of neigh) {
          if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
          const ni = idx(nx, ny);
          if (bin[ni] && !visited[ni]) { visited[ni] = 1; queue.push([nx, ny]); }
        }
      }
      if (area >= minArea) boxes.push({ x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1, area });
    }
  }
  boxes.sort((a, b) => (a.y - b.y) || (a.x - b.x));
  return boxes;
}

/** argmax：返回数组中最大值的下标。 */
function argmax(arr) {
  let mi = 0, mv = arr[0];
  for (let i = 1; i < arr.length; i++) if (arr[i] > mv) { mv = arr[i]; mi = i; }
  return mi;
}

/**
 * CTC 贪心解码：把逐帧字符概率解成字符串。
 * @param {number[][]} logitsByT  T×C，每帧 C 个类别的分数（C 含 blank）
 * @param {string[]} charset      非 blank 字符表（下标 0 对应类别 1，类别 0 为 blank）
 *        约定：类别 0 = blank；类别 k(>=1) → charset[k-1]
 * @returns {string}
 */
function ctcGreedyDecode(logitsByT, charset, blankIndex = 0) {
  const ids = logitsByT.map((row) => argmax(row));
  let prev = -1;
  let out = "";
  for (const id of ids) {
    if (id !== prev && id !== blankIndex) {
      const ci = id - (blankIndex === 0 ? 1 : 0); // blank 在 0 时，字符下标后移 1
      if (ci >= 0 && ci < charset.length) out += charset[ci];
    }
    prev = id;
  }
  return out;
}

/** 把检测框按行分组（y 中心相近视为同一行），便于阅读顺序拼接。 */
function groupByRows(boxes, rowTol = 8) {
  const rows = [];
  for (const b of boxes) {
    const cy = b.y + b.h / 2;
    let row = rows.find((r) => Math.abs(r.cy - cy) <= rowTol);
    if (!row) { row = { cy, items: [] }; rows.push(row); }
    row.items.push(b);
  }
  rows.sort((a, b) => a.cy - b.cy);
  for (const r of rows) r.items.sort((a, b) => a.x - b.x);
  return rows.map((r) => r.items);
}

module.exports = { normalizeToCHW, binarize, extractBoxes, argmax, ctcGreedyDecode, groupByRows };
