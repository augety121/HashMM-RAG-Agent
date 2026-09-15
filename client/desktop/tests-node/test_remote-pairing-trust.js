"use strict";

const assert = require("assert");
const { TrustedDeviceStore } = require("../services/remote-pairing");

let records = {};
const store = new TrustedDeviceStore({
  load: () => records,
  save: (next) => { records = JSON.parse(JSON.stringify(next)); },
  now: () => 123456,
});
const deviceId = "viewer-device-1234";
const token = store.issue(deviceId);
assert.ok(/^[a-f0-9]{48}$/.test(token));
assert.ok(records[deviceId]);
assert.notStrictEqual(records[deviceId].tokenHash, token, "host stores only a digest");
assert.strictEqual(store.verify(deviceId, token), true);
assert.strictEqual(store.verify(deviceId, token.replace(/^./, token[0] === "a" ? "b" : "a")), false);
assert.strictEqual(store.verify("another-device", token), false);
assert.strictEqual(store.revoke(deviceId), true);
assert.strictEqual(store.verify(deviceId, token), false);

console.log("remote pairing trust: ok");
