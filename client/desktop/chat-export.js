/**
 * desktop/chat-export.js — 对话导出纯逻辑（V103.90）。
 *
 * 把一段对话导出成 Markdown / 纯文本（PDF 由主进程用渲染后的 HTML 走 printToPDF）。
 * 处理角色标注、多模态消息、时间戳、安全文件名。纯函数、不碰 IO，便于沙箱单测。
 */
"use strict";

function _msgText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const parts = [];
    for (const p of content) {
      if (!p) continue;
      if (p.type === "text" && p.text) parts.push(p.text);
      else if (p.type === "image_url" || p.type === "image") parts.push("![图片](已附截图)");
    }
    return parts.join("\n");
  }
  return "";
}

/** 会话 → Markdown。conv: {title, messages:[{role,content}], createdAt?} */
function toMarkdown(conv, opts) {
  conv = conv || {}; opts = opts || {};
  const title = conv.title || "HashMM 对话";
  const lines = ["# " + title, ""];
  if (conv.createdAt || conv.updatedAt) {
    const d = new Date(conv.updatedAt || conv.createdAt);
    if (!isNaN(d.getTime())) lines.push("> 导出时间：" + d.toLocaleString(), "");
  }
  const msgs = Array.isArray(conv.messages) ? conv.messages : [];
  for (const m of msgs) {
    if (!m || !m.role) continue;
    if (m.role === "system" && !opts.includeSystem) continue;
    const who = m.role === "user" ? "你" : m.role === "assistant" ? "助手" : "系统";
    lines.push("## " + who, "", _msgText(m.content) || "", "");
  }
  return lines.join("\n").replace(/\n{4,}/g, "\n\n\n").trim() + "\n";
}

/** 会话 → 纯文本。 */
function toPlainText(conv, opts) {
  conv = conv || {}; opts = opts || {};
  const msgs = Array.isArray(conv.messages) ? conv.messages : [];
  const out = [(conv.title || "HashMM 对话"), "=".repeat(20), ""];
  for (const m of msgs) {
    if (!m || !m.role) continue;
    if (m.role === "system" && !opts.includeSystem) continue;
    const who = m.role === "user" ? "你" : m.role === "assistant" ? "助手" : "系统";
    out.push("【" + who + "】", _msgText(m.content) || "", "");
  }
  return out.join("\n").trim() + "\n";
}

/** 安全文件名（去非法字符、限长、补扩展名）。 */
function exportFilename(title, ext) {
  let base = String(title || "对话").replace(/[\\/:*?"<>|\u0000-\u001f]/g, "").replace(/\s+/g, "_").trim();
  if (!base) base = "对话";
  if (base.length > 50) base = base.slice(0, 50);
  const e = String(ext || "md").replace(/^\./, "");
  return base + "." + e;
}

module.exports = { toMarkdown, toPlainText, exportFilename };
