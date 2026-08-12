/** Browser action trajectory evidence and evaluator (plain Node/browser logic). */
"use strict";

const MODES = new Set(["exact", "subsequence", "any_order"]);

function actionSequence(events) {
  return (Array.isArray(events) ? events : [])
    .filter((ev) => ev && (ev.phase === "done" || ev.phase === "error") && ev.action)
    .map((ev) => String(ev.action));
}

function _expected(value) {
  const src = Array.isArray(value) ? value : String(value || "").split(/[,，\s]+/);
  return src.map((x) => String(x || "").trim()).filter(Boolean);
}

function evaluateTrajectory(events, expectedValue, modeValue) {
  const actual = actionSequence(events);
  const expected = _expected(expectedValue);
  const mode = MODES.has(modeValue) ? modeValue : "subsequence";
  let pass = false;
  let matched = 0;
  let missing = [];
  if (mode === "exact") {
    pass = actual.length === expected.length && actual.every((x, i) => x === expected[i]);
    matched = actual.reduce((n, x, i) => n + (x === expected[i] ? 1 : 0), 0);
    missing = expected.filter((x, i) => actual[i] !== x);
  } else if (mode === "subsequence") {
    let j = 0;
    for (const action of actual) if (j < expected.length && action === expected[j]) j++;
    matched = j; pass = j === expected.length;
    missing = expected.slice(j);
  } else {
    const counts = new Map();
    for (const action of actual) counts.set(action, (counts.get(action) || 0) + 1);
    for (const action of expected) {
      const n = counts.get(action) || 0;
      if (n > 0) { matched++; counts.set(action, n - 1); }
      else missing.push(action);
    }
    pass = matched === expected.length;
  }
  return {
    pass, mode, expected, actual, matched,
    missing,
    summary: pass ? `通过：${matched}/${expected.length} 个预期动作已匹配` : `未通过：仅匹配 ${matched}/${expected.length} 个预期动作`,
  };
}

function evidenceEvents(events) {
  return (Array.isArray(events) ? events : []).map((ev) => ({
    seq: Number(ev && ev.seq) || 0,
    ts: Number(ev && ev.ts) || 0,
    phase: String(ev && ev.phase || ""),
    action: String(ev && ev.action || ""),
    url: String(ev && ev.url || ""),
    title: String(ev && ev.title || ""),
    index: ev && ev.index != null ? Number(ev.index) : undefined,
    error: String(ev && ev.error || ""),
    element_count: Array.isArray(ev && ev.elements) ? ev.elements.length : 0,
    screenshot_present: !!(ev && (ev.image || ev.screenshot_present)),
  }));
}

const SECRET_QUERY_KEY = /(?:token|key|auth|session|password|passwd|secret|credential|code)/i;

function sanitizeEvidenceUrl(value) {
  try {
    const url = new URL(String(value || ""));
    if (url.protocol !== "http:" && url.protocol !== "https:") return "";
    url.username = "";
    url.password = "";
    url.hash = "";
    for (const key of Array.from(url.searchParams.keys())) {
      if (SECRET_QUERY_KEY.test(key)) url.searchParams.delete(key);
    }
    return url.toString();
  } catch (_e) {
    return "";
  }
}

function _cleanLabel(value, fallback) {
  const text = String(value || "").replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  return (text || fallback || "网页来源").slice(0, 160);
}

/**
 * Convert browser events into the same bounded evidence/trajectory contract
 * used by Chat.  A source is created only for a successful `read` carrying
 * actual page text.  Navigation/click events remain trajectory evidence and
 * cannot masquerade as factual support.
 */
function browserEvidenceBundle(events, afterSeq) {
  const trace = [];
  const sources = [];
  const urlRewrites = [];
  const seen = new Set();
  const floor = Number(afterSeq) || 0;
  const rows = (Array.isArray(events) ? events : []).filter((ev) => ev && Number(ev.seq) > floor);

  for (const ev of rows) {
    if (ev.phase !== "done" && ev.phase !== "error") continue;
    const action = String(ev.action || "browser").slice(0, 48);
    const safeUrl = sanitizeEvidenceUrl(ev.url);
    const title = _cleanLabel(ev.title, safeUrl || "浏览器步骤");
    trace.push({
      id: `browser-${Number(ev.seq) || trace.length + 1}`,
      node: `browser:${action}`,
      tool: "browser",
      detail: String(ev.phase === "error" ? (ev.error || "执行失败") : [title, safeUrl].filter(Boolean).join(" | ")).slice(0, 420),
      status: ev.phase === "error" ? "error" : "done",
    });

    const snippet = String(ev.snippet || "").replace(/\x00/g, "").trim().slice(0, 1200);
    if (ev.phase !== "done" || action !== "read" || !safeUrl || !snippet || seen.has(safeUrl) || sources.length >= 8) continue;
    seen.add(safeUrl);
    const citationId = sources.length + 1;
    sources.push({
      citation_id: citationId,
      source_id: `browser-${Number(ev.seq) || citationId}`,
      filename: title,
      section: safeUrl,
      text: snippet,
      score: 1,
      method: "browser_use",
      modality: "text",
    });
    urlRewrites.push({ raw: String(ev.url || ""), safe: safeUrl, citation_id: citationId });
  }
  return { sources, trace: trace.slice(-40), url_rewrites: urlRewrites };
}

/**
 * Keep only citations tied to an actually-read URL.  Model-authored numeric
 * citations are removed first because prose alone is not execution evidence.
 */
function attachEvidenceCitations(answer, bundle) {
  const data = bundle && typeof bundle === "object" ? bundle : {};
  let text = String(answer || "").replace(/(?<![A-Za-z0-9_\]])\[\d{1,3}\](?!\()/g, "").trim();
  const rewrites = Array.isArray(data.url_rewrites) ? data.url_rewrites : [];
  for (const item of rewrites) {
    const raw = String(item && item.raw || "");
    const safe = String(item && item.safe || "");
    const id = Number(item && item.citation_id) || 0;
    if (!safe || !id) continue;
    if (raw && raw !== safe) text = text.split(raw).join(safe);
    const at = text.indexOf(safe);
    if (at >= 0) text = text.slice(0, at) + safe + ` [${id}]` + text.slice(at + safe.length);
  }
  const sources = Array.isArray(data.sources) ? data.sources : [];
  if (sources.length) {
    const lines = sources.map((source) =>
      `[${source.citation_id}] ${_cleanLabel(source.filename, "网页来源")} - ${String(source.section || "")}`);
    text += `${text ? "\n\n" : ""}浏览证据\n${lines.join("\n")}`;
  }
  return text;
}

function buildDatasetCase(input) {
  const src = input && typeof input === "object" ? input : {};
  const events = evidenceEvents(src.events);
  const evaluation = evaluateTrajectory(events, src.expected, src.mode);
  return {
    schema: "hashmm.browser-trajectory.v1",
    created_at: new Date().toISOString(),
    goal: String(src.goal || ""),
    expected_actions: evaluation.expected,
    match_mode: evaluation.mode,
    actual_actions: evaluation.actual,
    evaluation,
    events,
    result: String(src.result || ""),
  };
}

module.exports = {
  MODES, actionSequence, evaluateTrajectory, evidenceEvents, buildDatasetCase,
  sanitizeEvidenceUrl, browserEvidenceBundle, attachEvidenceCitations,
};
