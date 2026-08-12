"use strict";

const assert = require("assert");
const http = require("http");
const { WebSocketServer } = require("ws");
const { RemoteHostSupervisor } = require("../services/remote-host-supervisor.js");

const waitFor = async (predicate, timeoutMs = 3000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("condition-timeout");
};

(async () => {
  let receivedAuth = null;
  const server = http.createServer((request, response) => {
    if (request.url === "/api/remote/v4/socket-ticket" && request.method === "POST") {
      response.writeHead(200, { "content-type": "application/json" });
      const origin = `ws://127.0.0.1:${server.address().port}`;
      response.end(JSON.stringify({
        ticket: "rst_real", attempt_id: "ra_real", trace_id: "rt_real",
        control_wss: `${origin}/api/remote/v4/ws`, config_revision: "rev-real",
      }));
      return;
    }
    response.writeHead(404); response.end();
  });
  const wss = new WebSocketServer({ server, path: "/api/remote/v4/ws" });
  wss.on("connection", (socket) => {
    socket.on("message", (data) => {
      const message = JSON.parse(data.toString("utf8"));
      if (message.type === "auth") {
        receivedAuth = message;
        socket.send(JSON.stringify({ type: "authOk", protocol: "hashmm.remote.v4" }));
      } else if (message.type === "heartbeat") {
        socket.send(JSON.stringify({ type: "heartbeatAck", leaseExpiresIn: 35 }));
      }
    });
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const states = [];
  const supervisor = new RemoteHostSupervisor({ onState: (state) => states.push(state) });
  try {
    await supervisor.start({
      token: "account-token", apiBase: `http://127.0.0.1:${address.port}`,
      signalUrl: `ws://127.0.0.1:${address.port}/api/remote/v4/ws`,
      deviceId: "desktop-real-001", name: "Desktop", platform: "win32", appVersion: "12.1.0",
    });
    await waitFor(() => supervisor.snapshot().registered === true);
    assert.strictEqual(receivedAuth.type, "auth");
    assert.strictEqual(receivedAuth.ticket, "rst_real");
    assert.strictEqual(receivedAuth.role, "host");
    assert.strictEqual(supervisor.snapshot().attemptId, "ra_real");
    assert.strictEqual(supervisor.snapshot().authFrameSent, true);
    assert.ok(states.some((state) => state.state === "authenticating" && state.authFrameSent));
  } finally {
    supervisor.stop("test-complete");
    await new Promise((resolve) => wss.close(resolve));
    await new Promise((resolve) => server.close(resolve));
  }
  console.log("remote host supervisor real ws: ok");
})().catch((error) => { console.error(error); process.exitCode = 1; });
