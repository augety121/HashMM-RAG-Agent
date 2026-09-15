/**
 * First-party desktop prompt protocol.
 *
 * The main process owns the request and its decision boundary; the renderer is
 * only allowed to render a bounded payload and return one of the advertised
 * decision ids.  Keeping normalization in a pure module makes the security
 * contract independently testable without Electron.
 */
"use strict";

const KINDS = new Set(["privacy", "warning", "danger", "info", "update"]);
const TONES = new Set(["primary", "secondary", "danger"]);

function _text(value, max) {
  return String(value == null ? "" : value).replace(/\0/g, "").trim().slice(0, max);
}

function _button(value, index) {
  const src = value && typeof value === "object" ? value : {};
  const rawId = _text(src.id, 48);
  const id = /^[a-z0-9][a-z0-9:_-]*$/i.test(rawId) ? rawId : `option-${index + 1}`;
  return {
    id,
    label: _text(src.label, 40) || `选项 ${index + 1}`,
    tone: TONES.has(src.tone) ? src.tone : "secondary",
  };
}

function normalizePrompt(input) {
  const src = input && typeof input === "object" ? input : {};
  const buttons = (Array.isArray(src.buttons) ? src.buttons : []).slice(0, 4).map(_button);
  if (!buttons.length) buttons.push({ id: "dismiss", label: "知道了", tone: "primary" });

  // A duplicated decision id makes an approval ambiguous. Keep the first and
  // deterministically rename later duplicates instead of silently widening it.
  const seen = new Set();
  for (let i = 0; i < buttons.length; i++) {
    if (seen.has(buttons[i].id)) buttons[i].id = `option-${i + 1}`;
    seen.add(buttons[i].id);
  }

  const requestedCancel = _text(src.cancelId, 48);
  const cancelId = seen.has(requestedCancel) ? requestedCancel : buttons[buttons.length - 1].id;
  const requestedDefault = _text(src.defaultId, 48);
  const defaultId = seen.has(requestedDefault) ? requestedDefault : cancelId;
  return {
    id: _text(src.id, 96),
    source: _text(src.source, 64) || "desktop",
    taskId: _text(src.taskId, 160) || "session",
    requestedAt: Number.isFinite(Number(src.requestedAt)) ? Math.max(0, Math.floor(Number(src.requestedAt))) : 0,
    kind: KINDS.has(src.kind) ? src.kind : "privacy",
    eyebrow: _text(src.eyebrow, 48) || "隐私保护",
    title: _text(src.title, 180) || "需要你的确认",
    message: _text(src.message, 1200),
    detail: _text(src.detail, 4000),
    target: _text(src.target, 2048),
    boundary: _text(src.boundary, 1200),
    buttons,
    cancelId,
    defaultId,
  };
}

function normalizeDecision(prompt, decision) {
  const p = normalizePrompt(prompt);
  const candidate = _text(decision, 48);
  return p.buttons.some((button) => button.id === candidate) ? candidate : p.cancelId;
}

module.exports = { normalizePrompt, normalizeDecision, KINDS, TONES };
