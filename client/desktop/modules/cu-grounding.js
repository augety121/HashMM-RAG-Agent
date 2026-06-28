/**
 * desktop/modules/cu-grounding.js — Computer Use 视觉元素定位 · 纯逻辑核心（V100）。
 *
 * 解决 Computer Use 二期的真痛点：让模型"点提交按钮"时不再靠**肉眼估坐标**
 * （视觉模型给的像素常偏、按钮一小就点空），而是 OCR 抓出屏幕上所有文字元素
 * （`{text, x1,y1,x2,y2}` 像素框）后，用本模块按**文本相似度**把目标定位到
 * 具体元素，返回其中心的**归一化坐标（0..1000）**——直接喂进 cu-actions 的
 * denormalize/validateAction/禁区流水线，定位出来的点击同样受安全策略约束。
 *
 * 对标业界 GUI grounding（OmniParser / set-of-marks）的轻量工程化：
 *   OCR（真机由 cu-driver 调系统/视觉服务做）→ 候选文本框
 *   本模块：归一化文本 → 分层打分（精确>前缀>包含>词重叠>编辑距离）→ 排序
 *          → 最佳元素中心（归一化）+ 置信度 + 备选列表
 *
 * 纯逻辑：不 require electron、不跑 OCR、不碰屏幕。OCR 结果由调用方注入，
 * test 直接喂合成文本框断言定位正确性。真机 OCR 接线见 cu-driver / cu.ts。
 */
"use strict";

const NORM = 1000; // 与 cu-actions 同一归一化空间（0..1000）

/** 文本规范化：小写、折叠空白、去常见标点（含中文标点）与外围装饰符。 */
function normalizeLabel(s) {
  return String(s == null ? "" : s)
    .toLowerCase()
    .replace(/[【】「」『』《》()（）\[\]{}<>"'`*_:：,，.。!！?？、;；…]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** 受限编辑距离（Levenshtein），超过 cap 早停返回 cap+1（省算力）。 */
function editDistance(a, b, cap) {
  a = String(a); b = String(b);
  cap = cap == null ? Math.max(a.length, b.length) : cap;
  if (Math.abs(a.length - b.length) > cap) return cap + 1;
  if (a === b) return 0;
  if (!a.length) return Math.min(b.length, cap + 1);
  if (!b.length) return Math.min(a.length, cap + 1);
  let prev = new Array(b.length + 1);
  let cur = new Array(b.length + 1);
  for (let j = 0; j <= b.length; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    cur[0] = i;
    let rowMin = cur[0];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (cur[j] < rowMin) rowMin = cur[j];
    }
    if (rowMin > cap) return cap + 1;
    const t = prev; prev = cur; cur = t;
  }
  return prev[b.length];
}

/** 词集合（用于词重叠打分）。 */
function _tokens(s) {
  return normalizeLabel(s).split(" ").filter(Boolean);
}

/**
 * 单个候选文本对目标 query 的匹配分（0..1）+ 匹配方式。分层语义：
 *   1.0  精确相等
 *   0.9  候选以 query 开头 / query 以候选开头（按钮文字常被 OCR 带零碎尾巴）
 *   0.8  query 是候选子串（"提交" ⊂ "提交订单"）
 *   0.6..0.8  词重叠（Jaccard 缩放）
 *   0..0.6  编辑距离相似度（1 - d/maxLen），仅当较接近才给分
 * 返回 {score, how}。
 */
function scoreMatch(query, candidate) {
  const q = normalizeLabel(query);
  const c = normalizeLabel(candidate);
  if (!q || !c) return { score: 0, how: "empty" };
  if (q === c) return { score: 1, how: "exact" };
  if (c.startsWith(q) || q.startsWith(c)) {
    // 长度越接近越可信，最低 0.82
    const ratio = Math.min(q.length, c.length) / Math.max(q.length, c.length);
    return { score: 0.82 + 0.08 * ratio, how: "prefix" };
  }
  if (c.includes(q)) {
    const ratio = q.length / c.length;
    return { score: 0.7 + 0.1 * ratio, how: "contains" };
  }
  // 词重叠
  const qt = new Set(_tokens(q));
  const ct = new Set(_tokens(c));
  if (qt.size && ct.size) {
    let inter = 0;
    for (const t of qt) if (ct.has(t)) inter++;
    if (inter > 0) {
      const jac = inter / (qt.size + ct.size - inter);
      if (jac >= 0.5) return { score: 0.6 + 0.2 * jac, how: "tokens" };
    }
  }
  // 编辑距离（仅近似时给分）
  const maxLen = Math.max(q.length, c.length);
  const d = editDistance(q, c, Math.ceil(maxLen * 0.5));
  const sim = 1 - d / maxLen;
  if (sim >= 0.6) return { score: 0.45 + 0.15 * (sim - 0.6) / 0.4, how: "fuzzy" };
  return { score: 0, how: "none" };
}

/** 像素框 → 归一化中心坐标（0..1000）。 */
function centerOf(box, screen) {
  const w = Math.max(1, (screen && screen.width) || 1);
  const h = Math.max(1, (screen && screen.height) || 1);
  const cx = (Number(box.x1) + Number(box.x2)) / 2;
  const cy = (Number(box.y1) + Number(box.y2)) / 2;
  const clamp = (v) => (v < 0 ? 0 : v > NORM ? NORM : Math.round(v));
  return { nx: clamp((cx / w) * NORM), ny: clamp((cy / h) * NORM) };
}

/**
 * 对一批 OCR 元素就 query 打分排序（降序）。
 * @param {Array<{text:string,x1:number,y1:number,x2:number,y2:number}>} elements
 * @returns {Array<{text,box,score,how,nx,ny}>}
 */
function rankElements(elements, query, screen) {
  const list = Array.isArray(elements) ? elements : [];
  return list
    .map((el) => {
      const { score, how } = scoreMatch(query, el.text);
      const { nx, ny } = centerOf(el, screen);
      return { text: el.text, box: { x1: el.x1, y1: el.y1, x2: el.x2, y2: el.y2 }, score, how, nx, ny };
    })
    .filter((r) => r.score > 0)
    .sort((a, b) => b.score - a.score);
}

/**
 * 定位目标元素。
 * @returns {{
 *   found:boolean, nx?:number, ny?:number, score?:number, how?:string,
 *   text?:string, box?:object, ambiguous?:boolean, alternatives:Array, reason?:string
 * }}
 * - minScore 默认 0.55：低于此判未找到（避免乱点）。
 * - ambiguous：top1 与 top2 分差很小（<0.08）且都过阈 → 提示有歧义，让上层确认。
 */
function locateElement(elements, query, screen, opts) {
  opts = opts || {};
  const minScore = opts.minScore == null ? 0.55 : opts.minScore;
  const ranked = rankElements(elements, query, screen);
  const alternatives = ranked.slice(0, 5).map((r) => ({ text: r.text, score: Number(r.score.toFixed(3)), nx: r.nx, ny: r.ny }));
  if (!ranked.length || ranked[0].score < minScore) {
    return { found: false, alternatives, reason: ranked.length ? `最佳候选「${ranked[0].text}」相似度 ${ranked[0].score.toFixed(2)} 低于阈值 ${minScore}` : "屏幕上未识别到任何匹配文字" };
  }
  const top = ranked[0];
  const ambiguous = ranked.length > 1 && top.score - ranked[1].score < 0.08 && ranked[1].score >= minScore;
  return {
    found: true,
    nx: top.nx, ny: top.ny,
    score: Number(top.score.toFixed(3)), how: top.how,
    text: top.text, box: top.box,
    ambiguous, alternatives,
  };
}

module.exports = {
  NORM,
  normalizeLabel,
  editDistance,
  scoreMatch,
  centerOf,
  rankElements,
  locateElement,
};
