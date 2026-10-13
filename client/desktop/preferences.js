/**
 * desktop/preferences.js — 客户端偏好设置纯逻辑（V103.90）。
 *
 * 主题、字号、发送键等偏好此前没有统一管理。本模块定义偏好模型 + 默认值 + 校验（白名单/裁剪），
 * 并把偏好映射成 CSS 变量覆盖（主题色 / 字号缩放），供渲染层一行应用。
 *
 * 纯函数、不抛异常，便于沙箱单测。真实持久化（存进 hashmm-config.json 的 preferences 字段）由调用方做。
 */
"use strict";

const THEMES = ["dark", "light", "auto"];
const FONT_MIN = 12, FONT_MAX = 20, FONT_DEFAULT = 14;
const DENSITY = ["comfortable", "compact"];

const DEFAULTS = {
  theme: "dark",
  fontSize: FONT_DEFAULT,
  density: "comfortable",
  sendOnEnter: true,          // Enter 发送（false=Enter 换行、Ctrl+Enter 发送）
  renderMarkdown: true,       // 助手消息渲染 Markdown
};

function _clamp(v, lo, hi, dflt) { const n = Number(v); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : dflt; }

/** 规范化偏好：补默认、白名单校验、数值裁剪。 */
function normalize(prefs) {
  const p = prefs || {};
  return {
    theme: THEMES.includes(p.theme) ? p.theme : DEFAULTS.theme,
    fontSize: _clamp(p.fontSize, FONT_MIN, FONT_MAX, FONT_DEFAULT),
    density: DENSITY.includes(p.density) ? p.density : DEFAULTS.density,
    sendOnEnter: p.sendOnEnter !== undefined ? !!p.sendOnEnter : DEFAULTS.sendOnEnter,
    renderMarkdown: p.renderMarkdown !== undefined ? !!p.renderMarkdown : DEFAULTS.renderMarkdown,
  };
}

// 深色 / 浅色主题的 CSS 变量值（与 app 既有变量名对齐）
const DARK = {
  "--bg-primary": "#1a1a1e", "--bg-secondary": "#222228", "--bg-tertiary": "#2c2c34",
  "--text-primary": "#ececf1", "--text-secondary": "#b4b4be", "--text-tertiary": "#8a8a96",
  "--border": "#34343c", "--accent": "#6c8cff", "--success": "#34c759", "--danger": "#ff6b6b",
};
const LIGHT = {
  "--bg-primary": "#ffffff", "--bg-secondary": "#f5f5f7", "--bg-tertiary": "#ececef",
  "--text-primary": "#1a1a1e", "--text-secondary": "#54545c", "--text-tertiary": "#86868b",
  "--border": "#d8d8de", "--accent": "#3b6cff", "--success": "#1f9d4d", "--danger": "#e0322f",
};

/**
 * 解析有效主题（auto 按系统暗色偏好落地）。
 * @param theme "dark"|"light"|"auto"
 * @param systemPrefersDark 布尔（auto 时用）
 */
function effectiveTheme(theme, systemPrefersDark) {
  if (theme === "auto") return systemPrefersDark ? "dark" : "light";
  return THEMES.includes(theme) ? theme : "dark";
}

/**
 * 把偏好映射成要写到 :root 的 CSS 变量（含主题色 + 字号缩放 + 行距）。
 * @returns { vars: {cssVar: value}, effectiveTheme }
 */
function cssVariables(prefs, systemPrefersDark) {
  const p = normalize(prefs);
  const eff = effectiveTheme(p.theme, !!systemPrefersDark);
  const palette = eff === "light" ? LIGHT : DARK;
  const vars = Object.assign({}, palette);
  vars["--app-font-size"] = p.fontSize + "px";
  vars["--app-line-height"] = p.density === "compact" ? "1.5" : "1.75";
  return { vars, effectiveTheme: eff };
}

/** 单项更新并规范化（供 UI 改一个选项时用）。 */
function update(prefs, key, value) {
  const p = Object.assign({}, normalize(prefs));
  if (key in DEFAULTS) p[key] = value;
  return normalize(p);
}

module.exports = { THEMES, DENSITY, FONT_MIN, FONT_MAX, DEFAULTS, normalize, effectiveTheme, cssVariables, update };
