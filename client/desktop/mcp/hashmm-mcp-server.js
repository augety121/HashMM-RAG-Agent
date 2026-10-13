#!/usr/bin/env node
/**
 * desktop/mcp/hashmm-mcp-server.js — HashMM 的 MCP Server（V100，对标 MarvisMCP.exe）。
 *
 * 作为**独立进程**运行（进程分离：工具崩了不拖垮主进程/UI），通过 **stdin/stdout
 * JSON-RPC 2.0**（行分隔）暴露工具。标准 MCP 流：
 *   initialize → notifications/initialized → tools/list → tools/call
 *
 * 工具用 ToolRegistry 注册、WhitelistManager 管控（默认 deny，未在白名单的工具拒调用）。
 * 这里内置一批"本地优先"的安全工具（回显/系统信息/列目录/读文本片段/CU 文字定位）；
 * 重活（真实 Computer Use 点击、终端命令）由宿主在授权后接更高权限的实现，此处给出
 * 协议骨架与可独立运行验证的最小可用集。
 *
 * 纯 Node，无 electron 依赖 → 可在沙箱/CI 直接 `node hashmm-mcp-server.js` 跑通握手。
 */
"use strict";

const os = require("os");
const fs = require("fs");
const path = require("path");
const P = require("./protocol");
const { ToolRegistry } = require("./tool-registry");
const { WhitelistManager } = require("./whitelist");

const SERVER_INFO = { name: "hashmm-mcp", version: "17.0.2" };

// ── 工具注册 ──
const registry = new ToolRegistry();

registry.register("echo", {
  description: "回显文本（连通性自检）",
  inputSchema: { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
  handler: ({ text }) => ({ content: [{ type: "text", text: String(text) }] }),
});

registry.register("system_info", {
  description: "返回本机基础信息（平台/架构/CPU 数/内存），用于本地/云端模型能力判断",
  inputSchema: { type: "object", properties: {} },
  handler: () => ({
    content: [{ type: "text", text: JSON.stringify({
      platform: os.platform(), arch: os.arch(), cpus: os.cpus().length,
      totalmemMB: Math.round(os.totalmem() / 1048576), freememMB: Math.round(os.freemem() / 1048576),
    }) }],
  }),
});

registry.register("list_dir", {
  description: "列出目录下的条目（只读）",
  inputSchema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
  handler: ({ path: p }) => {
    const entries = fs.readdirSync(p, { withFileTypes: true })
      .map((d) => ({ name: d.name, type: d.isDirectory() ? "dir" : "file" }));
    return { content: [{ type: "text", text: JSON.stringify(entries) }] };
  },
});

registry.register("read_text", {
  description: "读取文本文件的前 N 个字符（只读，默认 4000）",
  inputSchema: {
    type: "object",
    properties: { path: { type: "string" }, maxChars: { type: "integer" } },
    required: ["path"],
  },
  handler: ({ path: p, maxChars }) => {
    const lim = Number.isInteger(maxChars) && maxChars > 0 ? maxChars : 4000;
    const txt = fs.readFileSync(p, "utf8").slice(0, lim);
    return { content: [{ type: "text", text: txt }] };
  },
});

// CU 文字定位（复用 modules/cu-grounding 的纯逻辑；找不到则降级，不让 server 崩）
try {
  const grounding = require("../modules/cu-grounding");
  if (grounding && typeof grounding.locateElement === "function") {
    registry.register("locate_element", {
      description: "在 OCR 元素列表里按文字定位目标，返回归一化中心坐标（只读，不点击）",
      inputSchema: {
        type: "object",
        properties: { query: { type: "string" }, elements: { type: "array" } },
        required: ["query", "elements"],
      },
      handler: ({ query, elements }) => {
        const r = grounding.locateElement(query, elements || []);
        return { content: [{ type: "text", text: JSON.stringify(r) }] };
      },
    });
  }
} catch (_) { /* grounding 不在则跳过该工具 */ }

// ── 受权限「重活」工具（privileged）：默认不在白名单 → 拒调，宿主显式授权才放行 ──
// 这是"重活工具 → MCP 高权限"的落地：把会写盘/动系统的能力收口到 MCP，加权限门。
// save_file/download_to 用 storage 真实写入工作区（默认安装目录内的 HashMM Files）。
const PRIVILEGED = new Set(["save_file", "download_to", "run_command", "click"]);
let _storage = null;
function storage() {
  if (_storage) return _storage;
  try {
    const { Storage } = require("../storage");
    const path = require("path");
    // server 作为子进程时，安装目录 = 主程序 exe 目录（其父）；测试/独立跑时退回 cwd
    const installDir = process.env.HASHMM_INSTALL_DIR || path.dirname(path.dirname(process.execPath || "")) || process.cwd();
    _storage = new Storage({ installDir });
  } catch (_) { _storage = null; }
  return _storage;
}

registry.register("save_file", {
  description: "把内容保存为文件到工作区（默认安装目录内的 HashMM Files；受权限管控）",
  inputSchema: {
    type: "object",
    properties: { name: { type: "string" }, content: { type: "string" }, kind: { type: "string" } },
    required: ["name", "content"],
  },
  handler: ({ name, content, kind }) => {
    const s = storage();
    if (!s) return { content: [{ type: "text", text: "存储服务不可用" }], isError: true };
    const r = s.saveFile(name, content, kind);
    return r.ok
      ? { content: [{ type: "text", text: "已保存：" + r.path }] }
      : { content: [{ type: "text", text: "保存失败：" + r.error }], isError: true };
  },
});

registry.register("download_to", {
  description: "返回工作区 Downloads 下不冲突的目标路径（下载落地用；受权限管控）",
  inputSchema: { type: "object", properties: { filename: { type: "string" } }, required: ["filename"] },
  handler: ({ filename }) => {
    const s = storage();
    if (!s) return { content: [{ type: "text", text: "存储服务不可用" }], isError: true };
    return { content: [{ type: "text", text: s.getDownloadTarget(filename) }] };
  },
});

// run_command / click 等真正危险的：给协议骨架 + 权限门，但不在此进程实际执行——
// 需宿主注入 executor 后才落地（诚实：不让 MCP server 自行跑任意命令/点任意位置）。
registry.register("run_command", {
  description: "执行命令（高危·受权限管控·需宿主授权并注入执行器）",
  inputSchema: { type: "object", properties: { command: { type: "string" } }, required: ["command"] },
  handler: () => ({ content: [{ type: "text", text: "run_command 需宿主授权并注入执行器后才可用" }], isError: true }),
});

// ── 白名单：默认只放行内置**只读**工具；privileged 工具默认拒（defaultPolicy=deny） ──
const whitelist = new WhitelistManager({
  defaultPolicy: "deny",
  initial: ["echo", "system_info", "list_dir", "read_text", "locate_element"],
});

/** 宿主授权：把指定 privileged 工具加入白名单（如用户在 UI 同意"允许保存文件"）。 */
function grant(tools) {
  const cur = whitelist.snapshot().allow;
  whitelist.setAllowed(Array.from(new Set([...cur, ...(tools || [])])));
}

// ── JSON-RPC 主循环 ──
let initialized = false;
let buf = "";

function send(msg) { process.stdout.write(P.encode(msg)); }

async function handleRequest(msg) {
  const { id, method, params } = msg;
  try {
    switch (method) {
      case "initialize":
        return send(P.makeResponse(id, {
          protocolVersion: P.MCP_PROTOCOL_VERSION,
          capabilities: { tools: { listChanged: false } },
          serverInfo: SERVER_INFO,
        }));
      case "ping":
        return send(P.makeResponse(id, {}));
      case "tools/list":
        return send(P.makeResponse(id, { tools: registry.list() }));
      case "tools/call": {
        const name = params && params.name;
        const args = (params && params.arguments) || {};
        if (!registry.has(name)) return send(P.makeError(id, P.ErrorCode.MethodNotFound, `未知工具：${name}`));
        if (!whitelist.isAllowed(name)) {
          // 不在白名单：按 MCP 约定返回 isError 的工具结果（而非协议错误）
          return send(P.makeResponse(id, { content: [{ type: "text", text: `工具 ${name} 未授权` }], isError: true }));
        }
        try {
          const result = await registry.call(name, args);
          return send(P.makeResponse(id, result));
        } catch (e) {
          return send(P.makeResponse(id, { content: [{ type: "text", text: "工具执行失败：" + ((e && e.message) || e) }], isError: true }));
        }
      }
      default:
        return send(P.makeError(id, P.ErrorCode.MethodNotFound, `未知方法：${method}`));
    }
  } catch (e) {
    return send(P.makeError(id, P.ErrorCode.InternalError, (e && e.message) || String(e)));
  }
}

function onMessage(msg) {
  if (P.isNotification(msg)) {
    if (msg.method === "notifications/initialized") initialized = true;
    return;
  }
  if (P.isRequest(msg)) return handleRequest(msg);
}

if (require.main === module) {
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    buf += chunk;
    const { messages, rest } = P.decode(buf);
    buf = rest;
    for (const m of messages) onMessage(m);
  });
  process.stdin.on("end", () => process.exit(0));
}

// 导出供单测直接驱动（不经 stdio）
module.exports = { registry, whitelist, handleRequest, onMessage, SERVER_INFO, grant, PRIVILEGED };
