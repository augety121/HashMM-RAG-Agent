/**
 * desktop/services/net-util.js — 网络工具纯函数（V101）。
 *
 * 抽出下载/探测里的易错点，可单测。本次真机崩溃（TypeError: Invalid URL）的根因就是
 * 重定向的 Location 可能是相对路径，直接 new URL(相对) 会抛——resolveRedirect 用基地址
 * 解析成绝对，safeParseUrl 永不抛。
 */
"use strict";

/** 安全解析 URL：失败返回 null 而非抛。base 可选（用于相对地址）。 */
function safeParseUrl(s, base) {
  try { return base ? new URL(s, base) : new URL(s); }
  catch (_) { return null; }
}

/**
 * 把重定向 Location 解析成绝对 URL 字符串。
 * - 绝对地址原样规范化
 * - **相对地址用 baseUrl 作基地址解析**（修 Invalid URL 崩溃）
 * - 非法返回 null
 */
function resolveRedirect(location, baseUrl) {
  if (!location) return null;
  const u = safeParseUrl(location, baseUrl || undefined);
  return u ? u.href : null;
}

/** 按 URL 协议选 http/https 库。 */
function pickProtocolLib(url, libs) {
  const u = typeof url === "string" ? safeParseUrl(url) : url;
  if (!u) return null;
  return u.protocol === "https:" ? libs.https : libs.http;
}

module.exports = { safeParseUrl, resolveRedirect, pickProtocolLib };
