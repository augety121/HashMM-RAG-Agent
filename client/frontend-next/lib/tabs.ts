// lib/tabs.ts — 多标签会话纯逻辑（V103.90，frontend-next 真 UI）。
// 同时打开多个对话、用标签切换。本模块只管标签数组的增删与"关掉当前标签后激活谁"，
// 纯函数、不碰 store/DOM，便于 tsc 编译后单测。

export const MAX_TABS = 12;

/** 把会话 id 加进标签条：已存在则不动；超过上限淘汰最旧的（数组头）。 */
export function addTab(tabs: string[], id: string, max = MAX_TABS): string[] {
  if (!id) return tabs.slice();
  if (tabs.includes(id)) return tabs.slice();
  const next = [...tabs, id];
  return next.length > max ? next.slice(next.length - max) : next;
}

/**
 * 关掉某个标签。若关的是当前激活的，激活相邻标签（优先右、否则左）；否则激活不变。
 * @returns { tabs, nextActive }
 */
export function closeTab(tabs: string[], id: string, activeId: string | null): { tabs: string[]; nextActive: string | null } {
  const idx = tabs.indexOf(id);
  if (idx === -1) return { tabs: tabs.slice(), nextActive: activeId };
  const next = tabs.filter((t) => t !== id);
  if (activeId !== id) return { tabs: next, nextActive: activeId };  // 关的不是当前→激活不变
  if (next.length === 0) return { tabs: next, nextActive: null };
  // 关的是当前：优先右邻（原 idx 处现在是右邻），否则取末尾（左邻）
  const nextActive = next[idx] ?? next[next.length - 1];
  return { tabs: next, nextActive };
}

/** 左/右切换激活标签（环绕）。 */
export function adjacentTab(tabs: string[], activeId: string | null, delta: number): string | null {
  if (!tabs.length) return null;
  const idx = activeId ? tabs.indexOf(activeId) : -1;
  if (idx === -1) return tabs[0];
  const n = (idx + delta + tabs.length) % tabs.length;
  return tabs[n];
}

/** 同步：sid 变成非空时确保它在标签里（每处设 sid 都自动开标签）。 */
export function ensureTab(tabs: string[], sid: string | null, max = MAX_TABS): string[] {
  if (!sid) return tabs.slice();
  return addTab(tabs, sid, max);
}

/** 删除会话时，把它从标签条也摘掉（连带算出新激活）。 */
export function pruneTab(tabs: string[], deletedId: string, activeId: string | null): { tabs: string[]; nextActive: string | null } {
  return closeTab(tabs, deletedId, activeId);
}
