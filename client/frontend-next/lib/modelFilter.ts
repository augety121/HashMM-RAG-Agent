// lib/modelFilter.ts — 模型配置：搜索/服务商过滤/统计纯逻辑（V103.90，frontend-next 真 UI）。
// 给后台模型配置列表加大厂级深度（按关键词/服务商过滤、服务商分布）。纯函数便于单测。

export interface ModelRec { id: string; name?: string; provider?: string; model_name?: string; base_url?: string; is_default?: boolean | number; }

/** 按关键词（配置名/模型名/Base URL，大小写不敏感）+ 服务商过滤。 */
export function filterModels(models: ModelRec[], opts: { query?: string; provider?: string } = {}): ModelRec[] {
  const arr = Array.isArray(models) ? models : [];
  const q = String(opts.query || "").trim().toLowerCase();
  const prov = opts.provider || "all";
  return arr.filter((m) => {
    if (prov !== "all" && (m.provider || "") !== prov) return false;
    if (q) {
      const hay = `${m.name || ""} ${m.model_name || ""} ${m.base_url || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

/** 出现过的服务商（去重排序），供筛选。 */
export function distinctProviders(models: ModelRec[]): string[] {
  const set = new Set<string>();
  for (const m of (Array.isArray(models) ? models : [])) if (m.provider) set.add(m.provider);
  return [...set].sort();
}

export interface ModelStats { total: number; providers: number; hasDefault: boolean; }

/** 汇总：总数、服务商种类数、是否已设默认。 */
export function modelStats(models: ModelRec[]): ModelStats {
  const arr = Array.isArray(models) ? models : [];
  return {
    total: arr.length,
    providers: distinctProviders(arr).length,
    hasDefault: arr.some((m) => !!m.is_default),
  };
}

/** 某服务商的配置数。 */
export function countByProvider(models: ModelRec[], provider: string): number {
  return (Array.isArray(models) ? models : []).filter((m) => (m.provider || "") === provider).length;
}
