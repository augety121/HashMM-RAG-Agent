// lib/messageSearch.ts — 会话内消息搜索纯逻辑（V103.90，frontend-next 真 UI）。
// 在当前对话里查关键词、定位到第几条消息、跨消息上一个/下一个。大小写不敏感。
// 纯函数、不碰 DOM/React，便于用 tsc 编译后单测。

import type { Message } from "./types";

export interface MatchRange { start: number; end: number; }
export interface MsgHit { msgIndex: number; count: number; snippet: string; }
export interface SearchResult { totalMatches: number; hits: MsgHit[]; flat: Array<{ msgIndex: number; matchIndex: number }>; }

/** 取消息纯文本（content 始终是 string，但去掉旧附件占位行更干净）。 */
function msgText(m: Message): string {
  const c = typeof m?.content === "string" ? m.content : "";
  return c.split("\n").filter((l) => !l.startsWith("\u{1F4CE} ")).join("\n");
}

/** 在 text 里找 query 的所有出现（大小写不敏感，不重叠）。 */
export function findMatches(text: string, query: string): MatchRange[] {
  const t = String(text || "");
  const q = String(query || "");
  if (!q) return [];
  const tl = t.toLowerCase();
  const ql = q.toLowerCase();
  const out: MatchRange[] = [];
  let i = 0;
  for (;;) {
    const idx = tl.indexOf(ql, i);
    if (idx === -1) break;
    out.push({ start: idx, end: idx + q.length });
    i = idx + q.length;
  }
  return out;
}

/** 命中周围的片段（结果列表预览），命中词居中。 */
export function snippet(text: string, query: string, radius = 28): string {
  const t = String(text || "");
  const m = findMatches(t, query)[0];
  if (!m) return t.slice(0, radius * 2);
  const start = Math.max(0, m.start - radius);
  const end = Math.min(t.length, m.end + radius);
  return (start > 0 ? "…" : "") + t.slice(start, end).replace(/\n/g, " ") + (end < t.length ? "…" : "");
}

/** 搜索整段对话。 */
export function searchMessages(messages: Message[], query: string): SearchResult {
  const arr = Array.isArray(messages) ? messages : [];
  const q = String(query || "").trim();
  const hits: MsgHit[] = [];
  const flat: Array<{ msgIndex: number; matchIndex: number }> = [];
  if (!q) return { totalMatches: 0, hits, flat };
  arr.forEach((m, idx) => {
    const text = msgText(m);
    const ms = findMatches(text, q);
    if (ms.length) {
      hits.push({ msgIndex: idx, count: ms.length, snippet: snippet(text, q) });
      ms.forEach((_, mi) => flat.push({ msgIndex: idx, matchIndex: mi }));
    }
  });
  return { totalMatches: flat.length, hits, flat };
}

/** 上一个/下一个匹配的全局序号（环绕）。空集返回 -1。 */
export function stepHit(current: number, delta: number, total: number): number {
  if (!total) return -1;
  const n = ((Number(current) || 0) + (Number(delta) || 0)) % total;
  return n < 0 ? n + total : n;
}
