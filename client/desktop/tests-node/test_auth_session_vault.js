"use strict";

const assert = require("assert");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { createAuthSessionVault } = require("../modules/auth-session-vault");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "hashmm-auth-vault-"));
const filePath = path.join(root, "auth-session.vault");
const key = crypto.randomBytes(32);
const encrypt = (text) => {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
  const body = Buffer.concat([cipher.update(text, "utf8"), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), body]);
};
const decrypt = (value) => {
  const iv = value.subarray(0, 12);
  const tag = value.subarray(12, 28);
  const decipher = crypto.createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(value.subarray(28)), decipher.final()]).toString("utf8");
};

try {
  const vault = createAuthSessionVault({
    filePath, encryptionAvailable: () => true, encrypt, decrypt,
  });
  assert.strictEqual(vault.save("refresh-secret", "user-a").ok, true);
  assert.strictEqual(fs.readFileSync(filePath).includes(Buffer.from("refresh-secret")), false);
  assert.deepStrictEqual(vault.read(), {
    ok: true, found: true, refreshToken: "refresh-secret", subject: "user-a",
  });
  assert.deepStrictEqual(vault.matches("refresh-secret", "user-a"), {
    ok: true, matched: true,
  });
  assert.deepStrictEqual(vault.replaceIfCurrent("stale-secret", "user-a", "must-not-win"), {
    ok: true, matched: false,
  });
  assert.strictEqual(vault.read().refreshToken, "refresh-secret");
  assert.deepStrictEqual(vault.replaceIfCurrent("refresh-secret", "user-a", "refresh-rotated"), {
    ok: true, stored: true, matched: true,
  });
  assert.strictEqual(vault.read().refreshToken, "refresh-rotated");
  assert.deepStrictEqual(vault.clearIfCurrent("refresh-secret", "user-a"), {
    ok: true, matched: false,
  });
  assert.strictEqual(vault.read().found, true);
  assert.strictEqual(vault.clear().ok, true);
  assert.strictEqual(vault.read().found, false);

  const refused = createAuthSessionVault({
    filePath: path.join(root, "plaintext-must-not-exist"),
    encryptionAvailable: () => false, encrypt, decrypt,
  });
  assert.strictEqual(refused.save("refresh-secret").ok, false);
  assert.strictEqual(fs.existsSync(path.join(root, "plaintext-must-not-exist")), false);

  fs.writeFileSync(filePath, Buffer.from("corrupt"));
  assert.strictEqual(vault.read().ok, false);
  assert.strictEqual(fs.existsSync(filePath), false);
  console.log("auth-session-vault: ok");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
