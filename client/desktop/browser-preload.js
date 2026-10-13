"use strict";
const { contextBridge, ipcRenderer } = require("electron");

// Narrow bridge for the trusted local browser cockpit. It has no filesystem,
// shell, generic IPC or web-page privileges.
contextBridge.exposeInMainWorld("hashmmBrowserCockpit", {
  state: () => ipcRenderer.invoke("browser:state"),
  clearTrace: () => ipcRenderer.invoke("browser:clearTrace"),
  showControlled: () => ipcRenderer.invoke("browser:showControlled"),
  onEvent: (cb) => {
    const handler = (_event, value) => { try { cb(value); } catch (_e) {} };
    ipcRenderer.on("browser:event", handler);
    return () => ipcRenderer.removeListener("browser:event", handler);
  },
});
