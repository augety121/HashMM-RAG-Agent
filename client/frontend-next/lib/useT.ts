"use client";
// lib/useT.ts — 绑定 store 当前语言的翻译 hook。组件里 const t = useT(); t("工作台")。
// 单独成文件（不放 i18n.ts）以保持 i18n.ts 纯净可单测、避免引入 store/zustand 依赖。
import { useStore } from "./store";
import { translate } from "./i18n";

export function useT() {
  const locale = useStore((s) => s.locale);
  return (text: string, params?: Record<string, string | number>) => translate(text, locale, params);
}
