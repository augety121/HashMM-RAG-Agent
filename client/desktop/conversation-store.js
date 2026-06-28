/**
 * desktop/conversation-store.js — 对话历史持久化纯逻辑（V103.90）。
 *
 * 对话此前只在内存（hist 数组），关掉应用就没了。本模块提供会话的纯数据管理：
 * 会话模型、列表增改删、从首条用户消息派生标题、超量裁剪、序列化/反序列化。
 * 真实落盘（userData/hashmm-conversations.json）由调用方经 IPC 做。
 *
 * 纯函数、不抛异常，便于沙箱单测。
 */
"use strict";

const MAX_CONVERSATIONS = 50;          // 最多保留 50 个会话（超出删最旧）
const MAX_TITLE_LEN = 30;

function newId() {
  return "c_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 7);
}

/** 从消息列表派生标题（取首条用户消息前若干字）。 */
function deriveTitle(messages) {
  const arr = Array.isArray(messages) ? messages : [];
  const firstUser = arr.find(m => m && m.role === "user");
  let raw = "";
  if (firstUser) {
    raw = typeof firstUser.content === "string"
      ? firstUser.content
      : (Array.isArray(firstUser.content) ? (firstUser.content.find(p => p && p.type === "text") || {}).text || "" : "");
  }
  raw = String(raw || "").replace(/\s+/g, " ").trim();
  if (!raw) return "新对话";
  return raw.length > MAX_TITLE_LEN ? raw.slice(0, MAX_TITLE_LEN) + "…" : raw;
}

/** 新建会话对象。 */
function createConversation(messages, now) {
  const ts = now == null ? Date.now() : now;
  const msgs = Array.isArray(messages) ? messages : [];
  return { id: newId(), title: deriveTitle(msgs), messages: msgs, createdAt: ts, updatedAt: ts };
}

/**
 * 插入或更新一个会话（按 id）。返回新的会话数组（最近更新的在前）。
 * 若 conv 无 id 视为新会话；标题为空则自动派生。超量裁剪。
 */
function upsert(list, conv, now) {
  const ts = now == null ? Date.now() : now;
  const arr = Array.isArray(list) ? list.slice() : [];
  const c = Object.assign({}, conv);
  if (!c.id) c.id = newId();
  if (!c.title) c.title = deriveTitle(c.messages);
  c.updatedAt = ts;
  if (!c.createdAt) c.createdAt = ts;
  const idx = arr.findIndex(x => x && x.id === c.id);
  if (idx >= 0) arr[idx] = c; else arr.push(c);
  // 最近更新在前
  arr.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  return prune(arr);
}

/** 删除会话。 */
function remove(list, id) {
  return (Array.isArray(list) ? list : []).filter(x => x && x.id !== id);
}

/** 重命名会话。 */
function rename(list, id, title) {
  const t = String(title || "").trim().slice(0, MAX_TITLE_LEN) || "未命名";
  return (Array.isArray(list) ? list : []).map(x => (x && x.id === id) ? Object.assign({}, x, { title: t }) : x);
}

/** 取某会话。 */
function getById(list, id) {
  return (Array.isArray(list) ? list : []).find(x => x && x.id === id) || null;
}

/** 超量裁剪（保留最近 MAX_CONVERSATIONS 个）。 */
function prune(list, max) {
  const m = max || MAX_CONVERSATIONS;
  const arr = Array.isArray(list) ? list : [];
  if (arr.length <= m) return arr;
  return arr.slice().sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0)).slice(0, m);
}

/** 序列化为字符串（落盘）。 */
function serialize(list) {
  try { return JSON.stringify({ v: 1, conversations: prune(Array.isArray(list) ? list : []) }); }
  catch (_e) { return JSON.stringify({ v: 1, conversations: [] }); }
}

/** 反序列化（容错：坏数据→空）。 */
function deserialize(input) {
  let obj = input;
  if (typeof input === "string") { try { obj = JSON.parse(input); } catch (_e) { return []; } }
  if (!obj || !Array.isArray(obj.conversations)) return [];
  return obj.conversations.filter(c => c && c.id && Array.isArray(c.messages));
}

/** 列表摘要（供侧栏渲染，不含完整 messages）。 */
function listSummary(list) {
  return (Array.isArray(list) ? list : []).map(c => ({
    id: c.id, title: c.title || deriveTitle(c.messages),
    count: Array.isArray(c.messages) ? c.messages.length : 0,
    updatedAt: c.updatedAt || c.createdAt || 0,
  }));
}

module.exports = {
  MAX_CONVERSATIONS, newId, deriveTitle, createConversation,
  upsert, remove, rename, getById, prune, serialize, deserialize, listSummary,
};
