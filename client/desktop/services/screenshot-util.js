/**
 * desktop/services/screenshot-util.js — 截屏工具纯函数（V101）。
 *
 * 截屏卡顿主因：desktopCapturer 在高分屏按 native×scaleFactor 捕获后 toDataURL() 同步 PNG
 * 编码几百万像素会冻主进程。capCaptureSize 把捕获分辨率封顶（最长边不超 maxDim），常见
 * 1080p/2K 不变，4K/HiDPI 按比例缩小——大幅降编码耗时、标注质量仍足。纯逻辑可测。
 */
"use strict";

const DEFAULT_MAX_DIM = 2880; // 最长边上限：1080p@1.5x / 2K 都不触顶，4K 才缩

/**
 * 计算实际捕获尺寸。
 * @param {number} logicalW 显示器逻辑宽（如 1920）
 * @param {number} logicalH 逻辑高（如 1080）
 * @param {number} scaleFactor 缩放因子（如 1 / 1.5 / 2）
 * @param {number} maxDim 最长边上限（默认 2880）
 * @returns {{ width:number, height:number, scaled:boolean }} 捕获像素尺寸
 */
function capCaptureSize(logicalW, logicalH, scaleFactor = 1, maxDim = DEFAULT_MAX_DIM) {
  const sf = scaleFactor > 0 ? scaleFactor : 1;
  let w = Math.round((logicalW || 0) * sf);
  let h = Math.round((logicalH || 0) * sf);
  if (w <= 0 || h <= 0) return { width: 0, height: 0, scaled: false };
  const longest = Math.max(w, h);
  if (longest <= maxDim) return { width: w, height: h, scaled: false };
  const k = maxDim / longest;
  return { width: Math.round(w * k), height: Math.round(h * k), scaled: true };
}

module.exports = { capCaptureSize, DEFAULT_MAX_DIM };
