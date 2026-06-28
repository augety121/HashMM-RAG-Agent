"use client";
/** components/desktop/MemoryView.tsx — 记忆中心（V103.52 改用统一设计系统 PanelKit）。
 *
 * 后端「跨会话用户记忆」(routes/user_memory.py) + 五层记忆体系的「读 + 删 + 教它记住」入口，
 * 遵循 agent-curated memory「人可审计」原则：记忆由对话自动沉淀，这里只读/删/手动写。
 * 视觉全部走 PanelKit，与其它面板统一。
 */
import { useEffect, useState, useCallback, useMemo } from "react";
import { Brain, Trash2, RefreshCw, Plus, Search } from "lucide-react";
import { listMemory, deleteMemory, memoryProfile, addMemory } from "@/lib/api";
import { getMyMemories, addMyMemory, deleteMyMemory } from "@/lib/supabase";
import { PanelShell, PageHeader, Card, CardGrid, Button, Badge, Field, StateView, SectionTitle, inputClass, inputStyle } from "./ui/PanelKit";
import { filterGroups, sortGroupItems, memoryStats, memoryCategories, countItems, type MemGroups, type MemSort } from "@/lib/memoryFilter";

type Mem = { id: string; key: string; value: string; confidence: number; last_used?: number | null };

export function MemoryView() {
  const [groups, setGroups] = useState<Record<string, Mem[]> | undefined>(undefined);
  const [total, setTotal] = useState(0);
  const [profile, setProfile] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [mCat, setMCat] = useState("偏好");
  const [mKey, setMKey] = useState("");
  const [mValue, setMValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // V103.90 搜索 / 类别筛选 / 排序
  const [query, setQuery] = useState("");
  const [catFilter, setCatFilter] = useState("all");
  const [sortBy, setSortBy] = useState<MemSort>("default");

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
      if (Array.isArray(sb)) {
        const seen = new Set<string>();
        for (const items of Object.values(merged)) for (const it of items) seen.add(`${it.key}|${it.value}`);
        for (const row of sb) {
          const sig = `${row.key}|${row.value}`;
          if (seen.has(sig)) continue;
          seen.add(sig);
          const cat = row.category || "其他";
          const lu = row.last_used ? new Date(row.last_used).getTime() / 1000 : null;
          (merged[cat] ||= []).push({ id: row.id, key: row.key, value: row.value, confidence: row.confidence ?? 0.8, last_used: lu });
        }
      }
      setGroups(merged);
      const totalCount = Object.values(merged).reduce((s, a) => s + a.length, 0);
      setTotal(totalCount);
      setProfile(p && p.ok ? p : null);
    } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

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
      if (r && r.ok) {
        setSaveMsg({ ok: true, text: "已记住" }); setMKey(""); setMValue("");
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

  // V103.90 应用筛选 + 排序（基于分组）
  const allCats = useMemo(() => memoryCategories((groups || {}) as MemGroups), [groups]);
  const shownGroups = useMemo(() => sortGroupItems(filterGroups((groups || {}) as MemGroups, { query, category: catFilter }), sortBy), [groups, query, catFilter, sortBy]);
  const shownCount = useMemo(() => countItems(shownGroups), [shownGroups]);
  const stats = useMemo(() => memoryStats((groups || {}) as MemGroups), [groups]);
  const cats = Object.keys(shownGroups);

  return (
    <PanelShell>
      <PageHeader icon={Brain} title="记忆中心"
        subtitle={<>Agent 跨会话记住的长期偏好 · 共 {total} 条 · 由对话自动沉淀，你可随时删除</>}
        actions={<>
          <Button variant={showForm ? "secondary" : "primary"} icon={Plus} size="sm" onClick={() => { setShowForm(v => !v); setSaveMsg(null); }}>教它记住</Button>
          <Button variant="secondary" icon={RefreshCw} busy={busy} size="sm" onClick={load}>刷新</Button>
        </>} />

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
          <Badge tone="neutral" mono>平均置信度 {stats.avgConfidence}%</Badge>
        </div>
      )}

      {showForm && (
        <Card className="mb-5">
          <div className="text-[13.5px] font-semibold mb-1" style={{ color: "var(--text-primary)" }}>教 Agent 记住一件事</div>
          <div className="text-[11px] mb-3" style={{ color: "var(--text-tertiary)" }}>手动写入的偏好会立刻生效、跨会话留存，和对话自动沉淀的记忆一起塑造它对你的个性化。</div>
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

      {cats.length > 0 && (<>
        <SectionTitle>记忆分组</SectionTitle>
        <CardGrid min={320}>
          {cats.map(cat => (
            <Card key={cat}>
              <div className="flex items-baseline justify-between mb-2.5">
                <div className="text-[13.5px] font-bold" style={{ color: "var(--text-primary)" }}>{cat}</div>
                <Badge tone="neutral" mono>{shownGroups[cat].length} 条</Badge>
              </div>
              <div className="flex flex-col">
                {shownGroups[cat].map(m => (
                  <div key={m.id} className="group flex items-start gap-2 py-2" style={{ borderBottom: "1px dashed var(--border)" }}>
                    <div className="flex-1 min-w-0">
                      {m.key && <div className="text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>{m.key}</div>}
                      <div className="text-[12.5px] break-words" style={{ color: "var(--text-primary)" }}>{m.value}</div>
                    </div>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      <span className="text-[9px] font-mono" style={{ color: "var(--text-tertiary)" }} title="置信度">{Math.round((m.confidence ?? 0.8) * 100)}%</span>
                      <button onClick={() => onDelete(m.id)} title="删除这条记忆" aria-label="删除这条记忆"
                        className="p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity hover:bg-[var(--bg-tertiary)]">
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
