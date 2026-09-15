"use strict";

// Narrow bridge for the hidden capture renderer.  The page gets no Node.js,
// filesystem, shell or unrestricted IPC access.
const { contextBridge, ipcRenderer, clipboard } = require("electron");
const quality = require("./remote-quality.js");
const protocol = require("./remote-protocol.js");
const { ClipboardSync } = require("./remote-extras.js");

const INBOUND = new Set([
  "remote-host-bootstrap",
  "remote-host-token", "remote-host-access-decision", "remote-set-privacy",
  "remote-host-reply", "remote-set-quality", "manual-create", "manual-answer",
  "remote-switch-monitor", "remote-host-signal-message", "remote-host-signal-state",
]);
const OUTBOUND = new Set([
  "remote-host-access-request", "remote-quality-telemetry", "remote-host-input",
  "remote-host-file", "remote-host-monitors-query", "remote-host-select-monitor",
  "manual-offer", "manual-applied", "remote-handoff", "remote-host-status",
  "remote-host-ready",
  "remote-host-signal-send", "remote-host-signal-ack",
]);
const clipSync = new ClipboardSync({ minIntervalMs: 400 });

contextBridge.exposeInMainWorld("hashmmRemoteHost", {
  ipc: {
    on: (channel, callback) => {
      if (!INBOUND.has(channel) || typeof callback !== "function") return;
      ipcRenderer.on(channel, (_event, ...args) => callback(null, ...args));
    },
    send: (channel, payload) => { if (OUTBOUND.has(channel)) ipcRenderer.send(channel, payload); },
  },
  quality: {
    DEFAULT_TIER: quality.DEFAULT_TIER,
    extractStats: (entries) => quality.extractStats(entries),
    adaptQuality: (stats, tier) => quality.adaptQuality(stats, tier),
    buildQualityTelemetry: (stats, decision, meta) => quality.buildQualityTelemetry(stats, decision, meta),
  },
  protocol: {
    T: Object.assign({}, protocol.T),
    decode: (raw) => protocol.decode(raw),
  },
  clipboard: {
    readText: () => clipboard.readText(),
    writeText: (text) => clipboard.writeText(String(text || "")),
    onLocalChange: (text) => clipSync.onLocalChange(text),
    onRemoteData: (payload) => clipSync.onRemoteData(payload),
  },
});
