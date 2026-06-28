/**
 * desktop/mcp/index.js — MCP 主进程接入（V100）。
 *
 * 提供懒加载单例 McpHost（首次用到才拉起 server 子进程）与 IPC 注册，让前端 Agent
 * 通过 `hashmm:mcp:*` 调到 MCP 工具。全程 try/catch 包裹——MCP 出问题绝不拖垮 App。
 */
"use strict";
const path = require("path");
const { McpHost } = require("./mcp-host");

let _host = null;
let _starting = null;

async function getHost() {
  if (_host) return _host;
  if (_starting) return _starting;
  _starting = (async () => {
    const host = new McpHost({
      args: [path.join(__dirname, "hashmm-mcp-server.js")],
    });
    await host.start();
    await host.listTools();
    _host = host;
    _starting = null;
    return host;
  })();
  return _starting;
}

function stopHost() {
  if (_host) { try { _host.stop(); } catch (_) { /* */ } _host = null; }
}

/** 注册 MCP 相关 IPC（在主进程 ipcMain 上）。返回是否注册成功。 */
function registerMcpIpc(ipcMain) {
  if (!ipcMain || typeof ipcMain.handle !== "function") return false;
  ipcMain.handle("hashmm:mcp:listTools", async () => {
    try { const h = await getHost(); return { ok: true, tools: await h.listTools() }; }
    catch (e) { return { ok: false, error: (e && e.message) || String(e) }; }
  });
  ipcMain.handle("hashmm:mcp:callTool", async (_e, name, args) => {
    try { const h = await getHost(); return { ok: true, result: await h.callTool(name, args) }; }
    catch (e) { return { ok: false, error: (e && e.message) || String(e) }; }
  });
  return true;
}

module.exports = { getHost, stopHost, registerMcpIpc };
