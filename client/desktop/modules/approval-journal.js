/**
 * Durable desktop approval journal.
 *
 * The Electron main process remains the authority for pending operations. This
 * module only persists a bounded, normalized audit trail so a renderer reload,
 * Chat switch, or application restart cannot make an approval silently vanish.
 * Pending entries recovered after a process restart are marked interrupted;
 * they can never be approved because the originating operation no longer
 * exists.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const Prompt = require("./desktop-prompt");

const SCHEMA = "hashmm.desktop-approval.v1";
const STATUSES = new Set(["pending", "resolved", "interrupted"]);

function _time(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : fallback;
}

function _record(input, now) {
  const src = input && typeof input === "object" ? input : {};
  const prompt = Prompt.normalizePrompt(src.prompt || src);
  const status = STATUSES.has(src.status) ? src.status : "pending";
  const requestedAt = _time(src.requestedAt || prompt.requestedAt, now);
  const resolvedAt = status === "pending" ? 0 : _time(src.resolvedAt, now);
  return {
    schema: SCHEMA,
    id: prompt.id,
    status,
    prompt,
    requestedAt,
    resolvedAt,
    decision: status === "pending" ? "" : Prompt.normalizeDecision(prompt, src.decision),
    reason: String(src.reason || "").replace(/\0/g, "").trim().slice(0, 96),
  };
}

class ApprovalJournal {
  constructor(file, options = {}) {
    this.file = String(file || "");
    this.limit = Math.max(20, Math.min(1000, Number(options.limit) || 300));
    this.now = typeof options.now === "function" ? options.now : Date.now;
    this.items = [];
    this._load();
  }

  _load() {
    if (!this.file) return;
    try {
      const parsed = JSON.parse(fs.readFileSync(this.file, "utf8"));
      const rows = Array.isArray(parsed) ? parsed : parsed && Array.isArray(parsed.items) ? parsed.items : [];
      const now = this.now();
      this.items = rows.map(row => _record(row, now)).filter(row => row.id).slice(-this.limit);
    } catch (_error) {
      this.items = [];
    }
  }

  _save() {
    if (!this.file) return false;
    const dir = path.dirname(this.file);
    const tmp = `${this.file}.tmp-${process.pid}`;
    try {
      fs.mkdirSync(dir, { recursive: true });
      const body = JSON.stringify({ schema: SCHEMA, items: this.items.slice(-this.limit) }, null, 2);
      fs.writeFileSync(tmp, body, { encoding: "utf8", mode: 0o600 });
      fs.renameSync(tmp, this.file);
      return true;
    } catch (_error) {
      try { fs.unlinkSync(tmp); } catch (_e) { /* */ }
      return false;
    }
  }

  request(prompt) {
    const now = this.now();
    const normalized = Prompt.normalizePrompt(prompt);
    if (!normalized.id) return null;
    const next = _record({ prompt: normalized, status: "pending", requestedAt: normalized.requestedAt || now }, now);
    const index = this.items.findIndex(row => row.id === next.id);
    if (index >= 0) {
      if (this.items[index].status !== "pending") return this.items[index];
      next.requestedAt = this.items[index].requestedAt;
      this.items[index] = next;
    } else {
      this.items.push(next);
    }
    this.items = this.items.slice(-this.limit);
    this._save();
    return next;
  }

  resolve(id, decision, reason) {
    const key = String(id || "");
    const index = this.items.findIndex(row => row.id === key);
    if (index < 0 || this.items[index].status !== "pending") return null;
    const current = this.items[index];
    const next = _record({
      ...current,
      status: "resolved",
      decision: Prompt.normalizeDecision(current.prompt, decision),
      reason: reason || "user",
      resolvedAt: this.now(),
    }, this.now());
    this.items[index] = next;
    this._save();
    return next;
  }

  interruptPending(reason = "app-restarted") {
    const now = this.now();
    let changed = false;
    this.items = this.items.map(current => {
      if (current.status !== "pending") return current;
      changed = true;
      return _record({
        ...current,
        status: "interrupted",
        decision: current.prompt.cancelId,
        reason,
        resolvedAt: now,
      }, now);
    });
    if (changed) this._save();
    return changed;
  }

  list(options = {}) {
    const taskId = String(options.taskId || "").trim();
    const status = String(options.status || "").trim();
    const limit = Math.max(1, Math.min(this.limit, Number(options.limit) || 100));
    return this.items
      .filter(row => !taskId || row.prompt.taskId === taskId)
      .filter(row => !status || row.status === status)
      .slice()
      .sort((a, b) => b.requestedAt - a.requestedAt)
      .slice(0, limit)
      .map(row => JSON.parse(JSON.stringify(row)));
  }
}

module.exports = { ApprovalJournal, SCHEMA, STATUSES };
