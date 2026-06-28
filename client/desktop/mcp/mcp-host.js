/**
 * desktop/mcp/mcp-host.js — MCP 宿主/客户端（V100）。
 *
 * 主进程用它把一个或多个 MCP Server 作为**独立子进程**拉起（进程分离），完成标准握手
 * （initialize → notifications/initialized），随后 listTools()/callTool() 供 Agent 调度。
 * 传输 = 子进程 stdin/stdout 行分隔 JSON-RPC（对标 Marvis 宿主与 MarvisMCP.exe 的关系）。
 *
 * 依赖 child_process（沙箱可用），可被集成测试 spawn 真实 server 验证端到端握手。
 */
"use strict";

const { spawn } = require("child_process");
const path = require("path");
const P = require("./protocol");

class McpHost {
  /**
   * @param {object} opts
   * @param {string} opts.command 启动 server 的可执行（默认当前 node）
   * @param {string[]} opts.args 参数（默认指向内置 hashmm-mcp-server.js）
   * @param {number} opts.timeoutMs 单次请求超时（默认 15s）
   */
  constructor(opts = {}) {
    this.command = opts.command || process.execPath;
    this.args = opts.args || [path.join(__dirname, "hashmm-mcp-server.js")];
    this.timeoutMs = opts.timeoutMs || 15000;
    this.proc = null;
    this._buf = "";
    this._nextId = 1;
    this._pending = new Map(); // id -> {resolve, reject, timer}
    this._serverInfo = null;
    this._tools = [];
  }

  /** 拉起子进程并完成 MCP 握手，返回 serverInfo。 */
  async start() {
    this.proc = spawn(this.command, this.args, { stdio: ["pipe", "pipe", "inherit"] });
    this.proc.stdout.setEncoding("utf8");
    this.proc.stdout.on("data", (chunk) => this._onData(chunk));
    this.proc.on("exit", () => this._failAll(new Error("MCP server 已退出")));

    const init = await this._request("initialize", {
      protocolVersion: P.MCP_PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: { name: "hashmm-host", version: "1.6.0" },
    });
    this._serverInfo = init && init.serverInfo;
    this._notify("notifications/initialized");
    return this._serverInfo;
  }

  /** 拉取并缓存工具清单。 */
  async listTools() {
    const r = await this._request("tools/list");
    this._tools = (r && r.tools) || [];
    return this._tools;
  }

  /** 调用一个工具，返回 MCP 工具结果 { content, isError? }。 */
  async callTool(name, args) {
    return this._request("tools/call", { name, arguments: args || {} });
  }

  stop() {
    if (this.proc) { try { this.proc.kill(); } catch (_) { /* */ } this.proc = null; }
    this._failAll(new Error("host 已停止"));
  }

  // ── 内部 ──
  _onData(chunk) {
    this._buf += chunk;
    const { messages, rest } = P.decode(this._buf);
    this._buf = rest;
    for (const m of messages) {
      if ("id" in m && (("result" in m) || ("error" in m))) {
        const waiter = this._pending.get(m.id);
        if (!waiter) continue;
        this._pending.delete(m.id);
        clearTimeout(waiter.timer);
        if (m.error) waiter.reject(new Error(m.error.message || "MCP error"));
        else waiter.resolve(m.result);
      }
      // 通知（如 server 主动推送）这里可扩展处理
    }
  }

  _request(method, params) {
    const id = this._nextId++;
    const msg = P.makeRequest(id, method, params);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pending.delete(id);
        reject(new Error(`MCP 请求超时：${method}`));
      }, this.timeoutMs);
      this._pending.set(id, { resolve, reject, timer });
      try { this.proc.stdin.write(P.encode(msg)); }
      catch (e) { clearTimeout(timer); this._pending.delete(id); reject(e); }
    });
  }

  _notify(method, params) {
    try { this.proc.stdin.write(P.encode(P.makeNotification(method, params))); } catch (_) { /* */ }
  }

  _failAll(err) {
    for (const [, w] of this._pending) { clearTimeout(w.timer); w.reject(err); }
    this._pending.clear();
  }
}

module.exports = { McpHost };
