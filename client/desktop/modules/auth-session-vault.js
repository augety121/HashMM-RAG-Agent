"use strict";

const fs = require("fs");
const path = require("path");

const SCHEMA = "hashmm.desktop-auth-session.v1";
const MAX_REFRESH_BYTES = 16 * 1024;

function createAuthSessionVault(options = {}) {
  const filePath = String(options.filePath || "");
  const encryptionAvailable = options.encryptionAvailable;
  const encrypt = options.encrypt;
  const decrypt = options.decrypt;

  function supported() {
    return !!filePath
      && typeof encryptionAvailable === "function"
      && encryptionAvailable() === true
      && typeof encrypt === "function"
      && typeof decrypt === "function";
  }

  function clear() {
    try {
      if (filePath && fs.existsSync(filePath)) fs.unlinkSync(filePath);
      return { ok: true, cleared: true };
    } catch (error) {
      return { ok: false, error: String(error && error.message || error) };
    }
  }

  function save(refreshToken, subject = "") {
    const token = String(refreshToken || "").trim();
    if (!token || Buffer.byteLength(token, "utf8") > MAX_REFRESH_BYTES) {
      return { ok: false, error: "invalid-refresh-token" };
    }
    if (!supported()) return { ok: false, error: "encryption-unavailable" };
    try {
      const payload = JSON.stringify({
        schema: SCHEMA,
        refresh_token: token,
        subject: String(subject || "").slice(0, 240),
        updated_at: Date.now(),
      });
      const ciphertext = Buffer.from(encrypt(payload));
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      const temporary = `${filePath}.${process.pid}.tmp`;
      fs.writeFileSync(temporary, ciphertext, { mode: 0o600 });
      fs.renameSync(temporary, filePath);
      return { ok: true, stored: true };
    } catch (error) {
      clear();
      return { ok: false, error: String(error && error.message || error) };
    }
  }

  function read() {
    if (!supported()) return { ok: false, error: "encryption-unavailable" };
    if (!fs.existsSync(filePath)) return { ok: true, found: false };
    try {
      const payload = JSON.parse(decrypt(fs.readFileSync(filePath)));
      const token = String(payload && payload.refresh_token || "").trim();
      if (!payload || payload.schema !== SCHEMA || !token
          || Buffer.byteLength(token, "utf8") > MAX_REFRESH_BYTES) {
        clear();
        return { ok: false, error: "invalid-vault-payload" };
      }
      return {
        ok: true, found: true, refreshToken: token,
        subject: String(payload.subject || "").slice(0, 240),
      };
    } catch (error) {
      clear();
      return { ok: false, error: "vault-decryption-failed" };
    }
  }

  function matches(refreshToken, subject = "") {
    const current = read();
    if (!current.ok || !current.found) {
      return { ok: current.ok, matched: false, error: current.error };
    }
    return {
      ok: true,
      matched: current.refreshToken === String(refreshToken || "").trim()
        && current.subject === String(subject || "").slice(0, 240),
    };
  }

  function clearIfCurrent(refreshToken, subject = "") {
    const current = matches(refreshToken, subject);
    if (!current.ok || !current.matched) return current;
    const cleared = clear();
    return { ...cleared, matched: cleared.ok };
  }

  function replaceIfCurrent(refreshToken, subject, nextRefreshToken) {
    const current = matches(refreshToken, subject);
    if (!current.ok || !current.matched) return current;
    const stored = save(nextRefreshToken, subject);
    return { ...stored, matched: stored.ok };
  }

  return {
    save, read, clear, supported, matches, clearIfCurrent, replaceIfCurrent,
  };
}

module.exports = { createAuthSessionVault, SCHEMA, MAX_REFRESH_BYTES };
