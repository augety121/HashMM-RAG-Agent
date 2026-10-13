"use client";
/** components/desktop/MemoryView.tsx — 记忆中心（V203 重构）。
 *
 * 布局对标大厂"数据管理页"三段式：概览指标 → 工具条 → 内容区。
 * 功能与数据流不变：后端 SQLite（桌面端）+ Supabase user_memory（与 App 同源）
 * 双源合并去重；读 / 删 / 手动"教它记住"，遵循 agent-curated memory 人可审计原则。
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { Brain, Trash2, RefreshCw, Plus, Search, Layers, Gauge, Smartphone, Radar, MessageSquare } from "lucide-react";
import { listMemory, deleteMemory, memoryProfile, addMemory } from "@/lib/api";
import { getMyMemories, addMyMemory, deleteMyMemory } from "@/lib/supabase";
import { PanelShell, PageHeader, Card, CardGrid, Button, Badge, Field, StateView, SectionTitle, StatCard, inputClass, inputStyle } from "./ui/PanelKit";
import { filterGroups, sortGroupItems, memoryStats, memoryCategories, countItems, type MemGroups, type MemSort } from "@/lib/memoryFilter";
import { hubRecall, hubStats, hubRemember } from "@/lib/api";
import { insertContextIntoChat } from "@/lib/contextInsert";

type Mem = { id: string; key: string; value: string; confidence: number; last_used?: number | null };

/** 置信度：三档色的迷你进度点，替代裸百分比小字 */
function ConfidenceDot({ v }: { v: number }) {
  const pct = Math.round((v ?? 0.8) * 100);
  const color = pct >= 70 ? "var(--success)" : pct >= 40 ? "var(--warning)" : "var(--text-tertiary)";
  return (
    <span className="inline-flex items-center gap-1 flex-shrink-0" title={`置信度 ${pct}%`}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />
      <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{pct}%</span>
    </span>
  );
}

export function MemoryView() {
  const [groups, setGroups] = useState<Record<string, Mem[]> | undefined>(undefined);
  const [total, setTotal] = useState(0);
  const [profile, setProfile] = useState<any>(null);
  const [cloudCount, setCloudCount] = useState(0);   // 来自 Supabase（与 App 同源）的条数
  const [busy, setBusy] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [mCat, setMCat] = useState("偏好");
  const [mKey, setMKey] = useState("");
  const [mValue, setMValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [query, setQuery] = useState("");
  const [catFilter, setCatFilter] = useState("all");
  const [sortBy, setSortBy] = useState<MemSort>("default");
  // ── V249 记忆中枢：四路联邦召回（长期记忆·经验回放·画像·图谱实体）+ 快照 ──
  const [hub, setHub] = useState<{ episodic?: number; entities?: number; service?: { count?: number } } | null>(null);
  const [hubHits, setHubHits] = useState<{ kind: string; source: string; text: string; score: number }[] | null>(null);
  const [hubBusy, setHubBusy] = useState(false);
  const [hubNote, setHubNote] = useState("");

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const tok = typeof window !== "undefined" ? localStorage.getItem("hmm_token") : null;
      const [m, p, sb] = await Promise.all([
        listMemory(200).catch(() => null),
        memoryProfile().catch(() => null),
        tok ? getMyMemories(tok).catch(() => null) : Promise.resolve(null),
      ]);
      // 合并：后端 SQLite（桌面端）+ Supabase user_memory（与 App 同源），按 类别|标签|内容 去重。
      const merged: Record<string, Mem[]> = m && m.ok ? { ...(m.groups || {}) } : {};
      let cloudN = 0;
      if (Array.isArray(sb)) {
        const seen = new Set<string>();
        for (const items of Object.values(merged)) for (const it of items) seen.add(`${it.key}|${it.value}`);
        for (const row of sb) {
          const sig = `${row.key}|${row.value}`;
          if (seen.has(sig)) continue;
          seen.add(sig);
          cloudN++;
          const cat = row.category || "其他";
          const lu = row.last_used ? new Date(row.last_used).getTime() / 1000 : null;
          (merged[cat] ||= []).push({ id: row.id, key: row.key, value: row.value, confidence: row.confidence ?? 0.8, last_used: lu });
        }
      }
      setGroups(merged);
      setCloudCount(cloudN);
      const totalCount = Object.values(merged).reduce((s, a) => s + a.length, 0);
      setTotal(totalCount);
      setProfile(p && p.ok ? p : null);
      hubStats().then(h => setHub(h && h.ok ? h : null)).catch(() => setHub(null));   // 老后端 404 → 卡片不显示
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const doRecall = async () => {
    const q = query.trim();
    if (!q) { setHubNote("先在搜索框输入要召回的内容"); setHubHits(null); return; }
    setHubBusy(true); setHubNote("");
    try {
      const r = await hubRecall(q, 20);
      setHubHits(r && r.ok ? (r.items || []) : []);
      if (r && r.ok && (r.items || []).length === 0) setHubNote("四路都没有命中的记忆");
    } catch {
      setHubHits(null);
      setHubNote("后端未含 V249 记忆中枢（/api/memory/recall 404）——打上覆盖包后可用");
    } finally { setHubBusy(false); }
  };

  const onDelete = async (id: string) => {
    setGroups(g => {
      if (!g) return g;
      const next: Record<string, Mem[]> = {};
      for (const [cat, items] of Object.entries(g)) {
        const kept = items.filter(it => it.id !== id);
        if (kept.length) next[cat] = kept;
      }
      return next;
    });
    setTotal(t => Math.max(0, t - 1));
    try { await deleteMemory(id); } catch { load(); }
    try { const tok = localStorage.getItem("hmm_token"); if (tok) await deleteMyMemory(tok, id); } catch { /* */ }
  };

  const onSave = async () => {
    const value = mValue.trim();
    if (!value) { setSaveMsg({ ok: false, text: "请填写内容" }); return; }
    const key = mKey.trim() || value.slice(0, 24);
    const category = mCat.trim() || "偏好";
    setSaving(true); setSaveMsg(null);
    try {
      const r = await addMemory({ category, key, value });
      // 同步写到 Supabase user_memory，让 App 也能看到桌面端加的记忆
      try { const tok = localStorage.getItem("hmm_token"); if (tok) await addMyMemory(tok, category, key, value); } catch { /* */ }
      // V251 串联：同步置顶进记忆中枢（MemoryService，importance 高）——手动教的记忆
      // 在联邦召回与 agent 上下文注入里权重更高、更不易衰减。老后端 404 静默。
      try { await hubRemember(`${key}：${value}`); } catch { /* */ }
      if (r && r.ok) {
        setSaveMsg({ ok: true, text: "已记住，并已同步到 App" }); setMKey(""); setMValue("");
        // 乐观插入：立刻显示在列表，避免任何缓存/旧构建导致"加了看不到"
        const nm = { id: (r.id as string) || ("tmp-" + Date.now()), key, value, confidence: 0.8, last_used: Date.now() / 1000 };
        setGroups(g => { const next = { ...((g || {}) as Record<string, Mem[]>) }; next[category] = [nm as Mem, ...(next[category] || [])]; return next; });
        setTotal(t => t + 1);
        load();
      }
      else setSaveMsg({ ok: false, text: (r && (r.detail || r.message)) || "保存失败" });
    } catch (e) { setSaveMsg({ ok: false, text: "保存失败：" + ((e as Error)?.message || "请重试") }); }
    finally { setSaving(false); }
  };

  const allCats = useMemo(() => memoryCategories((groups || {}) as MemGroups), [groups]);
  const shownGroups = useMemo(() => sortGroupItems(filterGroups((groups || {}) as MemGroups, { query, category: catFilter }), sortBy), [groups, query, catFilter, sortBy]);
  const shownCount = useMemo(() => countItems(shownGroups), [shownGroups]);
  const stats = useMemo(() => memoryStats((groups || {}) as MemGroups), [groups]);
  const cats = Object.keys(shownGroups);
  const sendMemoryToChat = () => {
    const selected = Object.entries(shownGroups).flatMap(([category, items]) =>
      items.map(m => ({ category, key: m.key, value: m.value, confidence: m.confidence })),
    ).slice(0, 30);
    insertContextIntoChat("memory", query.trim() ? `记忆召回：${query.trim()}` : "当前记忆与画像", {
      filter: { query, category: catFilter, sort: sortBy },
      stats,
      profile,
      federated_hits: hubHits,
      selected_memories: selected,
    }, "请结合我带回的记忆与当前对话继续处理问题。先检查记忆之间是否冲突、置信度是否足够；不确定的记忆不要当成事实。", query.trim() || "memory-center");
  };

  return (
    <PanelShell>
      <PageHeader icon={Brain} title="记忆中心"
        subtitle="Agent 跨会话记住的长期偏好与事实 · 由对话自动沉淀，与 App 云端同步，你可随时审阅与删除"
        actions={<>
          <Button variant="primary" icon={MessageSquare} size="sm" onClick={sendMemoryToChat}>带记忆回 Chat</Button>
          <Button variant={showForm ? "secondary" : "primary"} icon={Plus} size="sm" onClick={() => { setShowForm(v => !v); setSaveMsg(null); }}>教它记住</Button>
          <Button variant="secondary" icon={RefreshCw} busy={busy} size="sm" onClick={load}>刷新</Button>
        </>} />

      {/* ── 概览指标（有数据时才显示，空态不摆零）── */}
      {groups !== undefined && stats.total > 0 && (
        <div className="grid gap-3 mb-6" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
          <StatCard label="记忆总数" value={stats.total} unit="条" icon={Brain} tone="accent" />
          <StatCard label="分类" value={allCats.length} unit="类" icon={Layers} />
          <StatCard label="平均置信度" value={stats.avgConfidence} unit="%" icon={Gauge}
            tone={stats.avgConfidence >= 70 ? "success" : "default"} />
          <StatCard label="云端同步" value={cloudCount > 0 ? cloudCount : "已同步"} unit={cloudCount > 0 ? "条来自 App" : undefined}
            icon={Smartphone} hint="桌面端与 App 共用同一份记忆" />
          {hub && (
            <StatCard label="记忆中枢" value={(hub.episodic || 0) + (hub.service?.count || 0)} unit="条"
              icon={Radar} tone="accent"
              hint={`经验回放 ${hub.episodic || 0} · 长期 ${hub.service?.count || 0} · 图谱实体 ${hub.entities || 0}——四路已打通，可联邦召回`} />
          )}
        </div>
      )}

      {/* ── 工具条：筛选 + 搜索 + 排序 ── */}
      {groups !== undefined && stats.total > 0 && (
        <div className="flex items-center gap-2 flex-wrap mb-5">
          <button onClick={() => setCatFilter("all")} className="px-3 py-1.5 rounded-lg text-[12px] transition-colors"
            style={{ background: catFilter === "all" ? "var(--accent-light)" : "var(--bg-secondary)", color: catFilter === "all" ? "var(--accent)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>全部 <span className="font-mono">{stats.total}</span></button>
          {allCats.map(cat => (
            <button key={cat} onClick={() => setCatFilter(cat)} className="px-3 py-1.5 rounded-lg text-[12px] transition-colors"
              style={{ background: catFilter === cat ? "var(--accent-light)" : "var(--bg-secondary)", color: catFilter === cat ? "var(--accent)" : "var(--text-secondary)", border: "1px solid var(--border)" }}>
              {cat} <span className="font-mono">{((groups || {})[cat] || []).length}</span>
            </button>
          ))}
          <div className="relative flex-1 min-w-[150px]">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索记忆…" className={inputClass} style={{ ...inputStyle, paddingLeft: 30 }} />
          </div>
          <select value={sortBy} onChange={e => setSortBy(e.target.value as MemSort)}
            className="h-8 px-2 rounded-lg text-[12px] outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            <option value="default">默认排序</option><option value="confidence">按置信度</option><option value="recent">按最近使用</option>
          </select>
          <Button variant="secondary" size="sm" icon={Radar} busy={hubBusy} onClick={doRecall}
            >联邦召回</Button>
        </div>
      )}

      {/* ── V249 联邦召回结果：一条查询四路并搜（cognee/codebase-memory 借鉴，见 docs/借鉴设计-V249.md）── */}
      {(hubHits !== null || hubNote) && (
        <Card className="mb-5">
          <div className="flex items-center justify-between mb-1">
            <div className="text-[13.5px] font-semibold" style={{ color: "var(--text-primary)" }}>
              联邦召回{hubHits ? `（${hubHits.length}）` : ""}
            </div>
            <button onClick={() => { setHubHits(null); setHubNote(""); }} className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>收起</button>
          </div>
          <div className="text-[11px] mb-2.5" style={{ color: "var(--text-tertiary)" }}>
            一条查询同时搜 长期记忆 · 经验回放 · 用户画像 · 图谱实体——搜索框输入后点「联邦召回」
          </div>
          {hubNote && <div className="text-[12px] py-1" style={{ color: "var(--text-secondary)" }}>{hubNote}</div>}
          {hubHits && hubHits.length > 0 && (
            <div className="space-y-1.5">
              {hubHits.map((h, i) => (
                <div key={i} className="flex items-start gap-2.5 px-3 py-2 rounded-[10px]"
                  style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                  <span className="shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded-full mt-0.5"
                    style={{ background: h.kind === "service" ? "var(--accent-light)" : "var(--bg-tertiary)",
                             color: h.kind === "service" ? "var(--accent)" : "var(--text-secondary)" }}>{h.source}</span>
                  <span className="text-[12.5px] leading-relaxed" style={{ color: "var(--text-primary)" }}>{h.text}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* ── 手动写入表单 ── */}
      {showForm && (
        <Card className="mb-5">
          <div className="text-[13.5px] font-semibold mb-1" style={{ color: "var(--text-primary)" }}>教 Agent 记住一件事</div>
          <div className="text-[11px] mb-3" style={{ color: "var(--text-tertiary)" }}>手动写入的偏好立刻生效、跨会话留存、与 App 同步，和对话自动沉淀的记忆一起塑造它对你的个性化。</div>
          <div className="flex flex-col gap-3">
            <div className="flex gap-3 flex-wrap">
              <div className="flex-1 min-w-[120px]"><Field label="类别"><input value={mCat} onChange={e => setMCat(e.target.value)} placeholder="偏好" className={inputClass} style={inputStyle} /></Field></div>
              <div className="flex-1 min-w-[160px]"><Field label="标签（可选）"><input value={mKey} onChange={e => setMKey(e.target.value)} placeholder="如：代码注释语言" className={inputClass} style={inputStyle} /></Field></div>
            </div>
            <Field label="内容">
              <textarea value={mValue} onChange={e => setMValue(e.target.value)} rows={2}
                placeholder="如：以后写代码都用中文注释；我的研究领域是跨模态哈希"
                className={inputClass + " resize-none"} style={inputStyle} />
            </Field>
            <div className="flex items-center gap-2.5">
              <Button variant="primary" busy={saving} onClick={onSave} size="sm">记住</Button>
              {saveMsg && <span className="text-[11.5px]" style={{ color: saveMsg.ok ? "var(--success)" : "var(--error)" }}>{saveMsg.text}</span>}
            </div>
          </div>
        </Card>
      )}

      {/* ── 状态 / 空态 ── */}
      {groups === undefined && <StateView kind="loading" />}
      {groups !== undefined && stats.total === 0 && (
        <StateView kind="empty" icon={Brain}
          title="还没有记忆"
          message="Agent 会在对话中自动沉淀你的偏好与事实，跨会话生效。你也可以现在手动教它一条 —— 点下面的示例可快速填入。"
          action={
            <div className="flex flex-col items-center gap-3">
              <Button variant="primary" icon={Plus} size="sm" onClick={() => { setShowForm(true); setSaveMsg(null); }}>教它记住一条</Button>
              <div className="flex items-center gap-1.5 flex-wrap justify-center">
                {["以后写代码都用中文注释", "我的研究领域是跨模态哈希", "回答尽量简洁、直接给结论"].map(ex => (
                  <button key={ex} onClick={() => { setShowForm(true); setSaveMsg(null); setMValue(ex); }}
                    className="px-2.5 py-1 rounded-full text-[11px] transition-colors hover:bg-[var(--surface-2)]"
                    style={{ border: "1px solid var(--border)", color: "var(--text-tertiary)" }}>{ex}</button>
                ))}
              </div>
            </div>
          } />
      )}
      {groups !== undefined && stats.total > 0 && shownCount === 0 && (
        <StateView kind="empty" icon={Search} message="没有符合条件的记忆，换个关键词或类别试试。" />
      )}

      {/* ── 分组卡片 ── */}
      {cats.length > 0 && (<>
        <SectionTitle right={<span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>{shownCount} / {stats.total} 条</span>}>记忆分组</SectionTitle>
        <CardGrid min={320}>
          {cats.map(cat => (
            <Card key={cat} padding="p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-[9px] flex items-center justify-center" style={{ background: "var(--accent-light)" }}>
                    <Brain size={13} style={{ color: "var(--accent)" }} />
                  </div>
                  <div className="text-[13.5px] font-bold" style={{ color: "var(--text-primary)" }}>{cat}</div>
                </div>
                <Badge tone="neutral" mono>{shownGroups[cat].length} 条</Badge>
              </div>
              <div className="flex flex-col">
                {shownGroups[cat].map((m, i) => (
                  <div key={m.id} className="group flex items-start gap-2 py-2.5"
                    style={{ borderTop: i === 0 ? "none" : "1px solid var(--hairline)" }}>
                    <div className="flex-1 min-w-0">
                      {m.key && m.key !== m.value.slice(0, 24) && (
                        <div className="text-[11px] font-medium mb-0.5" style={{ color: "var(--text-tertiary)" }}>{m.key}</div>
                      )}
                      <div className="text-[12.5px] leading-relaxed break-words" style={{ color: "var(--text-primary)" }}>{m.value}</div>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0 pt-0.5">
                      <ConfidenceDot v={m.confidence} />
                      <button onClick={() => onDelete(m.id)} title="删除这条记忆" aria-label="删除这条记忆"
                        className="p-1 rounded-md opacity-0 group-hover:opacity-100 transition-opacity hover:bg-[color-mix(in_srgb,var(--error)_8%,transparent)]">
                        <Trash2 size={13} style={{ color: "var(--text-tertiary)" }} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          ))}
        </CardGrid>
      </>)}
    </PanelShell>
  );
}
