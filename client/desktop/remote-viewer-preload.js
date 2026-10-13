"use strict";

// The visible viewer only receives refreshed identity credentials. It has no
// filesystem, shell, clipboard or arbitrary IPC bridge.
const { contextBridge, ipcRenderer } = require("electron");

function subscribe(channel, callback) {
  if (typeof callback !== "function") return;
  ipcRenderer.on(channel, (_event, payload) => callback(payload || {}));
}

contextBridge.exposeInMainWorld("hashmmRemoteViewer", {
  onBootstrap: (callback) => subscribe("remote-viewer-bootstrap", callback),
  onToken: (callback) => subscribe("remote-viewer-token", callback),
  renewAdmission: (deviceId) => ipcRenderer.invoke("remote:viewerAdmission", { deviceId }),
});
