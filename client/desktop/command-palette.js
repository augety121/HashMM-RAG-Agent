/**
 * desktop/command-palette.js — 命令面板纯逻辑（V103.90）。
 *
 * 快捷键体系的核心是一个命令面板（Ctrl/⌘+K 唤起、输入即筛、回车执行），免去记一堆快捷键。
 * 本模块提供模糊匹配评分与命令过滤排序（子序列匹配 + 连续/词首加权），不碰 DOM，便于沙箱单测。
 */
"use strict";

/**
 * 模糊匹配评分：query 是否为 text 的子序列，给出匹配度（越大越好；-1 不匹配）。
 * 规则：字符按序命中得分；连续命中、词首命中额外加权；大小写不敏感。
 */
function matchScore(text, query) {
  const t = String(text || "").toLowerCase();
  const q = String(query || "").toLowerCase().trim();
  if (!q) return 0;                 // 空查询：全命中（保持原序）
  if (!t) return -1;
  let ti = 0, score = 0, streak = 0;
  for (let qi = 0; qi < q.length; qi++) {
    const ch = q[qi];
    let found = -1;
    for (let k = ti; k < t.length; k++) { if (t[k] === ch) { found = k; break; } }
    if (found === -1) return -1;    // 有字符没命中 → 不匹配
    let add = 1;
    if (found === ti) { streak += 1; add += streak; } else { streak = 0; }   // 连续命中加权
    if (found === 0 || /[\s/_-]/.test(t[found - 1] || "")) add += 2;          // 词首命中加权
    score += add;
    ti = found + 1;
  }
  // 越短的目标、越靠前的命中更优（轻微偏好）
  return score + Math.max(0, 5 - (t.length - q.length) * 0.05);
}

/**
 * 过滤并排序命令。命令对象需含 { id, title }，可选 { keywords:[], section, shortcut, when }。
 * 匹配范围：title + keywords。query 为空时返回全部（保持传入顺序，但过滤掉 when()===false）。
 * @returns 命中的命令数组（按分数降序），每项附 _score。
 */
function filterCommands(commands, query, ctx) {
  const list = Array.isArray(commands) ? commands : [];
  const q = String(query || "").trim();
  const usable = list.filter(c => c && (typeof c.when !== "function" || c.when(ctx)));
  if (!q) return usable.map(c => Object.assign({ _score: 0 }, c));
  const scored = [];
  for (const c of usable) {
    const hay = [c.title || "", ...(Array.isArray(c.keywords) ? c.keywords : [])];
    let best = -1;
    for (const h of hay) { const s = matchScore(h, q); if (s > best) best = s; }
    if (best >= 0) scored.push(Object.assign({ _score: best }, c));
  }
  scored.sort((a, b) => b._score - a._score);
  return scored;
}

/** 列表上下移动选中索引（带环绕）。 */
function moveSelection(current, delta, length) {
  if (!length) return 0;
  const n = ((Number(current) || 0) + (Number(delta) || 0)) % length;
  return n < 0 ? n + length : n;
}

module.exports = { matchScore, filterCommands, moveSelection };
