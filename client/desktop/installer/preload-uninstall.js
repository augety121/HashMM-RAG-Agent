/**
 * desktop/installer/preload-uninstall.js — 自绘卸载器 UI 的受控 IPC 桥（V100）。
 */
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("hashmmUninstaller", {
  minimize: () => ipcRenderer.send("hm-uninst:minimize"),
  close: () => ipcRenderer.send("hm-uninst:close"),
  info: () => ipcRenderer.invoke("hm-uninst:info"),
  run: () => ipcRenderer.invoke("hm-uninst:run"),
  done: () => ipcRenderer.send("hm-uninst:done"),
});
