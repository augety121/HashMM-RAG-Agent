"use strict";

const fs = require("fs");
const path = require("path");

const CACHE_SCHEMA = "hashmm.desktop-workspace-cache.v2";
const LEGACY_CACHE_SCHEMA = "hashmm.desktop-work-canvas-cache.v1";
const MAX_ENTRIES = 48;
const MAX_ENTRY_BYTES = 24 * 1024 * 1024;
const NS_RE = /^[a-f0-9]{64}$/;
const RUN_RE = /^[A-Za-z0-9._:-]{1,160}$/;

function _blank(namespace = "") {
  return { schema: CACHE_SCHEMA, namespace, entries: {} };
}

function _validCanvas(runId, value) {
  return !!(
    value && typeof value === "object"
    && value.schema === "hashmm.work-canvas.v1"
    && value.run_id === runId
    && value.integrity && value.integrity.projection_only === true
    && value.integrity.auto_executes === false
    && value.integrity.widens_scope === false
  );
}

function _validWorkspaceSnapshot(cacheId, value) {
  if (!String(cacheId || "").startsWith("workspace:")) return false;
  const expectedId = String(cacheId).slice("workspace:".length);
  return !!(
    value && typeof value === "object"
    && value.schema === "hashmm.workspace.v2"
    && value.workspace && value.workspace.id === expectedId
    && value.sync && Number.isFinite(Number(value.sync.high_water_cursor))
    && value.trust && value.trust.owner_isolation === true
    && value.trust.model_prose_is_execution_evidence === false
    && Array.isArray(value.runs)
  );
}

function _validAccountReplica(cacheId, value) {
  if (cacheId !== "account:sessions") return false;
  if (!value || value.schema !== "hashmm.account-workspace-replica.v1"
      || !Array.isArray(value.sessions) || value.sessions.length > 10000) return false;
  return value.sessions.every((session) => !!(
    session && typeof session === "object"
    && typeof session.id === "string" && session.id.length <= 200
    && typeof session.title === "string" && session.title.length <= 2000
    && Array.isArray(session.messages)
  ));
}

function _validPayload(cacheId, value) {
  return _validCanvas(cacheId, value) || _validWorkspaceSnapshot(cacheId, value)
    || _validAccountReplica(cacheId, value);
}

/**
 * Encrypted, single-account work-canvas cache.
 *
 * The caller provides Electron safeStorage adapters. If platform encryption is
 * unavailable the cache stays memory-only and never writes plaintext. A new
 * opaque account namespace atomically discards the previous account's cache.
 */
function createWorkCanvasCache({
  filePath,
  encryptionAvailable,
  encrypt,
  decrypt,
  fsImpl = fs,
  now = () => Date.now(),
} = {}) {
  let loaded = false;
  let state = _blank();

  function available() {
    try {
      return !!filePath && !!encryptionAvailable() && typeof encrypt === "function"
        && typeof decrypt === "function";
    } catch (_) {
      return false;
    }
  }

  function persist() {
    if (!available()) return false;
    try {
      const plaintext = JSON.stringify(state);
      const encrypted = encrypt(plaintext);
      if (!Buffer.isBuffer(encrypted) || encrypted.length === 0) return false;
      const dir = path.dirname(filePath);
      fsImpl.mkdirSync(dir, { recursive: true });
      const temp = `${filePath}.tmp`;
      fsImpl.writeFileSync(temp, encrypted, { mode: 0o600 });
      fsImpl.renameSync(temp, filePath);
      return true;
    } catch (_) {
      return false;
    }
  }

  function loadOnce() {
    if (loaded) return;
    loaded = true;
    if (!available()) return;
    try {
      if (!fsImpl.existsSync(filePath)) return;
      const raw = fsImpl.readFileSync(filePath);
      const parsed = JSON.parse(decrypt(raw));
      if (parsed && [CACHE_SCHEMA, LEGACY_CACHE_SCHEMA].includes(parsed.schema)
          && NS_RE.test(parsed.namespace)
          && parsed.entries && typeof parsed.entries === "object") {
        state = { ...parsed, schema: CACHE_SCHEMA };
      }
    } catch (_) {
      state = _blank();
    }
  }

  function bind(namespace) {
    loadOnce();
    if (!NS_RE.test(String(namespace || ""))) return false;
    if (state.namespace !== namespace) {
      state = _blank(namespace);
      persist();
    }
    return true;
  }

  function get(namespace, runId) {
    if (!bind(namespace) || !RUN_RE.test(String(runId || ""))) {
      return { ok: false, hit: false, error: "invalid_cache_identity" };
    }
    const entry = state.entries[runId];
    if (!entry || !_validPayload(runId, entry.data)) return { ok: true, hit: false };
    entry.accessed_at = now();
    return { ok: true, hit: true, etag: String(entry.etag || ""), data: entry.data };
  }

  function put(namespace, runId, etag, data) {
    if (!bind(namespace) || !RUN_RE.test(String(runId || ""))
        || !_validPayload(runId, data)) {
      return { ok: false, error: "invalid_cache_entry" };
    }
    let bytes = 0;
    try { bytes = Buffer.byteLength(JSON.stringify(data), "utf8"); } catch (_) {}
    if (!bytes || bytes > MAX_ENTRY_BYTES) return { ok: false, error: "cache_entry_too_large" };
    state.entries[runId] = {
      etag: String(etag || "").slice(0, 160),
      data,
      bytes,
      accessed_at: now(),
    };
    const ordered = Object.entries(state.entries)
      .sort((a, b) => Number(b[1].accessed_at || 0) - Number(a[1].accessed_at || 0));
    state.entries = Object.fromEntries(ordered.slice(0, MAX_ENTRIES));
    return { ok: persist(), stored: true };
  }

  function remove(namespace, runId) {
    if (!bind(namespace) || !RUN_RE.test(String(runId || ""))) {
      return { ok: false, error: "invalid_cache_identity" };
    }
    const removed = Object.prototype.hasOwnProperty.call(state.entries, runId);
    delete state.entries[runId];
    persist();
    return { ok: true, removed };
  }

  return { get, put, remove, available, CACHE_SCHEMA };
}

module.exports = {
  createWorkCanvasCache,
  CACHE_SCHEMA,
  MAX_ENTRIES,
  MAX_ENTRY_BYTES,
  _validCanvas,
  _validWorkspaceSnapshot,
  _validAccountReplica,
  _validPayload,
};
