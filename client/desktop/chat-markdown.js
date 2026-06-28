/**
 * desktop/chat-markdown.js — 轻量安全 Markdown→HTML 渲染器（V103.90，聊天消息渲染用）。
 *
 * 聊天消息现在用 textContent 平铺，LLM 返回的 Markdown（标题/列表/粗体/`代码`/```代码块```）
 * 全成了纯文本，这是聊天 UI 最大的体验短板。本模块把 Markdown 渲染成 HTML，且**XSS 安全**：
 * 先转义所有 HTML、链接只放行 http(s)/mailto、不产出任何脚本或事件属性。
 *
 * 无第三方依赖（不引 marked/highlight.js，保持瘦客户端），纯函数 string→string，可沙箱单测。
 * 代码高亮交给渲染层 CSS；复制按钮由调用方扫描 <pre><code> 后挂（保持本模块纯净）。
 */
"use strict";

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// 只放行安全的链接协议，挡掉 javascript:/data: 等
function safeHref(url) {
  const u = String(url || "").trim();
  if (/^(https?:\/\/|mailto:)/i.test(u)) return u;
  return "";
}

// 行内格式：在**已转义**的文本上做 代码/粗斜体/链接（顺序：先行内代码占位，避免其内部被再格式化）
function renderInline(escaped) {
  const codes = [];
  let s = escaped.replace(/`([^`]+)`/g, (_m, c) => {
    codes.push(c);
    return "\u0000C" + (codes.length - 1) + "\u0000";
  });
  // 链接 [text](url)
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, text, url) => {
    const href = safeHref(url.replace(/&amp;/g, "&"));
    if (!href) return text;
    return `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${text}</a>`;
  });
  // 粗体 **x** / __x__
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+)__/g, "<strong>$1</strong>");
  // 斜体 *x* / _x_（避免吃掉已处理的粗体残留）
  s = s.replace(/(^|[^*])\*([^*\n]+)\*([^*]|$)/g, "$1<em>$2</em>$3");
  s = s.replace(/(^|[^_])_([^_\n]+)_([^_]|$)/g, "$1<em>$2</em>$3");
  // 删除线 ~~x~~
  s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  // 回填行内代码
  s = s.replace(/\u0000C(\d+)\u0000/g, (_m, i) => `<code>${codes[Number(i)]}</code>`);
  return s;
}

/**
 * 渲染 Markdown 为安全 HTML 字符串。
 * 支持：```围栏代码块```（带语言）、# 标题、- / * / 1. 列表、> 引用、--- 分隔线、
 *       **粗体** *斜体* `行内代码` [链接](url) ~~删除~~、段落与换行。
 */
function renderMarkdown(text) {
  const src = String(text == null ? "" : text);
  const lines = src.split(/\r?\n/);
  const out = [];

  let i = 0;
  let listType = null;        // "ul" | "ol" | null
  function closeList() { if (listType) { out.push(`</${listType}>`); listType = null; } }

  while (i < lines.length) {
    let line = lines[i];

    // 围栏代码块 ```lang
    const fence = line.match(/^\s*```(\w+)?\s*$/);
    if (fence) {
      closeList();
      const lang = fence[1] || "";
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++; // 跳过结束 ```
      const code = escapeHtml(buf.join("\n"));
      const langAttr = lang ? ` class="lang-${escapeHtml(lang)}"` : "";
      const langLabel = lang ? `<div class="code-lang">${escapeHtml(lang)}</div>` : "";
      out.push(`<pre>${langLabel}<code${langAttr}>${code}</code></pre>`);
      continue;
    }

    // 分隔线
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { closeList(); out.push("<hr>"); i++; continue; }

    // 标题 # .. ######
    const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (h) { closeList(); const lvl = h[1].length; out.push(`<h${lvl}>${renderInline(escapeHtml(h[2]))}</h${lvl}>`); i++; continue; }

    // 引用 >
    if (/^\s*>\s?/.test(line)) {
      closeList();
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push(`<blockquote>${renderInline(escapeHtml(buf.join(" ")))}</blockquote>`);
      continue;
    }

    // 无序列表 - / * / +
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) {
      if (listType !== "ul") { closeList(); out.push("<ul>"); listType = "ul"; }
      out.push(`<li>${renderInline(escapeHtml(ul[1]))}</li>`);
      i++; continue;
    }
    // 有序列表 1.
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) {
      if (listType !== "ol") { closeList(); out.push("<ol>"); listType = "ol"; }
      out.push(`<li>${renderInline(escapeHtml(ol[1]))}</li>`);
      i++; continue;
    }

    // 空行 → 关列表、段落分隔
    if (/^\s*$/.test(line)) { closeList(); i++; continue; }

    // 普通段落（把连续非空行并成一段，行内换行用 <br>）
    closeList();
    const para = [line];
    i++;
    while (i < lines.length && !/^\s*$/.test(lines[i]) &&
           !/^\s*```/.test(lines[i]) && !/^\s*#{1,6}\s/.test(lines[i]) &&
           !/^\s*[-*+]\s/.test(lines[i]) && !/^\s*\d+\.\s/.test(lines[i]) &&
           !/^\s*>\s?/.test(lines[i]) && !/^\s*(---|\*\*\*|___)\s*$/.test(lines[i])) {
      para.push(lines[i]); i++;
    }
    out.push(`<p>${para.map(l => renderInline(escapeHtml(l))).join("<br>")}</p>`);
  }
  closeList();
  return out.join("\n");
}

module.exports = { renderMarkdown, escapeHtml, safeHref, renderInline };
