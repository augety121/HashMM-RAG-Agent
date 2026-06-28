"use client";
import { useState, useEffect, useCallback, useMemo } from "react";
import { useStore } from "@/lib/store";
import { PROVIDERS } from "@/lib/types";
import type { ModelConfig } from "@/lib/types";
import * as api from "@/lib/api";
import { Plus, Trash2, Check, AlertTriangle, Star, Loader2, RefreshCw, Search } from "lucide-react";
import { Badge } from "./shared";
import { filterModels, distinctProviders, modelStats, countByProvider, type ModelRec } from "@/lib/modelFilter";

export function ModelsTab() {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ name: "DeepSeek Chat", provider: "deepseek", base_url: "https://api.deepseek.com/v1", api_key: "", model_name: "deepseek-chat", temperature: 0.1, max_tokens: 4096 });
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  // V103.90 搜索 / 服务商筛选
  const [query, setQuery] = useState("");
  const [provFilter, setProvFilter] = useState("all");
  const stats = useMemo(() => modelStats(models as ModelRec[]), [models]);
  const provs = useMemo(() => distinctProviders(models as ModelRec[]), [models]);
  const shown = useMemo(() => filterModels(models as ModelRec[], { query, provider: provFilter }) as ModelConfig[], [models, query, provFilter]);

  const load = useCallback(async () => { try { setModels(await api.listModels()); } catch (_e) { /* empty */ } }, []);
  useEffect(() => { load(); }, [load]);

  function selectProvider(id: string) {
    const p = PROVIDERS.find(x => x.id === id);
    if (p) setForm({ ...form, provider: id, base_url: p.url || form.base_url, name: p.name });
    setTestResult(null); setError("");
  }

  async function testConnection() {
    if (!form.base_url || !form.model_name) { setError("请填写 Base URL 和模型名"); return; }
    setTesting(true); setTestResult(null); setError("");
    try {
      const r = await api.testModel(form);
      setTestResult(r);
    } catch (e: unknown) { setTestResult({ ok: false, message: (e as Error).message || "测试失败" }); }
    setTesting(false);
  }

  async function addModel() {
    if (!form.name.trim()) { setError("请填写配置名称"); return; }
    if (!form.model_name.trim()) { setError("请填写模型名"); return; }
    if (!form.base_url.trim()) { setError("请填写 Base URL"); return; }
    setSaving(true); setError(""); setMsg("");
    try {
      await api.createModel(form);
      setShowAdd(false);
      setForm({ name: "DeepSeek Chat", provider: "deepseek", base_url: "https://api.deepseek.com/v1", api_key: "", model_name: "deepseek-chat", temperature: 0.1, max_tokens: 4096 });
      setTestResult(null); setMsg("模型添加成功"); setTimeout(() => setMsg(""), 3000);
      load();
      api.stats().then(s => useStore.getState().set({ stats: s })).catch(() => {});
    } catch (e: unknown) { setError((e as Error).message || "添加失败"); }
    setSaving(false);
  }

  async function setDefault(id: string) {
    try {
      await api.setDefaultModel(id);
      setMsg("默认模型已切换"); setTimeout(() => setMsg(""), 3000); load();
      api.stats().then(s => useStore.getState().set({ stats: s })).catch(() => {});
    } catch (e: unknown) { alert((e as Error).message || "切换失败"); }
  }

  async function delModel(id: string, name: string) {
    if (!confirm(`删除模型 "${name}"？`)) return;
    try { await api.deleteModel(id); load(); } catch (e: unknown) { alert((e as Error).message); }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>模型配置 <span className="font-normal text-[11px]" style={{ color: "var(--text-tertiary)" }}>{stats.total} 个 · {stats.providers} 家服务商{stats.hasDefault ? "" : " · 未设默认"}</span></h4>
        <button onClick={() => { setShowAdd(!showAdd); setError(""); setTestResult(null); }}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white" style={{ background: "var(--accent)" }}>
          <Plus size={14} /> 添加模型
        </button>
      </div>

      {msg && (
        <div className="mb-3 px-3 py-2 rounded-lg text-xs font-medium bg-green-50 dark:bg-green-950/20 text-green-600 flex items-center gap-2">
          <Check size={14} /> {msg}
        </div>
      )}

      {showAdd && (
        <div className="mb-4 p-4 rounded-xl space-y-3 anim-fade-up" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <div>
            <label className="block text-[11px] font-medium mb-1.5" style={{ color: "var(--text-tertiary)" }}>服务商</label>
            <div className="flex flex-wrap gap-1.5">
              {PROVIDERS.map(p => (
                <button key={p.id} onClick={() => selectProvider(p.id)}
                  className="px-3 py-1.5 rounded-lg text-[11px] font-medium transition-all"
                  style={{
                    background: form.provider === p.id ? "var(--accent-light)" : "var(--bg-primary)",
                    color: form.provider === p.id ? "var(--accent)" : "var(--text-secondary)",
                    border: `1px solid ${form.provider === p.id ? "var(--accent)" : "var(--border)"}`,
                  }}>{p.name}</button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>配置名称 *</label>
              <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="w-full h-9 px-3 rounded-lg text-xs outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
            <div>
              <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>模型名 *</label>
              <input value={form.model_name} onChange={e => setForm({ ...form, model_name: e.target.value })} className="w-full h-9 px-3 rounded-lg text-xs outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
          </div>
          <div>
            <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>Base URL *</label>
            <input value={form.base_url} onChange={e => setForm({ ...form, base_url: e.target.value })} className="w-full h-9 px-3 rounded-lg text-xs outline-none font-mono" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          </div>
          <div>
            <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>API Key</label>
            <input value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} type="password" className="w-full h-9 px-3 rounded-lg text-xs outline-none font-mono" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} placeholder="sk-..." />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>Temperature</label>
              <input type="number" step="0.1" min="0" max="2" value={form.temperature} onChange={e => setForm({ ...form, temperature: +e.target.value })} className="w-full h-9 px-3 rounded-lg text-xs outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
            <div>
              <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>Max Tokens</label>
              <input type="number" step="256" min="256" value={form.max_tokens} onChange={e => setForm({ ...form, max_tokens: +e.target.value })} className="w-full h-9 px-3 rounded-lg text-xs outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
          </div>
          {testResult && (
            <div className={`flex items-start gap-2 px-3 py-2.5 rounded-lg text-xs ${testResult.ok ? "bg-green-50 dark:bg-green-950/20 text-green-600" : "bg-red-50 dark:bg-red-950/20 text-red-500"}`}>
              {testResult.ok ? <Check size={14} className="mt-0.5 flex-shrink-0" /> : <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />}
              <span>{testResult.message}</span>
            </div>
          )}
          {error && <div className="text-xs text-red-500 px-3 py-2 bg-red-50 dark:bg-red-950/20 rounded-lg">{error}</div>}
          <div className="flex gap-2 pt-1">
            <button onClick={testConnection} disabled={testing} className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium disabled:opacity-50" style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
              {testing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} {testing ? "测试中..." : "测试连接"}
            </button>
            <button onClick={addModel} disabled={saving} className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-white disabled:opacity-50" style={{ background: "var(--accent)" }}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} {saving ? "保存中..." : "保存模型"}
            </button>
            <button onClick={() => { setShowAdd(false); setError(""); setTestResult(null); }} className="px-4 py-2 rounded-lg text-xs font-medium" style={{ color: "var(--text-secondary)", background: "var(--bg-tertiary)" }}>取消</button>
          </div>
        </div>
      )}

      {/* V103.90 搜索 / 服务商筛选（配置较多时出现）*/}
      {models.length > 2 && (
        <div className="flex items-center gap-2 flex-wrap mb-3">
          <div className="relative flex-1 min-w-[160px]">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索配置名 / 模型名 / URL…"
              className="w-full text-[12px] pl-8 pr-3 py-1.5 rounded-lg outline-none" style={{ background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          </div>
          <div className="inline-flex rounded-lg overflow-hidden flex-wrap" style={{ border: "1px solid var(--border)" }}>
            <button onClick={() => setProvFilter("all")} className="px-2.5 py-1 text-[11px] transition-colors" style={{ background: provFilter === "all" ? "var(--accent-light)" : "transparent", color: provFilter === "all" ? "var(--accent)" : "var(--text-tertiary)" }}>全部 {stats.total}</button>
            {provs.map(p => (
              <button key={p} onClick={() => setProvFilter(p)} className="px-2.5 py-1 text-[11px] transition-colors" style={{ background: provFilter === p ? "var(--accent-light)" : "transparent", color: provFilter === p ? "var(--accent)" : "var(--text-tertiary)", borderLeft: "1px solid var(--border)" }}>{p} {countByProvider(models as ModelRec[], p)}</button>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-2">
        {shown.map(m => (
          <div key={m.id} className="flex items-center gap-3 p-4 rounded-xl transition-all" style={{ background: "var(--bg-secondary)", border: `1.5px solid ${m.is_default ? "var(--accent)" : "var(--border)"}` }}>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{m.name}</span>
                {m.is_default ? <Badge color="#059669">默认</Badge> : null}
                <Badge>{m.provider}</Badge>
              </div>
              <div className="text-[11px] font-mono truncate" style={{ color: "var(--text-tertiary)" }}>{m.model_name} · {m.base_url}</div>
            </div>
            <div className="flex items-center gap-1.5 flex-shrink-0">
              {!m.is_default && <button onClick={() => setDefault(m.id)} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--accent-light)]" title="设为默认" style={{ color: "var(--text-tertiary)" }}><Star size={15} /></button>}
              {!m.is_default && <button onClick={() => delModel(m.id, m.name)} className="p-1.5 rounded-lg text-red-400 transition-colors hover:bg-red-50 dark:hover:bg-red-950/20"><Trash2 size={15} /></button>}
            </div>
          </div>
        ))}
        {shown.length === 0 && <div className="text-center py-12 text-sm" style={{ color: "var(--text-tertiary)" }}>{models.length ? "没有符合条件的模型" : "暂无模型配置"}</div>}
      </div>
    </div>
  );
}
