"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const {
  createWorkCanvasCache,
  MAX_ENTRIES,
} = require("../modules/work-canvas-cache");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "hashmm-work-cache-"));
const filePath = path.join(root, "work-canvas.cache");
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
const namespaceA = "a".repeat(64);
const namespaceB = "b".repeat(64);
let clock = 0;
const canvas = (runId) => ({
  schema: "hashmm.work-canvas.v1",
  run_id: runId,
  integrity: {
    projection_only: true,
    auto_executes: false,
    widens_scope: false,
  },
});
const workspace = (workspaceId) => ({
  schema: "hashmm.workspace.v2",
  workspace: { id: workspaceId },
  sync: { high_water_cursor: 9 },
  trust: {
    owner_isolation: true,
    model_prose_is_execution_evidence: false,
  },
  runs: [],
});

try {
  const cache = createWorkCanvasCache({
    filePath,
    encryptionAvailable: () => true,
    encrypt,
    decrypt,
  });
  assert.deepStrictEqual(cache.put(namespaceA, "run-1", "\"etag-1\"", canvas("run-1")), {
    ok: true,
    stored: true,
  });
  assert.strictEqual(cache.get(namespaceA, "run-1").hit, true);
  assert.strictEqual(cache.get(namespaceA, "run-1").data.run_id, "run-1");
  assert.strictEqual(
    cache.put(namespaceA, "workspace:personal", "\"workspace-etag\"", workspace("personal")).ok,
    true,
  );
  assert.strictEqual(cache.get(namespaceA, "workspace:personal").hit, true);
  const accountReplica = {
    schema: "hashmm.account-workspace-replica.v1",
    sessions: [{ id: "chat-1", title: "private", messages: [{ role: "user", content: "secret body" }] }],
    saved_at: Date.now(),
  };
  assert.strictEqual(cache.put(namespaceA, "account:sessions", "1", accountReplica).ok, true);
  assert.strictEqual(cache.get(namespaceA, "account:sessions").data.sessions[0].id, "chat-1");
  assert.strictEqual(fs.readFileSync(filePath).includes(Buffer.from("secret body")), false);
  assert.strictEqual(
    cache.put(namespaceA, "workspace:personal", "\"wrong\"", workspace("another")).ok,
    false,
  );
  assert.strictEqual(fs.readFileSync(filePath).includes(Buffer.from("run-1")), false);

  const reopened = createWorkCanvasCache({
    filePath,
    encryptionAvailable: () => true,
    encrypt,
    decrypt,
    now: () => ++clock,
  });
  assert.strictEqual(reopened.get(namespaceA, "run-1").hit, true);
  assert.strictEqual(reopened.get(namespaceB, "run-1").hit, false);
  assert.strictEqual(reopened.get(namespaceA, "run-1").hit, false);

  for (let index = 0; index < MAX_ENTRIES + 3; index += 1) {
    const runId = `bounded-${index}`;
    reopened.put(namespaceA, runId, `"${index}"`, canvas(runId));
  }
  assert.strictEqual(reopened.get(namespaceA, "bounded-0").hit, false);
  assert.strictEqual(reopened.get(namespaceA, `bounded-${MAX_ENTRIES + 2}`).hit, true);

  const unavailablePath = path.join(root, "must-not-exist.cache");
  const unavailable = createWorkCanvasCache({
    filePath: unavailablePath,
    encryptionAvailable: () => false,
    encrypt,
    decrypt,
  });
  assert.strictEqual(unavailable.put(namespaceA, "run-2", "\"e\"", canvas("run-2")).ok, false);
  assert.strictEqual(fs.existsSync(unavailablePath), false);

  assert.strictEqual(cache.put(namespaceA, "../escape", "\"x\"", canvas("../escape")).ok, false);
  assert.strictEqual(cache.put(namespaceA, "wrong", "\"x\"", canvas("another")).ok, false);
  assert.strictEqual(cache.put(namespaceA, "account:sessions", "x", {
    schema: "hashmm.account-workspace-replica.v1", sessions: [{ id: 1, title: "bad", messages: [] }],
  }).ok, false);
  console.log("work-canvas-cache: ok");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
