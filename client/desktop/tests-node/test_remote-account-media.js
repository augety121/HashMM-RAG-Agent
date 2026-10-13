"use strict";

const assert = require("assert");
const { RemoteMediaDeliveryBridge, NativeCompatPreview } = require("../modules/remote-account-media");

(async () => {
  const sent = [];
  const bridge = new RemoteMediaDeliveryBridge({ send: (message) => sent.push(message) });
  assert.strictEqual(bridge.deliver({ type: "viewerJoined", sessionId: "s1", vid: "v1" }), true);
  assert.strictEqual(sent.length, 0, "message waits until capture renderer is ready");
  bridge.setReady(true);
  assert.strictEqual(sent.length, 1);
  assert.ok(sent[0]._deliveryId);
  assert.strictEqual(bridge.acknowledge(sent[0]._deliveryId, true), true);
  assert.strictEqual(bridge.pending.size, 0);
  bridge.reset();

  const signals = [];
  let pushes = 0;
  const compat = new NativeCompatPreview({
    captureFrame: async () => Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
    pushFrame: async () => { pushes += 1; return { ok: true, watching: true }; },
    signal: (message) => signals.push(message),
  });
  assert.ok(compat.register({ type: "viewerJoined", sessionId: "s1", ticket: "t1", vid: "v1" }));
  assert.ok(compat.start("s1"));
  await new Promise((resolve) => setTimeout(resolve, 30));
  compat.stop("s1");
  assert.ok(pushes >= 1, "native compatibility path publishes a frame");
  assert.strictEqual(signals.filter((item) => item.milestone === "fallback_first_frame").length, 1);
  compat.reset();

  console.log("remote account media bridge: ok");
})().catch((error) => { console.error(error); process.exit(1); });
