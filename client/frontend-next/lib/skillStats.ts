// lib/skillStats.ts — 自我进化：技能排序/汇总 + 经验回放筛选/分类纯逻辑（V103.90，frontend-next 真 UI）。
// 给自我进化面板加大厂级深度（技能按质量/用量排序与汇总、经验按结果过滤）。
// 纯函数，便于 tsc 编译后单测。

export interface SkillRec { id?: string; name?: string; description?: string; quality_score?: number; use_count?: number; trigger_patterns?: string[]; }
export interface EpisodeRec { id?: string; query?: string; query_type?: string; strategy?: string; outcome?: string; key_insight?: string; elapsed_ms?: number; created_at?: number; }

export type SkillSort = "quality" | "usage" | "default";
export type Outcome = "success" | "fail" | "unknown";
export type OutcomeFilter = "all" | Outcome;

/** 技能按关键词（名字/描述/触发词，大小写不敏感）过滤。 */
export function filterSkills(skills: SkillRec[], query: string): SkillRec[] {
  const arr = Array.isArray(skills) ? skills : [];
  const q = String(query || "").trim().toLowerCase();
  if (!q) return arr.slice();
  return arr.filter((s) => {
    const trig = Array.isArray(s.trigger_patterns) ? s.trigger_patterns.join(" ") : "";
    return `${s.name || ""} ${s.description || ""} ${trig}`.toLowerCase().includes(q);
  });
}

/** 技能排序（不改原数组）。quality：质量分高→低；usage：用量高→低；default：原序。 */
export function sortSkills(skills: SkillRec[], by: SkillSort): SkillRec[] {
  const arr = (Array.isArray(skills) ? skills : []).slice();
  if (by === "quality") {
    arr.sort((a, b) => (b.quality_score ?? 0) - (a.quality_score ?? 0));
  } else if (by === "usage") {
    arr.sort((a, b) => (b.use_count ?? 0) - (a.use_count ?? 0));
  }
  return arr;
}

export interface SkillsSummary { total: number; avgQuality: number; totalUses: number; }

/** 技能汇总：总数、平均质量分(%)、累计调用。 */
export function skillsSummary(skills: SkillRec[]): SkillsSummary {
  const arr = Array.isArray(skills) ? skills : [];
  let qSum = 0, qN = 0, uses = 0;
  for (const s of arr) {
    if (typeof s.quality_score === "number") { qSum += s.quality_score; qN++; }
    uses += s.use_count || 0;
  }
  return { total: arr.length, avgQuality: qN ? Math.round((qSum / qN) * 100) : 0, totalUses: uses };
}

/** 结果分类（与面板 outcomeStyle 口径一致）。 */
export function classifyOutcome(outcome?: string): Outcome {
  const o = String(outcome || "").toLowerCase();
  if (["success", "ok", "good", "pass"].includes(o)) return "success";
  if (["fail", "error", "bad"].includes(o)) return "fail";
  return "unknown";
}

/** 经验按结果过滤。 */
export function filterEpisodes(eps: EpisodeRec[], filter: OutcomeFilter): EpisodeRec[] {
  const arr = Array.isArray(eps) ? eps : [];
  if (filter === "all") return arr.slice();
  return arr.filter((e) => classifyOutcome(e.outcome) === filter);
}

export interface OutcomeCounts { total: number; success: number; fail: number; unknown: number; }

/** 经验结果分布计数。 */
export function episodeOutcomeCounts(eps: EpisodeRec[]): OutcomeCounts {
  const arr = Array.isArray(eps) ? eps : [];
  let success = 0, fail = 0, unknown = 0;
  for (const e of arr) {
    const c = classifyOutcome(e.outcome);
    if (c === "success") success++;
    else if (c === "fail") fail++;
    else unknown++;
  }
  return { total: arr.length, success, fail, unknown };
}
