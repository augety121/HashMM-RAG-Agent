"use client";
import { useState, useEffect, useCallback } from "react";
import * as api from "@/lib/api";
import { Plus, Trash2, Play, Pencil, X, Globe, CheckCircle2, AlertCircle, RefreshCw } from "lucide-react";

type Param = api.CustomToolParam;
type Cfg = api.CustomToolConfig;

const EMPTY: Cfg = {
  name: "", description: "", method: "GET", url_template: "",
  headers: {}, params_schema: [], body_template: "", response_path: "", enabled: true,
};

// A couple of ready-made templates to get users started.
const PRESETS: { label: string; cfg: Partial<Cfg> }[] = [
  {
    label: "天气查询",
    cfg: {
      name: "get_weather", description: "查询指定城市的实时天气",
      method: "GET", url_template: "https://api.example.com/weather?city={city}",
      headers: { Authorization: "Bearer YOUR_KEY" },
      params_schema: [{ name: "city", type: "string", description: "城市名称", required: true, location: "path" }],
      response_path: "",
    },
  },
  {
    label: "通用 GET",
    cfg: { name: "", method: "GET", url_template: "https://api.example.com/path?q={q}",
      params_schema: [{ name: "q", type: "string", description: "查询参数", required: true, location: "path" }] },
  },
  {
    label: "通用 POST",
    cfg: { name: "", method: "POST", url_template: "https://api.example.com/action",
      headers: { "Content-Type": "application/json" },
      params_schema: [{ name: "text", type: "string", description: "内容", required: true, location: "body" }] },
  },
];

export function CustomToolsPanel() {
  const [tools, setTools] = useState<Cfg[]>([]);
  const [editing, setEditing] = useState<Cfg | null>(null);
  const [testArgs, setTestArgs] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [headersText, setHeadersText] = useState("{}");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastVerifiedAt, setLastVerifiedAt] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { const r = await api.listCustomTools(); setTools(r.tools || []); setLastVerifiedAt(Date.now()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "自定义工具列表读取失败"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  function openNew() {
    setEditing({ ...EMPTY }); setHeadersText("{}"); setTestArgs({}); setTestResult(null);
  }
  function openEdit(t: Cfg) {
    setEditing({ ...t }); setHeadersText(JSON.stringify(t.headers || {}, null, 2));
    setTestArgs({}); setTestResult(null);
  }
  function applyPreset(cfg: Partial<Cfg>) {
    const merged = { ...EMPTY, ...cfg } as Cfg;
    setEditing(merged); setHeadersText(JSON.stringify(merged.headers || {}, null, 2));
  }

  function setField<K extends keyof Cfg>(k: K, v: Cfg[K]) {
    setEditing(e => e ? { ...e, [k]: v } : e);
  }
  function addParam() {
    setEditing(e => e ? { ...e, params_schema: [...e.params_schema,
      { name: "", type: "string", description: "", required: false, location: "query" }] } : e);
  }
  function setParam(i: number, patch: Partial<Param>) {
    setEditing(e => e ? { ...e, params_schema: e.params_schema.map((p, idx) => idx === i ? { ...p, ...patch } : p) } : e);
  }
  function removeParam(i: number) {
    setEditing(e => e ? { ...e, params_schema: e.params_schema.filter((_, idx) => idx !== i) } : e);
  }

  function buildCfg(): Cfg | null {
    if (!editing) return null;
    let headers = {};
    try { headers = JSON.parse(headersText || "{}"); } catch { alert("Headers 不是合法 JSON"); return null; }
    return { ...editing, headers };
  }

  async function save() {
    const cfg = buildCfg(); if (!cfg) return;
    if (!cfg.name || !cfg.url_template || saving) { if (!cfg.name || !cfg.url_template) alert("工具名和 URL 不能为空"); return; }
    setSaving(true); setError(null); setNotice(null);
    try {
      if (cfg.id) await api.updateCustomTool(cfg.id, cfg);
      else await api.createCustomTool(cfg);
      setEditing(null); setNotice("服务器已确认保存自定义工具。"); await load();
    } catch (e) { setError("保存失败：" + (e instanceof Error ? e.message : String(e))); }
    finally { setSaving(false); }
  }
  async function runTest() {
    const cfg = buildCfg(); if (!cfg) return;
    setTesting(true); setTestResult(null);
    try {
      const args: Record<string, unknown> = {};
      cfg.params_schema.forEach(p => { if (testArgs[p.name] !== undefined) args[p.name] = testArgs[p.name]; });
      const r = await api.testCustomTool(cfg, args);
      setTestResult(`(${r.elapsed_ms}ms)\n${r.result}`);
    } catch (e) { setTestResult("测试失败：" + String(e)); }
    setTesting(false);
  }
  async function del(id?: string) {
    if (!id || saving || !confirm("确认删除该 API 工具？")) return;
    setSaving(true); setError(null); setNotice(null);
    try { await api.deleteCustomTool(id); setNotice("服务器已确认删除自定义工具。"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "删除未取得服务端回执"); }
    finally { setSaving(false); }
  }

  const card = { background: "var(--bg-secondary)", border: "1px solid var(--border)" };
  const input = "w-full px-2.5 py-1.5 rounded-lg text-[12px]";
  const inputStyle = { background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" };

  return (
    <div className="mt-6">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>自定义 API 工具</h4>
          <p className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
            配置外部 API（天气/地图/订票/搜索…），启用后 AI 可自动调用
          </p>
        </div>
        <button onClick={openNew}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium text-white"
          style={{ background: "var(--accent)" }}>
          <Plus size={14} /> 新建 API 工具
        </button>
      </div>

      {error && <div className="mb-3 flex items-start gap-3 rounded-xl px-4 py-3" style={{ background: "rgba(180,35,24,.06)", border: "1px solid rgba(180,35,24,.16)" }}><AlertCircle size={15} className="mt-0.5 flex-shrink-0" style={{ color: "#b42318" }} /><div className="flex-1"><div className="text-[12px]" style={{ color: "#b42318" }}>{error}</div><div className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{tools.length ? "当前保留最近一次成功读取的工具。" : "当前没有取得可验证的自定义工具列表。"}</div></div><button onClick={load} disabled={loading} className="inline-flex items-center gap-1 text-[11px]" style={{ color: "var(--accent)" }}><RefreshCw size={12} />重试</button></div>}
      {notice && <div className="mb-3 rounded-xl px-4 py-2.5 text-[11.5px]" style={{ background: "var(--accent-light)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>{notice}</div>}
      {lastVerifiedAt && !error && <div className="mb-3 text-[10px]" style={{ color: "var(--text-tertiary)" }}>本次已验证 · {new Date(lastVerifiedAt).toLocaleTimeString()}</div>}

      {/* List */}
      <div className="space-y-1">
        {tools.map(t => (
          <div key={t.id} className="flex items-center gap-3 px-4 py-3 rounded-xl"
            style={{ ...card, opacity: t.enabled ? 1 : 0.55 }}>
            <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
              style={{ background: "var(--accent-light)" }}>
              <Globe size={14} style={{ color: "var(--accent)" }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-semibold font-mono" style={{ color: "var(--text-primary)" }}>
                {t.name} <span className="text-[10px] font-normal px-1.5 py-0.5 rounded"
                  style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>{t.method}</span>
              </div>
              <div className="text-[10px] mt-0.5 truncate" style={{ color: "var(--text-tertiary)" }}>{t.description || t.url_template}</div>
            </div>
            {typeof t.call_count === "number" && t.call_count > 0 && (
              <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>调用 {t.call_count}</span>
            )}
            <button onClick={() => openEdit(t)} title="编辑" style={{ color: "var(--text-tertiary)" }}><Pencil size={15} /></button>
            <button disabled={saving} onClick={() => del(t.id)} title="删除" className="disabled:opacity-40" style={{ color: "var(--text-tertiary)" }}><Trash2 size={15} /></button>
          </div>
        ))}
        {!loading && tools.length === 0 && !error && (
          <div className="text-center py-8 rounded-xl" style={card}>
            <Globe size={28} style={{ color: "var(--text-tertiary)", opacity: 0.3 }} className="mx-auto mb-2" />
            <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>还没有自定义 API 工具</p>
            <p className="text-[10px] mt-1" style={{ color: "var(--text-tertiary)" }}>点右上角“新建 API 工具”，或从模板开始</p>
          </div>
        )}
      </div>

      {/* Editor modal */}
      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.5)" }} onClick={() => setEditing(null)}>
          <div className="w-full max-w-2xl max-h-[88vh] overflow-y-auto rounded-2xl p-5"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                {editing.id ? "编辑" : "新建"} API 工具
              </h3>
              <button onClick={() => setEditing(null)} style={{ color: "var(--text-tertiary)" }}><X size={18} /></button>
            </div>

            {!editing.id && (
              <div className="flex gap-2 mb-4">
                {PRESETS.map(p => (
                  <button key={p.label} onClick={() => applyPreset(p.cfg)}
                    className="px-2.5 py-1 rounded-lg text-[11px]"
                    style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                    {p.label}
                  </button>
                ))}
              </div>
            )}

            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>工具名（字母/数字/下划线）</label>
                  <input className={input} style={inputStyle} value={editing.name}
                    onChange={e => setField("name", e.target.value)} placeholder="get_weather" />
                </div>
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>HTTP 方法</label>
                  <select className={input} style={inputStyle} value={editing.method}
                    onChange={e => setField("method", e.target.value)}>
                    {["GET", "POST", "PUT", "DELETE"].map(m => <option key={m}>{m}</option>)}
                  </select>
                </div>
              </div>

              <div>
                <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>描述（告诉 AI 这个工具干什么、何时用）</label>
                <input className={input} style={inputStyle} value={editing.description}
                  onChange={e => setField("description", e.target.value)} placeholder="查询指定城市的实时天气" />
              </div>

              <div>
                <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>URL 模板（路径参数用 {"{name}"} 占位）</label>
                <input className={`${input} font-mono`} style={inputStyle} value={editing.url_template}
                  onChange={e => setField("url_template", e.target.value)}
                  placeholder="https://api.example.com/weather?city={city}" />
              </div>

              <div>
                <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>请求头 Headers（JSON，放 API Key 等）</label>
                <textarea className={`${input} font-mono`} style={{ ...inputStyle, minHeight: 56 }}
                  value={headersText} onChange={e => setHeadersText(e.target.value)}
                  placeholder='{"Authorization": "Bearer xxx"}' />
              </div>

              {/* Params */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>参数（AI 会按描述填写）</label>
                  <button onClick={addParam} className="text-[11px]" style={{ color: "var(--accent)" }}>+ 添加参数</button>
                </div>
                <div className="space-y-1.5">
                  {editing.params_schema.map((p, i) => (
                    <div key={i} className="flex gap-1.5 items-center">
                      <input className="px-2 py-1 rounded-md text-[11px] font-mono w-24" style={inputStyle}
                        value={p.name} onChange={e => setParam(i, { name: e.target.value })} placeholder="name" />
                      <input className="px-2 py-1 rounded-md text-[11px] flex-1" style={inputStyle}
                        value={p.description} onChange={e => setParam(i, { description: e.target.value })} placeholder="描述" />
                      <select className="px-1.5 py-1 rounded-md text-[11px]" style={inputStyle}
                        value={p.location} onChange={e => setParam(i, { location: e.target.value })}>
                        <option value="query">query</option>
                        <option value="path">path</option>
                        <option value="body">body</option>
                      </select>
                      <label className="text-[10px] flex items-center gap-1" style={{ color: "var(--text-tertiary)" }}>
                        <input type="checkbox" checked={p.required} onChange={e => setParam(i, { required: e.target.checked })} />必填
                      </label>
                      <button onClick={() => removeParam(i)} style={{ color: "var(--text-tertiary)" }}><X size={13} /></button>
                    </div>
                  ))}
                </div>
              </div>

              {editing.method !== "GET" && (
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>Body 模板（可选，{"{name}"} 占位；留空则用 body 参数自动组装）</label>
                  <textarea className={`${input} font-mono`} style={{ ...inputStyle, minHeight: 44 }}
                    value={editing.body_template} onChange={e => setField("body_template", e.target.value)}
                    placeholder='{"text": "{text}"}' />
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>响应取值路径（可选，如 data.now）</label>
                  <input className={`${input} font-mono`} style={inputStyle} value={editing.response_path}
                    onChange={e => setField("response_path", e.target.value)} placeholder="data.now" />
                </div>
                <label className="flex items-end gap-2 text-[12px] pb-1.5" style={{ color: "var(--text-secondary)" }}>
                  <input type="checkbox" checked={editing.enabled}
                    onChange={e => setField("enabled", e.target.checked)} /> 启用（AI 可调用）
                </label>
              </div>

              {/* Test */}
              <div className="rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
                <div className="text-[11px] mb-2" style={{ color: "var(--text-secondary)" }}>测试调用（填入参数值后点运行）</div>
                <div className="space-y-1.5 mb-2">
                  {editing.params_schema.filter(p => p.name).map(p => (
                    <div key={p.name} className="flex items-center gap-2">
                      <span className="text-[11px] font-mono w-24 truncate" style={{ color: "var(--text-tertiary)" }}>{p.name}</span>
                      <input className="px-2 py-1 rounded-md text-[11px] flex-1" style={inputStyle}
                        value={testArgs[p.name] || ""} onChange={e => setTestArgs(a => ({ ...a, [p.name]: e.target.value }))}
                        placeholder={p.description} />
                    </div>
                  ))}
                </div>
                <button onClick={runTest} disabled={testing}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px]"
                  style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
                  <Play size={12} /> {testing ? "测试中…" : "运行测试"}
                </button>
                {testResult && (
                  <pre className="mt-2 p-2 rounded-lg text-[10px] font-mono whitespace-pre-wrap max-h-40 overflow-y-auto"
                    style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>{testResult}</pre>
                )}
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setEditing(null)}
                className="px-3 py-1.5 rounded-lg text-[12px]"
                style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>取消</button>
              <button disabled={saving} onClick={save}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] text-white disabled:opacity-50"
                style={{ background: "var(--accent)" }}>
                <CheckCircle2 size={14} /> {saving ? "等待回执…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
