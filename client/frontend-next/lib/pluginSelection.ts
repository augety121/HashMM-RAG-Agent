"use client";

const KEY = "hmm_chat_plugins_v1";
const EVENT = "hmm-chat-plugins-changed";

export function readSelectedPluginIds(): string[] | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = localStorage.getItem(KEY);
    if (raw == null) return undefined;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return [...new Set(parsed
      .filter((item): item is string => typeof item === "string")
      .map(item => item.trim())
      .filter(item => /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(item))
    )].slice(0, 32);
  } catch {
    return [];
  }
}

export function writeSelectedPluginIds(ids: string[]): void {
  if (typeof window === "undefined") return;
  const normalized = [...new Set(ids
    .map(item => item.trim())
    .filter(item => /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(item))
  )].slice(0, 32);
  localStorage.setItem(KEY, JSON.stringify(normalized));
  window.dispatchEvent(new CustomEvent(EVENT, { detail: normalized }));
}

export function onSelectedPluginIdsChanged(listener: (ids: string[]) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<string[]>).detail;
    listener(Array.isArray(detail) ? detail : []);
  };
  window.addEventListener(EVENT, handler);
  return () => window.removeEventListener(EVENT, handler);
}

