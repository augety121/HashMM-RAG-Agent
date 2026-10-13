/**
 * Native Office hand-off registry.
 *
 * HashMM deliberately does not automate a closed-source office suite through
 * guessed executables or private protocols.  It writes the conversation
 * artifact to disk and asks Windows to open the registered application for
 * that file type.  This works with Microsoft Office, WPS, LibreOffice and
 * vivo Office when the user has installed it and registered the association.
 *
 * The registry keeps the exact file that HashMM opened.  A later read is only
 * allowed through the opaque handoff id, so the renderer does not gain a
 * general arbitrary-file read capability.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const OFFICE_EXTENSIONS = new Set([".docx", ".xlsx", ".pptx"]);
const DEFAULT_MAX_BYTES = 100 * 1024 * 1024;

function safeOfficeName(name) {
  const clean = path.basename(String(name || "")).replace(/[\\/:*?"<>|]/g, "_");
  const ext = path.extname(clean).toLowerCase();
  if (!clean || !OFFICE_EXTENSIONS.has(ext)) {
    throw new Error("只支持 DOCX、XLSX 和 PPTX 办公文件");
  }
  return clean;
}

function mimeFor(filename) {
  const ext = path.extname(String(filename || "")).toLowerCase();
  if (ext === ".docx") return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  if (ext === ".xlsx") return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  if (ext === ".pptx") return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
  return "application/octet-stream";
}

function snapshot(file) {
  const st = fs.statSync(file);
  if (!st.isFile()) throw new Error("办公接力目标不是普通文件");
  return { size: st.size, mtimeMs: Math.trunc(st.mtimeMs) };
}

function sha256Buffer(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

/**
 * Read one stable revision. Office applications often save through a temporary
 * file and rename, so a stat before/after the read is required. Returning the
 * digest of the exact bytes that will be uploaded gives the renderer a receipt
 * it can compare with the server response before acknowledging the revision.
 */
function readStableFile(file, maxBytes = DEFAULT_MAX_BYTES) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const before = snapshot(file);
    if (before.size > maxBytes) throw new Error("办公文件超过同步大小上限");
    const buffer = fs.readFileSync(file);
    const after = snapshot(file);
    if (before.size === after.size && before.mtimeMs === after.mtimeMs && buffer.length === after.size) {
      return {
        buffer,
        fingerprint: { ...after, sha256: sha256Buffer(buffer) },
      };
    }
  }
  throw new Error("办公文件仍在保存，请稍后重试");
}

function fingerprint(file, maxBytes = DEFAULT_MAX_BYTES) {
  return readStableFile(file, maxBytes).fingerprint;
}

/**
 * A small, renderer-safe application harness receipt. This deliberately
 * exposes neither a filesystem path nor an arbitrary command channel. It
 * turns the existing Office hand-off into a deterministic application session:
 * open -> observe revision -> read exact bytes -> commit the same digest.
 *
 * The contract follows the useful boundary from harness-anything (stable
 * session, explicit capabilities, machine-readable state) while remaining
 * native to HashMM's permission and audit model.
 */
function harnessReceipt(session, state) {
  return {
    schema: "hashmm.application-harness.v1",
    sessionId: session.id,
    application: "system-office",
    provider: "system-file-association",
    resource: { name: session.filename, kind: path.extname(session.filename).slice(1) },
    state,
    revision: session.revision,
    capabilities: ["open", "observe_revision", "read_revision", "commit_revision"],
    integrity: {
      contentAddressed: true,
      explicitCommit: true,
      arbitraryFileAccess: false,
      arbitraryCommandExecution: false,
    },
  };
}

class OfficeHandoffRegistry {
  constructor(opts) {
    const o = opts || {};
    this.openPath = o.openPath;
    this.maxBytes = Number(o.maxBytes) > 0 ? Number(o.maxBytes) : DEFAULT_MAX_BYTES;
    this.maxSessions = Number(o.maxSessions) > 0 ? Number(o.maxSessions) : 24;
    this.sessions = new Map();
  }

  async open(file) {
    const full = path.resolve(String(file || ""));
    const filename = safeOfficeName(full);
    const before = fingerprint(full, this.maxBytes);
    if (typeof this.openPath !== "function") throw new Error("当前环境没有系统文件关联能力");
    const error = await this.openPath(full);
    if (error) throw new Error(String(error));
    const id = crypto.randomBytes(18).toString("hex");
    this.sessions.set(id, {
      id, file: full, filename, baseline: before, observed: before,
      revision: 1, openedAt: Date.now(),
    });
    while (this.sessions.size > this.maxSessions) this.sessions.delete(this.sessions.keys().next().value);
    return {
      ok: true, id, filename, provider: "system-file-association",
      revision: 1, sha256: before.sha256,
      harness: harnessReceipt(this.sessions.get(id), "active"),
    };
  }

  status(id) {
    const s = this.sessions.get(String(id || ""));
    if (!s) return { ok: false, error: "办公接力已失效，请重新打开文件" };
    try {
      const metadata = snapshot(s.file);
      if (metadata.size > this.maxBytes) return { ok: false, error: "修改后的办公文件超过同步大小上限" };
      if (metadata.size !== s.observed.size || metadata.mtimeMs !== s.observed.mtimeMs) {
        const current = fingerprint(s.file, this.maxBytes);
        if (current.sha256 !== s.observed.sha256) s.revision += 1;
        s.observed = current;
      }
      const changed = s.observed.sha256 !== s.baseline.sha256;
      return {
        ok: true, id: s.id, filename: s.filename, changed,
        revision: s.revision, sha256: s.observed.sha256,
        current: { size: s.observed.size, mtimeMs: s.observed.mtimeMs },
        harness: harnessReceipt(s, changed ? "changed" : "active"),
      };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  }

  read(id) {
    const s = this.sessions.get(String(id || ""));
    if (!s) return { ok: false, error: "办公接力已失效，请重新打开文件" };
    try {
      const stable = readStableFile(s.file, this.maxBytes);
      const current = stable.fingerprint;
      if (current.sha256 !== s.observed.sha256) s.revision += 1;
      s.observed = current;
      return {
        ok: true, id: s.id, filename: s.filename, size: current.size,
        revision: s.revision, sha256: current.sha256,
        dataUrl: `data:${mimeFor(s.filename)};base64,${stable.buffer.toString("base64")}`,
        harness: harnessReceipt(s, "observed"),
      };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  }

  acknowledge(id, expectedSha256) {
    const s = this.sessions.get(String(id || ""));
    if (!s) return { ok: false, error: "办公接力已失效" };
    const expected = String(expectedSha256 || "").toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(expected)) {
      return { ok: false, error: "缺少有效的文件内容校验值" };
    }
    try {
      const current = fingerprint(s.file, this.maxBytes);
      if (current.sha256 !== s.observed.sha256) {
        s.revision += 1;
        s.observed = current;
      }
      if (current.sha256 !== expected) {
        return {
          ok: false, conflict: true, revision: s.revision,
          error: "文件在读取后又发生变化，请重新同步最新修订",
        };
      }
      s.baseline = current;
      return {
        ok: true, revision: s.revision, sha256: current.sha256,
        harness: harnessReceipt(s, "committed"),
      };
    }
    catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
  }
}

module.exports = {
  OFFICE_EXTENSIONS, DEFAULT_MAX_BYTES, safeOfficeName, mimeFor, snapshot,
  sha256Buffer, readStableFile, fingerprint, harnessReceipt, OfficeHandoffRegistry,
};
