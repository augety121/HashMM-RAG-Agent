"use client";
import { useState, useEffect, useCallback, useMemo } from "react";
import * as api from "@/lib/api";
import { Plus, Trash2, ThumbsUp, ThumbsDown, Zap, Brain, TrendingUp, Search, AlertCircle, RefreshCw } from "lucide-react";
import { sortSkills, filterSkills, type SkillRec, type SkillSort } from "@/lib/skillStats";

interface EvolutionSkill {
  id: string;
  name: string;
  description: string;
  trigger_patterns: string[];
  quality_score: number;
  use_count: number;
  created_at: number;
  prompt_template: string;
}

interface LegacySkill {
  name: string;
  description?: string;
  triggers?: string[];
  tools?: string[];
  _path?: string;
}

export function SkillsTab() {
  const [legacySkills, setLegacySkills] = useState<LegacySkill[]>([]);
  const [evoSkills, setEvoSkills] = useState<EvolutionSkill[]>([]);
  const [tab, setTab] = useState<"auto" | "manual">("auto");
  const [loading, setLoading] = useState({ auto: true, manual: true });
  const [errors, setErrors] = useState<{ auto: string | null; manual: string | null }>({ auto: null, manual: null });
  const [verifiedAt, setVerifiedAt] = useState<{ auto: number | null; manual: number | null }>({ auto: null, manual: null });
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", triggers: "", prompt: "", tools: "kb_search" });
  // V103.90 搜索 / 排序
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState<SkillSort>("default");
  const shownEvo = useMemo(() => sortSkills(filterSkills(evoSkills as SkillRec[], query), sortBy) as typeof evoSkills, [evoSkills, query, sortBy]);
  const shownLegacy = useMemo(() => filterSkills(legacySkills as SkillRec[], query) as typeof legacySkills, [legacySkills, query]);

  const loadLegacy = useCallback(async () => {
    setLoading(prev => ({ ...prev, manual: true }));
    try {
      const r = await api.listSkills(); setLegacySkills(r.skills || []);
      setErrors(prev => ({ ...prev, manual: null })); setVerifiedAt(prev => ({ ...prev, manual: Date.now() }));
    } catch (reason) {
      setErrors(prev => ({ ...prev, manual: reason instanceof Error ? reason.message : "手动技能读取失败" }));
    } finally { setLoading(prev => ({ ...prev, manual: false })); }
  }, []);

  const loadEvolution = useCallback(async () => {
    setLoading(prev => ({ ...prev, auto: true }));
    try {
      const r = await api.listEvolutionSkills(); setEvoSkills(r.skills || []);
      setErrors(prev => ({ ...prev, auto: null })); setVerifiedAt(prev => ({ ...prev, auto: Date.now() }));
    } catch (reason) {
      setErrors(prev => ({ ...prev, auto: reason instanceof Error ? reason.message : "自动技能读取失败" }));
    } finally { setLoading(prev => ({ ...prev, auto: false })); }
  }, []);

  useEffect(() => { loadLegacy(); loadEvolution(); }, [loadLegacy, loadEvolution]);

  async function handleCreate() {
    if (!form.name.trim() || busyKey) return;
    setBusyKey("create"); setErrors(prev => ({ ...prev, manual: null }));
    try {
      await api.createSkill({ name: form.name, description: form.description, triggers: form.triggers.split(",").map(t => t.trim()).filter(Boolean), prompt: form.prompt, tools: form.tools.split(",").map(t => t.trim()).filter(Boolean) });
      setForm({ name: "", description: "", triggers: "", prompt: "", tools: "kb_search" }); setShowAdd(false); await loadLegacy();
    } catch (reason) { setErrors(prev => ({ ...prev, manual: reason instanceof Error ? reason.message : "技能创建失败" })); }
    finally { setBusyKey(null); }
  }

  async function handleDeleteLegacy(name: string) {
    if (!confirm(`删除技能 "${name}"?`)) return;
    if (busyKey) return; setBusyKey(`manual:${name}`);
    try { await api.deleteSkill(name); await loadLegacy(); }
    catch (reason) { setErrors(prev => ({ ...prev, manual: reason instanceof Error ? reason.message : "技能删除失败" })); }
    finally { setBusyKey(null); }
  }

  async function handleDeleteEvo(id: string) {
    if (!confirm("删除此自动创建的技能?")) return;
    if (busyKey) return; setBusyKey(`auto:${id}`);
    try { await api.deleteEvolutionSkill(id); await loadEvolution(); }
    catch (reason) { setErrors(prev => ({ ...prev, auto: reason instanceof Error ? reason.message : "自动技能删除失败" })); }
    finally { setBusyKey(null); }
  }

  async function handleEvoFeedback(id: string, fb: "up" | "down") {
    if (busyKey) return; setBusyKey(`feedback:${id}`);
    try { await api.skillFeedback(id, fb); await loadEvolution(); }
    catch (reason) { setErrors(prev => ({ ...prev, auto: reason instanceof Error ? reason.message : "反馈未取得服务端回执" })); }
    finally { setBusyKey(null); }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>技能管理</h4>
          <p className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
            自动创建的技能来自用户正面反馈，手动技能由管理员配置
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
            <button onClick={() => setTab("auto")} className="px-3 py-1 text-[11px] font-medium transition-colors"
              style={{ background: tab === "auto" ? "var(--accent-light)" : "transparent", color: tab === "auto" ? "var(--accent)" : "var(--text-tertiary)" }}>
              <Brain size={12} className="inline mr-1" /> 自动 ({evoSkills.length})
            </button>
            <button onClick={() => setTab("manual")} className="px-3 py-1 text-[11px] font-medium transition-colors"
              style={{ background: tab === "manual" ? "var(--accent-light)" : "transparent", color: tab === "manual" ? "var(--accent)" : "var(--text-tertiary)" }}>
              <Zap size={12} className="inline mr-1" /> 手动 ({legacySkills.length})
            </button>
          </div>
          {tab === "manual" && (
            <button onClick={() => setShowAdd(!showAdd)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white" style={{ background: "var(--accent)" }}>
              <Plus size={14} /> 新建
            </button>
          )}
        </div>
      </div>

      {errors[tab] && <div className="mb-4 flex items-start gap-3 rounded-xl px-4 py-3" style={{ background: "rgba(180,35,24,.06)", border: "1px solid rgba(180,35,24,.16)" }}><AlertCircle size={15} className="mt-0.5 flex-shrink-0" style={{ color: "#b42318" }} /><div className="flex-1"><div className="text-[12px]" style={{ color: "#b42318" }}>{errors[tab]}</div><div className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{(tab === "auto" ? evoSkills : legacySkills).length ? "当前显示最近一次成功读取结果。" : "当前没有取得可验证的技能列表。"}</div></div><button disabled={loading[tab]} onClick={tab === "auto" ? loadEvolution : loadLegacy} className="inline-flex items-center gap-1 text-[11px]" style={{ color: "var(--accent)" }}><RefreshCw size={12} />重试</button></div>}
      {verifiedAt[tab] && !errors[tab] && <div className="mb-3 text-[10px]" style={{ color: "var(--text-tertiary)" }}>本次已验证 · {new Date(verifiedAt[tab]!).toLocaleTimeString()}</div>}

      {/* V103.90 搜索 / 排序 */}
      {((tab === "auto" && evoSkills.length > 0) || (tab === "manual" && legacySkills.length > 0)) && (
        <div className="flex items-center gap-2 flex-wrap mb-3">
          <div className="relative flex-1 min-w-[160px]">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索技能名 / 描述 / 触发词…"
              className="w-full text-[12px] pl-8 pr-3 py-1.5 rounded-lg outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          </div>
          {tab === "auto" && (
            <select value={sortBy} onChange={e => setSortBy(e.target.value as SkillSort)}
              className="h-8 px-2 rounded-lg text-[12px] outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
              <option value="default">默认排序</option><option value="quality">按质量分</option><option value="usage">按用量</option>
            </select>
          )}
        </div>
      )}

      {/* Manual skill creation form */}
      {showAdd && tab === "manual" && (
        <div className="mb-4 p-4 rounded-xl space-y-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="技能名称" className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="描述" className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <input value={form.triggers} onChange={e => setForm({ ...form, triggers: e.target.value })} placeholder="触发词（逗号分隔）" className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <textarea value={form.prompt} onChange={e => setForm({ ...form, prompt: e.target.value })} placeholder="技能 Prompt" rows={3} className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <div className="flex gap-2">
            <button disabled={!!busyKey} onClick={handleCreate} className="px-3 py-1.5 rounded-lg text-xs font-medium text-white disabled:opacity-50" style={{ background: "var(--accent)" }}>{busyKey === "create" ? "等待回执…" : "创建"}</button>
            <button onClick={() => setShowAdd(false)} className="px-3 py-1.5 rounded-lg text-xs" style={{ color: "var(--text-tertiary)" }}>取消</button>
          </div>
        </div>
      )}

      {/* Auto-created skills (evolution engine) */}
      {tab === "auto" && (
        <div className="space-y-2">
          {!loading.auto && evoSkills.length === 0 && !errors.auto && (
            <div className="text-center py-12">
              <Brain size={36} style={{ color: "var(--text-tertiary)", opacity: 0.3 }} className="mx-auto mb-3" />
              <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>暂无自动创建的技能</p>
              <p className="text-[11px] mt-1" style={{ color: "var(--text-tertiary)" }}>
                当用户对复杂回答给出正面反馈时，系统会自动提取可复用的"解题模式"
              </p>
            </div>
          )}
          {evoSkills.length > 0 && shownEvo.length === 0 && <div className="text-center py-8 text-[13px]" style={{ color: "var(--text-tertiary)" }}>没有匹配的技能</div>}
          {shownEvo.map(s => (
            <div key={s.id} className="p-4 rounded-xl" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-[13px]" style={{ color: "var(--text-primary)" }}>{s.name}</span>
                    <QualityBadge score={s.quality_score} />
                  </div>
                  <div className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{s.description}</div>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                  <button disabled={!!busyKey} onClick={() => handleEvoFeedback(s.id, "up")} className="p-1 rounded-md hover:bg-green-50 dark:hover:bg-green-950/20 disabled:opacity-40" title="提升质量分">
                    <ThumbsUp size={12} style={{ color: "#22c55e" }} />
                  </button>
                  <button disabled={!!busyKey} onClick={() => handleEvoFeedback(s.id, "down")} className="p-1 rounded-md hover:bg-red-50 dark:hover:bg-red-950/20 disabled:opacity-40" title="降低质量分">
                    <ThumbsDown size={12} style={{ color: "#ef4444" }} />
                  </button>
                  <button disabled={!!busyKey} onClick={() => handleDeleteEvo(s.id)} className="p-1 rounded-md hover:bg-red-100 dark:hover:bg-red-950/30 disabled:opacity-40">
                    <Trash2 size={12} className="text-red-400" />
                  </button>
                </div>
              </div>
              <div className="flex flex-wrap gap-1 mt-2">
                {s.trigger_patterns.map(t => (
                  <span key={t} className="px-2 py-0.5 rounded-md text-[10px]" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{t}</span>
                ))}
              </div>
              <div className="flex items-center gap-3 mt-2 text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                <span><TrendingUp size={10} className="inline mr-0.5" /> 使用 {s.use_count} 次</span>
                <span>创建于 {new Date(s.created_at * 1000).toLocaleDateString()}</span>
              </div>
              {s.prompt_template && (
                <div className="mt-2 px-3 py-2 rounded-lg text-[11px] font-mono leading-relaxed" style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
                  {s.prompt_template.slice(0, 200)}{s.prompt_template.length > 200 ? "..." : ""}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Manual skills */}
      {tab === "manual" && (
        <div className="space-y-2">
          {legacySkills.length > 0 && shownLegacy.length === 0 && <div className="text-center py-8 text-[13px]" style={{ color: "var(--text-tertiary)" }}>没有匹配的技能</div>}
          {shownLegacy.map(s => (
            <div key={s.name} className="p-4 rounded-xl transition-colors" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-semibold text-[13px]" style={{ color: "var(--text-primary)" }}>{s.name}</div>
                  <div className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{s.description}</div>
                </div>
                <button disabled={!!busyKey} onClick={() => handleDeleteLegacy(s.name)} className="p-1 rounded-md hover:bg-red-100 disabled:opacity-40"><Trash2 size={14} className="text-red-400" /></button>
              </div>
              <div className="flex flex-wrap gap-1 mt-2">
                {(s.triggers || []).map(t => <span key={t} className="px-2 py-0.5 rounded-md text-[10px]" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{t}</span>)}
              </div>
            </div>
          ))}
          {!loading.manual && legacySkills.length === 0 && !errors.manual && <div className="text-center py-8 text-sm" style={{ color: "var(--text-tertiary)" }}>暂无手动技能</div>}
        </div>
      )}
    </div>
  );
}

function QualityBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 70 ? "#22c55e" : pct >= 40 ? "#f59e0b" : "#ef4444";
  return (
    <span className="px-1.5 py-0.5 rounded text-[10px] font-bold"
      style={{ background: `${color}15`, color }}>
      {pct}%
    </span>
  );
}
