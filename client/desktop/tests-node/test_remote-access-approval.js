"use strict";

const assert = require("assert");
const {
  RemoteAccessApprovalCoordinator,
  normalizeRequest,
  promptSpec,
} = require("../modules/remote-access-approval.js");

async function main() {
  assert.strictEqual(normalizeRequest({ sessionId: "x", scopes: ["control"] }), null);
  const normalized = normalizeRequest({
    sessionId: "session-123",
    scopes: ["view", "control", "unknown", "view"],
    viewer: { name: "Android", platform: "android", deviceId: "phone-1" },
  });
  assert.deepStrictEqual(normalized.scopes, ["view", "control"]);
  assert.strictEqual(promptSpec(normalized).taskId, "session-123");

  let prompts = 0;
  const messages = [];
  let resolvePrompt;
  const coordinator = new RemoteAccessApprovalCoordinator({
    requestPrompt: () => {
      prompts += 1;
      return new Promise((resolve) => { resolvePrompt = resolve; });
    },
    sendDecision: (message) => { messages.push(message); return true; },
  });
  const payload = {
    sessionId: "session-123", scopes: ["view", "control"],
    viewer: { name: "Android", platform: "android", deviceId: "phone-1" },
  };
  const first = coordinator.handle(payload);
  const duplicate = coordinator.handle(payload);
  assert.strictEqual(first, duplicate, "same server session must share one approval task");
  assert.strictEqual(prompts, 1, "duplicate delivery must not open another prompt");
  resolvePrompt({ decision: "approve" });
  const result = await first;
  assert.deepStrictEqual(result, { ok: true, decision: "approve", sessionId: "session-123", error: "" });
  assert.deepStrictEqual(messages, [{
    type: "permissionDecision", sessionId: "session-123", decision: "approve",
    scopes: ["view", "control"],
  }]);

  const denied = [];
  const failClosed = new RemoteAccessApprovalCoordinator({
    requestPrompt: async () => { throw new Error("renderer unavailable"); },
    sendDecision: (message) => { denied.push(message); return true; },
  });
  const deniedResult = await failClosed.handle({ sessionId: "session-456", scopes: ["view"] });
  assert.strictEqual(deniedResult.decision, "deny");
  assert.strictEqual(denied[0].decision, "deny");

  console.log("remote access approval coordinator: ok");
}

main().catch((error) => { console.error(error); process.exit(1); });
