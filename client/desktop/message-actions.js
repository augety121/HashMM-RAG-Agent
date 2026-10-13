/**
 * desktop/message-actions.js — 单条消息操作纯逻辑（V103.90）。
 *
 * 复制 / 引用（把某条消息作为上下文插回输入框）/ 分享（格式化文本）单条消息。
 * 本模块负责文本构建与提取，纯函数、不碰 DOM/剪贴板，便于沙箱单测。
 */
"use strict";

/** 从消息 content 提取纯文本（兼容多模态）。 */
function extractText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.filter(p => p && p.type === "text").map(p => p.text || "").join("\n");
  }
  return "";
}

const ROLE_CN = { user: "你", assistant: "助手", system: "系统" };

/**
 * 构建"引用"文本（插回输入框，作为下一轮上下文）。
 * 形如：> 引用助手：xxxx\n\n  —— 折叠过长引用，去多余空白。
 */
function buildQuote(message, opts) {
  opts = opts || {};
  const text = extractText(message && message.content).replace(/\s+/g, " ").trim();
  if (!text) return "";
  const who = ROLE_CN[message && message.role] || "消息";
  const max = opts.maxLen || 200;
  const clipped = text.length > max ? text.slice(0, max) + "…" : text;
  // 每行加 > 前缀（引用块）
  const quoted = clipped.split("\n").map(l => "> " + l).join("\n");
  return "> 引用" + who + "：\n" + quoted + "\n\n";
}

/** 构建"分享"文本（带角色与可选标题，便于贴到别处）。 */
function buildShareText(message, opts) {
  opts = opts || {};
  const text = extractText(message && message.content).trim();
  const who = ROLE_CN[message && message.role] || "消息";
  const head = opts.title ? `【${opts.title}】\n` : "";
  return head + `${who}：${text}`.trim();
}

/** 复制用的纯文本（就是消息文本本身）。 */
function copyText(message) {
  return extractText(message && message.content);
}

module.exports = { extractText, buildQuote, buildShareText, copyText, ROLE_CN };
