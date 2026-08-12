/**
 * desktop/attachment-manager.js — 附件管理纯逻辑（V103.90）。
 *
 * 拖文件进对话：图片走多模态视觉、文本/代码文件读内容拼进上下文、其他给文件卡片。
 * 本模块负责文件分类、大小校验、展示标签、文本附件上下文组装。纯函数、不读文件，便于沙箱单测。
 */
"use strict";

const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp"]);
const TEXT_EXT = new Set(["txt", "md", "markdown", "json", "csv", "tsv", "log", "xml", "yaml", "yml",
  "js", "ts", "jsx", "tsx", "py", "java", "c", "cpp", "h", "go", "rs", "rb", "php", "sh", "sql", "html", "css", "vue"]);

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;   // 10MB
const MAX_TEXT_BYTES = 512 * 1024;          // 512KB（文本附件，避免撑爆上下文）

function _ext(name) {
  const m = String(name || "").toLowerCase().match(/\.([a-z0-9]+)$/);
  return m ? m[1] : "";
}

/** 分类文件。 @returns { kind:"image"|"text"|"other", ext, icon, canInline } */
function classifyFile(file) {
  file = file || {};
  const ext = _ext(file.name);
  const type = String(file.type || "").toLowerCase();
  if (IMAGE_EXT.has(ext) || type.startsWith("image/")) return { kind: "image", ext, icon: "IMG", canInline: true };
  if (TEXT_EXT.has(ext) || type.startsWith("text/")) return { kind: "text", ext, icon: "TXT", canInline: true };
  return { kind: "other", ext, icon: "FILE", canInline: false };
}

/** 校验附件是否可加。 @returns { ok, reason, kind } */
function validateAttachment(file, opts) {
  opts = opts || {};
  const cls = classifyFile(file);
  const size = Number(file && file.size) || 0;
  if (cls.kind === "image") {
    if (size > (opts.maxImage || MAX_IMAGE_BYTES)) return { ok: false, kind: cls.kind, reason: "图片过大（>10MB）" };
    return { ok: true, kind: cls.kind };
  }
  if (cls.kind === "text") {
    if (size > (opts.maxText || MAX_TEXT_BYTES)) return { ok: false, kind: cls.kind, reason: "文本文件过大（>512KB），请精简后再试" };
    return { ok: true, kind: cls.kind };
  }
  return { ok: false, kind: "other", reason: "暂不支持该文件类型（仅图片与文本/代码）" };
}

/** 人类可读大小。 */
function humanSize(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

/** 展示标签：文件名 + 大小。 */
function attachmentLabel(file) {
  const name = String((file && file.name) || "文件");
  const short = name.length > 28 ? name.slice(0, 14) + "…" + name.slice(-10) : name;
  return { name, short, size: humanSize(file && file.size), icon: classifyFile(file).icon };
}

/**
 * 把多个文本附件组装成喂给模型的上下文块（带文件名分隔），每个内容截断到 maxEach 字。
 * @param files [{name, content}]
 */
function buildTextContext(files, opts) {
  opts = opts || {};
  const arr = Array.isArray(files) ? files : [];
  if (!arr.length) return "";
  const maxEach = opts.maxEach || 8000;
  const blocks = arr.map(f => {
    const name = (f && f.name) || "文件";
    let content = String((f && f.content) || "");
    if (content.length > maxEach) content = content.slice(0, maxEach) + "\n…（内容过长已截断）";
    return `【附件：${name}】\n${content}`;
  });
  return "以下是用户附带的文件内容，回答时参考：\n\n" + blocks.join("\n\n---\n\n");
}

module.exports = {
  IMAGE_EXT, TEXT_EXT, MAX_IMAGE_BYTES, MAX_TEXT_BYTES,
  classifyFile, validateAttachment, humanSize, attachmentLabel, buildTextContext,
};
