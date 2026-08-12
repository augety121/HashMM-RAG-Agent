/**
 * desktop/mcp/protocol.js — MCP（Model Context Protocol）传输与消息层（V100）。
 *
 * 对标 Marvis 架构第 4 层（MarvisMCP.exe 以 stdin/stdout JSON-RPC 暴露工具）。
 * MCP 基于 **JSON-RPC 2.0**，stdio 传输用**行分隔 JSON**（每条消息一行 \n 结尾）。
 * 本文件只做协议编解码 + 消息构造，不依赖 electron/不碰 IO，可在沙箱/CI 单测。
 *
 * 标准方法流：initialize → notifications/initialized → tools/list → tools/call。
 */
"use strict";

const MCP_PROTOCOL_VERSION = "2025-11-25"; // 与后端协商首选版本保持一致
const JSONRPC = "2.0";

// JSON-RPC 标准错误码
const ErrorCode = {
  ParseError: -32700,
  InvalidRequest: -32600,
  MethodNotFound: -32601,
  InvalidParams: -32602,
  InternalError: -32603,
};

function makeRequest(id, method, params) {
  const m = { jsonrpc: JSONRPC, id, method };
  if (params !== undefined) m.params = params;
  return m;
}
function makeNotification(method, params) {
  const m = { jsonrpc: JSONRPC, method };
  if (params !== undefined) m.params = params;
  return m;
}
function makeResponse(id, result) {
  return { jsonrpc: JSONRPC, id, result };
}
function makeError(id, code, message, data) {
  const err = { code, message: String(message || "") };
  if (data !== undefined) err.data = data;
  return { jsonrpc: JSONRPC, id: id === undefined ? null : id, error: err };
}

/** 一条消息 → 行分隔 JSON 文本（stdio 传输单位）。 */
function encode(message) {
  return JSON.stringify(message) + "\n";
}

/**
 * 把累积的 stdio 缓冲按行切出完整消息。返回 { messages, rest }，rest 是尚未收全的
 * 残行（下次拼接）。坏行（非法 JSON）跳过但记入 errors，避免一条坏消息卡死整条流。
 */
function decode(buffer) {
  const messages = [];
  const errors = [];
  const parts = String(buffer == null ? "" : buffer).split("\n");
  const rest = parts.pop(); // 最后一段可能不完整，留到下次
  for (const line of parts) {
    const s = line.trim();
    if (!s) continue;
    try { messages.push(JSON.parse(s)); }
    catch (e) { errors.push({ line: s, error: (e && e.message) || String(e) }); }
  }
  return { messages, rest: rest || "", errors };
}

/** 是否是一条合法 JSON-RPC 消息（请求/通知/响应）。 */
function isValidMessage(m) {
  if (!m || typeof m !== "object" || m.jsonrpc !== JSONRPC) return false;
  const isReq = typeof m.method === "string";
  const isResp = ("result" in m || "error" in m) && ("id" in m);
  return isReq || isResp;
}

/** 是请求（有 id 且有 method）。通知是有 method 无 id。 */
function isRequest(m) { return typeof m.method === "string" && "id" in m && m.id !== undefined && m.id !== null; }
function isNotification(m) { return typeof m.method === "string" && !("id" in m); }

module.exports = {
  MCP_PROTOCOL_VERSION, JSONRPC, ErrorCode,
  makeRequest, makeNotification, makeResponse, makeError,
  encode, decode, isValidMessage, isRequest, isNotification,
};
