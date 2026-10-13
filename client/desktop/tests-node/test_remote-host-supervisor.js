"use strict";
const assert = require("assert");
const { RemoteHostSupervisor, classifyAdmissionError } = require("../services/remote-host-supervisor.js");

assert.deepStrictEqual(classifyAdmissionError("socket-ticket-404"), {
  state: "server_incompatible", retryable: false, errorCode: "REMOTE_SERVER_TOO_OLD",
});
assert.deepStrictEqual(classifyAdmissionError("remote-bootstrap-401"), {
  state: "auth_expired", retryable: false, errorCode: "REMOTE_AUTH_REQUIRED",
});

class FakeSocket {
  static instances = [];
  constructor(url) {
    this.url = url; this.readyState = 0; this.handlers = {}; this.sent = [];
    FakeSocket.instances.push(this);
  }
  addEventListener(name, handler) { (this.handlers[name] ||= []).push(handler); }
  emit(name, payload = {}) { for (const handler of this.handlers[name] || []) handler(payload); }
  send(payload) { this.sent.push(JSON.parse(payload)); }
  close(code = 1000) { this.readyState = 3; this.emit("close", { code }); }
}

(async () => {
  let ticketCalls = 0;
  const states = [];
  const messages = [];
  const timers = [];
  const supervisor = new RemoteHostSupervisor({
    WebSocketImpl: FakeSocket,
    fetchImpl: async () => {
      ticketCalls += 1;
      return { ok: true, json: async () => ({
        ticket: "rst_one", attempt_id: "ra_one", trace_id: "rt_one",
        control_wss: "wss://hashmm.example/api/remote/v4/ws", config_revision: "rev-one",
      }) };
    },
    onState: (state) => states.push(state),
    onMessage: (message) => messages.push(message),
    setTimeoutImpl: (fn, ms) => { const timer = { fn, ms, cleared: false }; timers.push(timer); return timer; },
    clearTimeoutImpl: (timer) => { if (timer) timer.cleared = true; },
    setIntervalImpl: () => ({ interval: true }),
    clearIntervalImpl: () => {},
    random: () => 0.5,
  });
  const config = {
    token: "account-token", apiBase: "https://hashmm.example", signalUrl: "wss://hashmm.example/api/remote/v4/ws",
    deviceId: "desktop-stable", name: "Desktop", platform: "win32", appVersion: "12.0.0",
  };
  const first = supervisor.start(config);
  const second = supervisor.start(config);
  await Promise.all([first, second]);
  assert.strictEqual(ticketCalls, 1, "concurrent starts must share one ticket request");
  assert.strictEqual(FakeSocket.instances.length, 1, "only one control socket may exist");
  const socket = FakeSocket.instances[0];
  socket.readyState = 1; socket.emit("open");
  assert.deepStrictEqual(socket.sent[0], {
    type: "auth", ticket: "rst_one", role: "host", deviceId: "desktop-stable",
    name: "Desktop", platform: "win32", appVersion: "12.0.0", protocol: "hashmm.remote.v4",
    attemptId: "ra_one", traceId: "rt_one",
  });
  assert.ok(socket.url.includes("attempt_id=ra_one"));
  assert.ok(socket.url.includes("trace_id=rt_one"));
  socket.emit("message", { data: JSON.stringify({ type: "authOk", protocol: "hashmm.remote.v4" }) });
  assert.strictEqual(supervisor.snapshot().registered, true);
  assert.strictEqual(messages.at(-1).type, "authOk");
  socket.close(1006);
  assert.strictEqual(supervisor.snapshot().state, "reconnecting");
  assert.ok(timers.some((timer) => timer.ms === 1000 && !timer.cleared), "disconnect must schedule bounded recovery");
  supervisor.stop();
  assert.strictEqual(supervisor.snapshot().state, "stopped");
  assert.ok(states.some((state) => state.state === "registered"));
  console.log("remote host supervisor: ok");
})().catch((error) => { console.error(error); process.exitCode = 1; });
