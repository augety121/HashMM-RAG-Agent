/**
 * desktop/message-search.js — 会话内消息搜索纯逻辑（V103.90）。
 *
 * 在当前对话里查找关键词、定位到第几条消息、第几个匹配，并给出可高亮的片段。
 * 大小写不敏感、安全转义（高亮 HTML 不引入 XSS）。纯函数、不碰 DOM，便于沙箱单测。
 */
"use strict";

function _text(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.filter(p => p && p.type === "text").map(p => p.text || "").join(" ");
  return "";
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** 在 text 里找 query 的所有出现位置（大小写不敏感），返回 [{start,end}]。 */
function findMatches(text, query) {
  const t = String(text || ""), q = String(query || "");
  if (!q) return [];
  const tl = t.toLowerCase(), ql = q.toLowerCase();
  const out = [];
  let i = 0;
  while (true) {
    const idx = tl.indexOf(ql, i);
    if (idx === -1) break;
    out.push({ start: idx, end: idx + q.length });
    i = idx + q.length;            // 不重叠
  }
  return out;
}

/** 把 text 转义并在匹配处包 <mark>（activeIndex 命中的那处加 class="active"）。 */
function highlightHtml(text, query, activeIndex) {
  const t = String(text || "");
  const matches = findMatches(t, query);
  if (!matches.length) return escapeHtml(t);
  let html = "", last = 0, gi = 0;
  for (const m of matches) {
    html += escapeHtml(t.slice(last, m.start));
    const cls = (gi === activeIndex) ? ' class="search-hit active"' : ' class="search-hit"';
    html += "<mark" + cls + ">" + escapeHtml(t.slice(m.start, m.end)) + "</mark>";
    last = m.end; gi++;
  }
  html += escapeHtml(t.slice(last));
  return html;
}

/** 取匹配周围的片段（用于结果列表预览），命中词居中。 */
function snippet(text, query, radius) {
  const t = String(text || ""); const r = radius || 30;
  const m = findMatches(t, query)[0];
  if (!m) return t.slice(0, r * 2);
  const start = Math.max(0, m.start - r), end = Math.min(t.length, m.end + r);
  return (start > 0 ? "…" : "") + t.slice(start, end) + (end < t.length ? "…" : "");
}

/**
 * 搜索整段对话。返回 { totalMatches, results:[{index, role, count, snippet}], hits:[{msgIndex, matchIndex}] }
 * hits 是所有匹配的扁平列表（供"下一个/上一个"跨消息跳转）。
 */
function searchMessages(messages, query) {
  const arr = Array.isArray(messages) ? messages : [];
  const q = String(query || "").trim();
  const results = [], hits = [];
  if (!q) return { totalMatches: 0, results, hits };
  arr.forEach((m, idx) => {
    if (!m || !m.role) return;
    const text = _text(m.content);
    const ms = findMatches(text, q);
    if (ms.length) {
      results.push({ index: idx, role: m.role, count: ms.length, snippet: snippet(text, q) });
      ms.forEach((_, mi) => hits.push({ msgIndex: idx, matchIndex: mi }));
    }
  });
  return { totalMatches: hits.length, results, hits };
}

/** 下一个/上一个匹配的全局序号（环绕）。 */
function stepHit(current, delta, total) {
  if (!total) return -1;
  const n = ((Number(current) || 0) + (Number(delta) || 0)) % total;
  return n < 0 ? n + total : n;
}

module.exports = { findMatches, highlightHtml, snippet, searchMessages, stepHit, escapeHtml };
