"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const read = (file) => fs.readFileSync(path.join(__dirname, "..", file), "utf8");
const main = read("main.js");
const host = read("remote-host.html");
const viewer = read("remote-viewer.html");
const hostPreload = read("remote-host-preload.js");
const viewerPreload = read("remote-viewer-preload.js");
const supervisor = read("services/remote-host-supervisor.js");
const approval = read("modules/remote-access-approval.js");

assert.ok(main.includes('preload: path.join(__dirname, "remote-host-preload.js")'));
assert.ok(main.includes('preload: path.join(__dirname, "remote-viewer-preload.js")'));
assert.ok(main.includes('nodeIntegration: false, contextIsolation: true'));
assert.ok(main.includes('webContents.send("remote-host-bootstrap", {'));
assert.ok(main.includes('/api/remote/v4/bootstrap'));
assert.ok(main.includes('/api/remote/v4/socket-ticket'));
assert.ok(!main.includes('return `${proto}//${u.host}/api/remote/ws`'), "account WSS must come from server bootstrap");
assert.ok(main.includes('ipcMain.on("remote-host-signal-send"'));
assert.ok(supervisor.includes('type: "auth"'), "main-process supervisor must own host authentication");
assert.ok(main.includes('message.type === "permissionRequest"'));
assert.ok(main.includes('_accountRemoteAccessApproval().handle(message)'));
assert.ok(approval.includes('this.sendDecision(message)'));
assert.ok(!main.includes('webContents.send("remote-host-access-decision", {'),
  "approval decisions must not depend on the hidden capture renderer");
assert.ok(!host.includes('/api/remote/v4/socket-ticket'), "capture renderer must not mint account tickets");
assert.ok(main.includes('webContents.send("remote-viewer-bootstrap", {'));
assert.ok(viewerPreload.includes('ipcRenderer.invoke("remote:viewerAdmission"'), "viewer reconnects need a fresh single-use ticket");
assert.ok(!main.includes('remote-host.html") + `?port=${encodeURIComponent(port)}&token='));
assert.ok(!main.includes('&token=${encodeURIComponent(opts.token'));

for (const source of [host, viewer]) {
  assert.ok(!source.includes('get("token")'), "remote page must not read an account token from its URL");
}
assert.ok(hostPreload.includes('"remote-host-bootstrap"'));
assert.ok(hostPreload.includes('"remote-host-status"'));
assert.ok(hostPreload.includes('"remote-host-signal-ack"'));
assert.ok(main.includes("RemoteMediaDeliveryBridge"));
assert.ok(main.includes("NativeCompatPreview"));
assert.ok(viewerPreload.includes('remote-viewer-bootstrap'));
assert.ok(!viewerPreload.includes("ipcRenderer.send"), "viewer preload must not expose fire-and-forget IPC");

assert.ok(main.includes("if (!_isRemoteHostSender(event)) return;"), "privileged host IPC must check its sender");
assert.ok(host.includes("permissionRequest"));
assert.ok(host.includes("remote-host-access-request"));
assert.ok(host.includes('createDataChannel("hashmm-control-v1"'));
assert.ok(host.includes('createDataChannel("hashmm-file-v1"'));
assert.ok(host.includes("_lastControlSeqByViewer"), "data-channel input must reject replayed sequences");
assert.ok(viewer.includes("sessionId: activeSessionId"));
assert.ok(viewer.includes("timestamp: Date.now()"));
assert.ok(viewer.includes("clientRequestId"), "remote retries must share an idempotency key");
assert.ok(!viewer.includes('mjpeg?ticket='), "compatibility preview tickets must not appear in URLs");
assert.ok(viewer.includes('Authorization: "Remote " + relayTicket'));
assert.ok(viewer.includes('"first_frame_rendered"'));
assert.ok(host.includes('"fallback_first_frame"'));
assert.ok(host.includes('type: "compatStarted"'), "host must confirm compatibility media startup to the viewer");
assert.ok(!viewer.includes("openrelay.metered.ca"));

assert.ok(main.includes("former direct Supabase polling"));
assert.ok(main.includes('registered: !!_remoteAcctState.registered'));
assert.ok(host.includes('reportHostStatus("registered")'));
assert.ok(supervisor.includes('type: "heartbeat"'), "main-process supervisor must renew its presence lease");
assert.ok(supervisor.includes('message.type === "leaseRejected" || message.type === "deviceReplaced"'));
assert.ok(host.includes('DEVICE_ID = String(payload.deviceId)'));
assert.ok(host.includes('MODE === "supabase") log("旧版直连信令已停用'));
assert.ok(!host.includes('if (MODE === "supabase") connectSupabase()'));

console.log("remote session security contract: ok");
