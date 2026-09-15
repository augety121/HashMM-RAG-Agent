/**
 * desktop/shot-annotate.js — 截图标注几何纯逻辑（V103.90）。
 *
 * 截图按钮标题写着"可标注"，但之前截完直接进待发条、没有标注环节。本模块补上标注编辑器的
 * 几何核心：把用户在显示画布上的拖拽（任意方向）归一成矩形，并把显示坐标映射回图片**原始分辨率**
 * 坐标（画布常被 CSS 缩放，直接用显示坐标画上去会错位）。
 *
 * 纯函数、不抛异常，便于沙箱单测。画布绘制/合成由渲染层做。
 */
"use strict";

/** 把一次拖拽的两点归一成 {x,y,w,h}（支持从任意角往任意方向拖）。 */
function normalizeRect(x0, y0, x1, y1) {
  const x = Math.min(x0, x1);
  const y = Math.min(y0, y1);
  const w = Math.abs(x1 - x0);
  const h = Math.abs(y1 - y0);
  return { x, y, w, h };
}

/** 显示坐标 → 图片原始坐标（按显示尺寸与原始尺寸的比例换算）。 */
function scalePoint(dx, dy, displayW, displayH, naturalW, naturalH) {
  const sx = displayW > 0 ? naturalW / displayW : 1;
  const sy = displayH > 0 ? naturalH / displayH : 1;
  return { x: Math.round(dx * sx), y: Math.round(dy * sy) };
}

/** 显示坐标系的矩形 → 图片原始坐标系的矩形。 */
function scaleRect(rect, displayW, displayH, naturalW, naturalH) {
  const r = rect || {};
  const sx = displayW > 0 ? naturalW / displayW : 1;
  const sy = displayH > 0 ? naturalH / displayH : 1;
  return {
    x: Math.round((r.x || 0) * sx),
    y: Math.round((r.y || 0) * sy),
    w: Math.round((r.w || 0) * sx),
    h: Math.round((r.h || 0) * sy),
  };
}

/** 矩形是否"太小"（视为误点，应丢弃）。阈值按显示像素。 */
function isTinyRect(rect, minPx) {
  const r = rect || {}; const m = minPx == null ? 4 : minPx;
  return (r.w || 0) < m || (r.h || 0) < m;
}

/** 把一点夹在画布范围内（拖出边界时收回）。 */
function clampPoint(x, y, w, h) {
  return { x: Math.min(Math.max(0, x), w), y: Math.min(Math.max(0, y), h) };
}

module.exports = { normalizeRect, scalePoint, scaleRect, isTinyRect, clampPoint };
