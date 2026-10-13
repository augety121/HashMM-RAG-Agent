/**
 * desktop/tab-manager.js — 多标签会话纯逻辑（V103.90）。
 *
 * 同时开多个对话，每个标签对应一个会话（与 conversation-store 配合）。本模块管理标签状态：
 * 打开（已存在则激活）、关闭（自动选相邻标签）、激活、重命名。纯函数（入状态→出新状态），
 * 不碰 DOM，便于沙箱单测。
 */
"use strict";

const MAX_TABS = 12;

function _clone(state) {
  const s = state || {};
  return { tabs: Array.isArray(s.tabs) ? s.tabs.map(t => ({ id: t.id, title: t.title })) : [], activeId: s.activeId || null };
}

/** 打开标签：已存在则仅激活；否则追加并激活。超过 MAX_TABS 不再加（返回原状态+overflow 标记）。 */
function openTab(state, tab) {
  const s = _clone(state);
  if (!tab || !tab.id) return s;
  const exist = s.tabs.find(t => t.id === tab.id);
  if (exist) { s.activeId = tab.id; if (tab.title) exist.title = tab.title; return s; }
  if (s.tabs.length >= MAX_TABS) { s.overflow = true; return s; }
  s.tabs.push({ id: tab.id, title: tab.title || "新对话" });
  s.activeId = tab.id;
  return s;
}

/** 关闭标签：移除；若关的是当前激活，自动选相邻（优先左侧，否则右侧）；空了 activeId=null。 */
function closeTab(state, id) {
  const s = _clone(state);
  const idx = s.tabs.findIndex(t => t.id === id);
  if (idx === -1) return s;
  const wasActive = s.activeId === id;
  s.tabs.splice(idx, 1);
  if (wasActive) {
    if (!s.tabs.length) s.activeId = null;
    else s.activeId = (s.tabs[idx - 1] || s.tabs[idx] || s.tabs[0]).id;   // 左邻优先
  }
  return s;
}

/** 激活某标签。 */
function setActive(state, id) {
  const s = _clone(state);
  if (s.tabs.find(t => t.id === id)) s.activeId = id;
  return s;
}

/** 重命名标签。 */
function renameTab(state, id, title) {
  const s = _clone(state);
  const t = s.tabs.find(x => x.id === id);
  if (t) t.title = String(title || "").trim() || t.title;
  return s;
}

/** 当前激活标签对象。 */
function getActive(state) {
  const s = state || {};
  return (Array.isArray(s.tabs) ? s.tabs : []).find(t => t.id === s.activeId) || null;
}

/** 激活相邻标签（dir=+1 右 / -1 左，带环绕）。 */
function activateAdjacent(state, dir) {
  const s = _clone(state);
  if (!s.tabs.length) return s;
  const idx = s.tabs.findIndex(t => t.id === s.activeId);
  const d = (Number(dir) || 0);
  let n = ((idx < 0 ? 0 : idx) + d) % s.tabs.length;
  if (n < 0) n += s.tabs.length;
  s.activeId = s.tabs[n].id;
  return s;
}

module.exports = { MAX_TABS, openTab, closeTab, setActive, renameTab, getActive, activateAdjacent };
