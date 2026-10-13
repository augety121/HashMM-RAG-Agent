// lib/templateSort.ts — 提示词模板：排序纯逻辑（V103.90，frontend-next 真 UI）。
// 给后台模板列表加排序（按用量/名字/创建时间）。纯函数便于单测。

export interface TemplateRec { id: string; name?: string; category?: string; use_count?: number; created_at?: number; }
export type TemplateSort = "use" | "name" | "recent" | "default";

/** 排序（不改原数组）。use：用量高→低；name：名字字母序；recent：创建新→旧；default：原序。 */
export function sortTemplates(templates: TemplateRec[], by: TemplateSort): TemplateRec[] {
  const arr = (Array.isArray(templates) ? templates : []).slice();
  if (by === "use") {
    arr.sort((a, b) => (b.use_count || 0) - (a.use_count || 0));
  } else if (by === "name") {
    arr.sort((a, b) => (a.name || "").localeCompare(b.name || "", "zh"));
  } else if (by === "recent") {
    arr.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  }
  return arr;
}
