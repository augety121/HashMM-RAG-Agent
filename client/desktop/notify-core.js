/**
 * desktop/notify-core.js — 桌面通知与未读纯逻辑（V103.90）。
 *
 * 托盘与最小化到托盘已有，缺的是：作答完成时若窗口没在前台，**弹桌面通知 + 累计未读角标 + 任务栏闪烁**，
 * 用户切走也不漏消息。本模块是这套的纯逻辑核心（该不该通知、未读怎么算、通知文案怎么截），
 * 不碰 Electron/DOM，便于沙箱单测。
 */
"use strict";

/**
 * 是否应该弹通知：仅当「开启通知」且窗口不在前台（未聚焦或不可见）时。
 * @param s { enabled, focused, visible, minimized }
 */
function shouldNotify(s) {
  s = s || {};
  if (s.enabled === false) return false;
  // 窗口聚焦且可见且没最小化 → 用户正在看，不打扰
  const inForeground = !!s.focused && s.visible !== false && !s.minimized;
  return !inForeground;
}

/** 是否应该累计未读（与通知同条件：不在前台才算未读）。 */
function shouldCountUnread(s) {
  return shouldNotify(Object.assign({}, s, { enabled: true }));
}

/** 角标文本：>99 显示 99+，0 显示空（清除角标）。 */
function badgeText(count) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  if (n === 0) return "";
  return n > 99 ? "99+" : String(n);
}

function _clip(s, n) { s = String(s == null ? "" : s).replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n) + "…" : s; }

/**
 * 通知文案：标题固定/可定制，正文取消息前若干字（去多余空白）。
 * @param message 助手回答文本
 * @param opts { title, bodyLen }
 */
function notificationContent(message, opts) {
  opts = opts || {};
  const body = _clip(message, opts.bodyLen || 80) || "新消息";
  return { title: opts.title || "HashMM 有新回复", body };
}

/** 未读累加（纯函数，返回新计数）。 */
function addUnread(current, delta) {
  const c = Math.max(0, Math.floor(Number(current) || 0));
  const d = Math.floor(Number(delta) || 0);
  return Math.max(0, c + (Number.isFinite(d) ? d : 0));
}

module.exports = { shouldNotify, shouldCountUnread, badgeText, notificationContent, addUnread };
