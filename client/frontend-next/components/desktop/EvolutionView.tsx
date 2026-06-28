"use client";
/** components/desktop/EvolutionView.tsx — 自我进化（V103.52 改用统一设计系统 PanelKit）。
 *
 * 后端「技能库 + 经验回放」(routes/evolution.py) 入口：技能（可复用流程，可顶/踩/删）+
 * 经验回放（每次问答沉淀的 episode），呼应 exp_rules「越用越聪明」。
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { Sparkles, RefreshCw, CheckCircle2, CircleHelp, XCircle, ThumbsUp, ThumbsDown, Trash2 } from "lucide-react";
import { listEvolutionSkills, listEpisodes, skillFeedback, deleteEvolutionSkill } from "@/lib/api";
import { PanelShell, PageHeader, Card, CardGrid, Button, Badge, StateView, SectionTitle } from "./ui/PanelKit";
import { sortSkills, skillsSummary, filterEpisodes, episodeOutcomeCounts, type SkillRec, type EpisodeRec, type SkillSort, type OutcomeFilter } from "@/lib/skillStats";

type Skill = { id?: string; name?: string; description?: string; quality_score?: number; use_count?: number; trigger_patterns?: string[]; [k: string]: any };
type Episode = { id?: string; query?: string; query_type?: string; strategy?: string; outcome?: string; key_insight?: string; elapsed_ms?: number; created_at?: number };

function outcomeStyle(outcome?: string): { color: string; Icon: any; label: string } {
  const o = (outcome || "").toLowerCase();
  if (["success", "ok", "good", "pass"].includes(o)) return { color: "var(--success)", Icon: CheckCircle2, label: outcome || "成功" };
  if (["fail", "error", "bad"].includes(o)) return { color: "var(--error)", Icon: XCircle, label: outcome || "失败" };
  return { color: "var(--text-tertiary)", Icon: CircleHelp, label: outcome || "未知" };
}

export function EvolutionView() {
  const [skills, setSkills] = useState<Skill[] | undefined>(undefined);
  const [eps, setEps] = useState<Episode[] | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  // V103.90 技能排序 + 经验结果筛选
  const [skillSort, setSkillSort] = useState<SkillSort>("default");
  const [epFilter, setEpFilter] = useState<OutcomeFilter>("all");
  const shownSkills = useMemo(() => sortSkills((skills || []) as SkillRec[], skillSort) as Skill[], [skills, skillSort]);
  const skSummary = useMemo(() => skillsSummary((skills || []) as SkillRec[]), [skills]);
  const epCounts = useMemo(() => episodeOutcomeCounts((eps || []) as EpisodeRec[]), [eps]);
  const shownEps = useMemo(() => filterEpisodes((eps || []) as EpisodeRec[], epFilter) as Episode[], [eps, epFilter]);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [s, e] = await Promise.all([listEvolutionSkills().catch(() => null), listEpisodes(40).catch(() => null)]);
      setSkills(s && Array.isArray(s.skills) ? s.skills : []);
      setEps(e && Array.isArray(e.episodes) ? e.episodes : []);
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const onFeedback = async (id: string | undefined, fb: "up" | "down") => {
    if (!id) return;
    setSkills(arr => arr?.map(s => s.id === id ? { ...s, quality_score: Math.max(0, Math.min(1, (s.quality_score ?? 0) + (fb === "up" ? 0.1 : -0.15))) } : s));
    try { await skillFeedback(id, fb); } catch { load(); }
  };
  const onDeleteSkill = async (id: string | undefined) => {
    if (!id) return;
    setSkills(arr => arr?.filter(s => s.id !== id));
    try { await deleteEvolutionSkill(id); } catch { load(); }
  };

  return (
    <PanelShell>
      <PageHeader icon={Sparkles} title="自我进化"
        subtitle="Agent 的技能库与经验回放 —— 每次问答都会沉淀，让系统越用越聪明"
        actions={<Button icon={RefreshCw} busy={busy} size="sm" onClick={load}>刷新</Button>} />

      <SectionTitle right={skills && skills.length > 0 ? (
        <div className="flex items-center gap-2">
          <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>平均质量 {skSummary.avgQuality}% · 累计用 {skSummary.totalUses}</span>
          <select value={skillSort} onChange={e => setSkillSort(e.target.value as SkillSort)}
            className="h-7 px-1.5 rounded-lg text-[11px] outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            <option value="default">默认</option><option value="quality">按质量</option><option value="usage">按用量</option>
          </select>
          <Badge tone="neutral" mono>{skills.length}</Badge>
        </div>
      ) : (skills && <Badge tone="neutral" mono>{skills.length}</Badge>)}>技能库</SectionTitle>
      <div className="text-[10.5px] -mt-1 mb-3" style={{ color: "var(--text-tertiary)" }}>自动学习的可复用流程 —— 顶有用的、踩没用的、删错的，质量分越高越常被调用</div>
      {skills === undefined && <StateView kind="loading" />}
      {skills !== undefined && skills.length === 0 && <StateView kind="empty" icon={Sparkles} message="暂无已注册技能。" />}
      {skills !== undefined && skills.length > 0 && (
        <CardGrid min={320}>
          {shownSkills.map((s, i) => {
            const q = typeof s.quality_score === "number" ? s.quality_score : 0;
            const qPct = Math.round(q * 100);
            const qColor = q >= 0.6 ? "var(--success)" : q >= 0.3 ? "var(--warning)" : "var(--error)";
            const triggers = Array.isArray(s.trigger_patterns) ? s.trigger_patterns : [];
            return (
              <Card key={s.id || s.name || i} padding="p-4" className="flex flex-col">
                <div className="flex items-start justify-between gap-2">
                  <div className="text-[13px] font-bold flex-1 min-w-0" style={{ color: "var(--text-primary)" }}>{s.name || "（未命名技能）"}</div>
                  {typeof s.use_count === "number" && <span className="text-[10px] font-mono flex-shrink-0 mt-0.5" style={{ color: "var(--text-tertiary)" }}>用 {s.use_count} 次</span>}
                </div>
                {s.description && <div className="text-[11.5px] mt-1 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>{s.description}</div>}
                {triggers.length > 0 && (
                  <div className="flex gap-1 flex-wrap mt-2">
                    {triggers.slice(0, 6).map((t: string, j: number) => <Badge key={j} tone="neutral">{t}</Badge>)}
                  </div>
                )}
                <div className="flex items-center gap-2 mt-2.5">
                  <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>质量</span>
                  <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-tertiary)" }}>
                    <div className="h-full rounded-full transition-all" style={{ width: `${qPct}%`, background: qColor }} />
                  </div>
                  <span className="text-[10px] font-mono" style={{ color: qColor }}>{qPct}%</span>
                </div>
                <div className="flex items-center gap-1.5 mt-2.5 pt-2.5" style={{ borderTop: "1px dashed var(--border)" }}>
                  <button onClick={() => onFeedback(s.id, "up")} disabled={!s.id} title="有用：提高被 Agent 调用的概率"
                    className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)" }}><ThumbsUp size={12} /> 有用</button>
                  <button onClick={() => onFeedback(s.id, "down")} disabled={!s.id} title="没用：降低被调用的概率"
                    className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-secondary)" }}><ThumbsDown size={12} /> 没用</button>
                  <button onClick={() => onDeleteSkill(s.id)} disabled={!s.id} title="删除这条技能" aria-label="删除这条技能"
                    className="ml-auto flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors hover:bg-[color-mix(in_srgb,var(--error)_8%,transparent)]" style={{ color: "var(--text-tertiary)" }}><Trash2 size={12} /></button>
                </div>
              </Card>
            );
          })}
        </CardGrid>
      )}

      <SectionTitle right={eps && <Badge tone="neutral" mono>近 {eps.length} 条</Badge>}>经验回放</SectionTitle>
      {eps !== undefined && eps.length > 0 && (
        <div className="inline-flex rounded-lg overflow-hidden mb-3" style={{ border: "1px solid var(--border)" }}>
          {([["all", `全部 ${epCounts.total}`], ["success", `成功 ${epCounts.success}`], ["fail", `失败 ${epCounts.fail}`], ["unknown", `未知 ${epCounts.unknown}`]] as const).map(([v, label], i) => (
            <button key={v} onClick={() => setEpFilter(v)} className="px-2.5 py-1 text-[11px] transition-colors"
              style={{ background: epFilter === v ? "var(--accent-light)" : "transparent", color: epFilter === v ? "var(--accent)" : "var(--text-tertiary)", borderLeft: i ? "1px solid var(--border)" : "none" }}>{label}</button>
          ))}
        </div>
      )}
      {eps === undefined && <StateView kind="loading" />}
      {eps !== undefined && eps.length === 0 && <StateView kind="empty" icon={Sparkles} message="还没有经验记录。多用 Agent 问答，这里会逐条沉淀「问了什么 · 用了什么策略 · 结果如何 · 学到什么」。" />}
      {eps !== undefined && eps.length > 0 && shownEps.length === 0 && <div className="text-center py-8 text-[12.5px]" style={{ color: "var(--text-tertiary)" }}>该结果下暂无经验</div>}
      {shownEps.length > 0 && (
        <div className="flex flex-col gap-2">
          {shownEps.map((e, i) => {
            const os = outcomeStyle(e.outcome);
            return (
              <Card key={e.id || i} padding="p-4">
                <div className="flex items-start gap-2">
                  <os.Icon size={15} style={{ color: os.color }} className="flex-shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div className="text-[12.5px] font-medium break-words" style={{ color: "var(--text-primary)" }}>{e.query || "（无查询）"}</div>
                    <div className="flex items-center gap-2 flex-wrap mt-1">
                      {e.strategy && <Badge tone="neutral">{e.strategy}</Badge>}
                      {e.query_type && <Badge tone="neutral">{e.query_type}</Badge>}
                      <span className="text-[10px]" style={{ color: os.color }}>{os.label}</span>
                      {typeof e.elapsed_ms === "number" && e.elapsed_ms > 0 && <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{(e.elapsed_ms / 1000).toFixed(1)}s</span>}
                    </div>
                    {e.key_insight && <div className="text-[11.5px] mt-1.5 leading-relaxed pl-2" style={{ color: "var(--text-secondary)", borderLeft: "2px solid var(--accent)" }}>{e.key_insight}</div>}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </PanelShell>
  );
}
