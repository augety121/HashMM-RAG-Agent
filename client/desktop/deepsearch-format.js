/**
 * desktop/deepsearch-format.js — 深度检索结果结构化展示纯逻辑（V103.90）。
 *
 * 深度检索（Self-RAG：多跳检索 + 自评 + 忠实度门控）的结果现在是一坨文本塞进气泡。
 * 大厂的"深度研究"展示会把它拆开：答案里的 [N] 引用高亮、编号来源卡片（文件/页码/片段）可展开、
 * 过程信息（多跳轮数 / 是否通过自评 / 置信度）做成干净的头部。
 *
 * 本模块是展示的纯逻辑核心（从答案抽 [N] 引用、规范化来源编号、组装展示模型），
 * 不碰 DOM，便于沙箱单测。
 */
"use strict";

/** 从答案文本里抽出被引用的 [N] 编号（去重升序）。兼容 [1]、[1,2]、[1, 2] 等写法。 */
function extractCitations(text) {
  const s = String(text || "");
  const nums = new Set();
  for (const m of s.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)) {
    for (const part of m[1].split(",")) {
      const n = parseInt(part.trim(), 10);
      if (Number.isFinite(n) && n > 0) nums.add(n);
    }
  }
  return [...nums].sort((a, b) => a - b);
}

function _clip(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n) + "…" : s; }

/**
 * 规范化来源列表为编号卡片。兼容字段名变体（filename/file、text/snippet/content、page/page_no）。
 * @returns [{ n, file, page, snippet, score }]（1-indexed）
 */
function normalizeSources(sources) {
  const arr = Array.isArray(sources) ? sources : [];
  return arr.map((s, i) => {
    s = s || {};
    const file = s.filename || s.file || s.source || s.path || "未知来源";
    const page = s.page != null ? s.page : (s.page_no != null ? s.page_no : (s.page_number != null ? s.page_number : null));
    const snippet = s.snippet || s.text || s.content || s.chunk || "";
    const score = s.score != null ? s.score : (s.relevance != null ? s.relevance : null);
    return {
      n: i + 1,
      file: String(file),
      page: page != null ? page : null,
      snippet: _clip(snippet, 400),
      score: score != null ? score : null,
    };
  });
}

/**
 * 组装深度检索展示模型（纯函数）。
 * @param data 后端 /api/deepsearch 的 data 字段
 * @returns {
 *   answer, degraded,
 *   meta: { grounded, confidence(0..100|null), rounds, sourceCount, citedCount },
 *   sources: [normalized], citedNumbers: [N],
 *   subQuestions: [str],
 * }
 */
function buildDisplay(data) {
  const d = data || {};
  const answer = String(d.answer || "");
  const sources = normalizeSources(d.sources);
  const cited = extractCitations(answer);
  // 子问题/多跳（字段名容错：sub_questions / subqueries / hops[].query）
  let subQuestions = [];
  if (Array.isArray(d.sub_questions)) subQuestions = d.sub_questions.map(String);
  else if (Array.isArray(d.subqueries)) subQuestions = d.subqueries.map(String);
  else if (Array.isArray(d.hops)) subQuestions = d.hops.map(h => String((h && (h.query || h.question)) || "")).filter(Boolean);

  return {
    answer,
    degraded: !!d.degraded,
    meta: {
      grounded: d.grounded != null ? !!d.grounded : null,
      confidence: (d.confidence != null && isFinite(d.confidence)) ? Math.round(Number(d.confidence) * 100) : null,
      rounds: (d.rounds != null) ? Number(d.rounds) : null,
      sourceCount: sources.length,
      citedCount: cited.length,
    },
    sources,
    citedNumbers: cited,
    subQuestions,
  };
}

/** 一句话过程摘要（供头部展示）。 */
function metaSummary(meta) {
  meta = meta || {};
  const bits = [];
  if (meta.grounded === true) bits.push("已通过自评");
  else if (meta.grounded === false) bits.push("资料可能不足");
  if (meta.confidence != null) bits.push("置信度 " + meta.confidence + "%");
  if (meta.rounds != null && meta.rounds > 0) bits.push("再检索 " + meta.rounds + " 轮");
  bits.push("命中 " + (meta.sourceCount || 0) + " 来源");
  if (meta.citedCount) bits.push("引用 " + meta.citedCount + " 处");
  return bits.join(" · ");
}

module.exports = { extractCitations, normalizeSources, buildDisplay, metaSummary };
