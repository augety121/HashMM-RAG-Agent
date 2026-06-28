/**
 * desktop/installer/preload.js — 自绘安装器 UI 的受控 IPC 桥（V100）。
 * contextIsolation 下，只暴露安装所需的最小 API 给 ui.html，绝不开放 node。
 */
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("hashmmInstaller", {
  minimize: () => ipcRenderer.send("hm-inst:minimize"),
  close: () => ipcRenderer.send("hm-inst:close"),
  defaults: () => ipcRenderer.invoke("hm-inst:defaults"),
  browse: (current) => ipcRenderer.invoke("hm-inst:browse", current),
  install: (dir) => ipcRenderer.invoke("hm-inst:install", dir),
  launch: (exe) => ipcRenderer.send("hm-inst:launch", exe),
  openEula: () => ipcRenderer.invoke("hm-inst:eula"),
  onProgress: (cb) => ipcRenderer.on("hm-inst:progress", (_e, data) => cb(data)),
});
