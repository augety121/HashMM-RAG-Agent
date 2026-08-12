"use client";
import { useState, useEffect, useCallback, useMemo } from "react";
import { useStore } from "@/lib/store";
import { PROVIDERS } from "@/lib/types";
import type { ModelConfig, ModelWireApi, ProviderSpec } from "@/lib/types";
import * as api from "@/lib/api";
import { Plus, Trash2, Check, AlertTriangle, Star, Loader2, RefreshCw, Search } from "lucide-react";
import { Badge } from "./shared";
import { filterModels, distinctProviders, modelStats, countByProvider, type ModelRec } from "@/lib/modelFilter";

type ModelForm = {
  name: string; provider: string; base_url: string; api_key: string;
  model_name: string; temperature: number; max_tokens: number; wire_api: ModelWireApi;
  config: {
    routing_tier: "fast" | "auto" | "deep";
    supports_reasoning: boolean;
    supports_parallel_tools: boolean;
    supports_structured_output: boolean;
    max_input_tokens: number;
    text_verbosity?: "low" | "medium" | "high";
    prompt_cache_retention?: "" | "in_memory" | "24h";
    anthropic_thinking_budget_tokens?: number;
  };
};

const FALLBACK_PROVIDERS: ProviderSpec[] = PROVIDERS.map(provider => ({
  id: provider.id,
  name: provider.name,
  base_url: provider.url,
  wire_apis: provider.id === "anthropic"
    ? ["anthropic_messages"]
    : ["openai", "azure_openai", "aws_bedrock", "oci_genai", "doubao", "minimax", "xai", "perplexity", "sambanova"].includes(provider.id)
      ? ["responses", "chat_completions"]
      : ["chat_completions"],
  default_wire_api: provider.id === "anthropic"
    ? "anthropic_messages"
    : ["openai", "azure_openai", "aws_bedrock", "oci_genai"].includes(provider.id)
      ? "responses"
      : "chat_completions",
  auth: ["ollama", "lmstudio", "vllm"].includes(provider.id) ? "optional" : "bearer",
  local: ["ollama", "lmstudio", "vllm"].includes(provider.id),
  base_url_required: ["azure_openai", "aws_bedrock", "oci_genai", "baidu_qianfan", "sambanova", "custom"].includes(provider.id),
  endpoint_note: "",
  model_hints: [],
  capabilities: { tools: true, streaming: true, vision: true, json_schema: false, model_discovery: true },
}));

const EMPTY_FORM: ModelForm = {
  name: "DeepSeek", provider: "deepseek", base_url: "https://api.deepseek.com",
  api_key: "", model_name: "", temperature: 0.1, max_tokens: 4096,
  wire_api: "chat_completions",
  config: {
    routing_tier: "auto", supports_reasoning: false,
    supports_parallel_tools: false, supports_structured_output: false,
    max_input_tokens: 0,
    text_verbosity: "medium", prompt_cache_retention: "",
    anthropic_thinking_budget_tokens: 4096,
  },
};

function modelWire(model: ModelConfig): string {
  if (model.wire_api) return model.wire_api;
  try {
    const config = JSON.parse(model.config_json || "{}");
    return String(config.wire_api || "chat_completions");
  } catch {
    return "chat_completions";
  }
}

export function ModelsTab() {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [providers, setProviders] = useState<ProviderSpec[]>(FALLBACK_PROVIDERS);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState<ModelForm>(EMPTY_FORM);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  // V103.90 搜索 / 服务商筛选
  const [query, setQuery] = useState("");
  const [provFilter, setProvFilter] = useState("all");
  const stats = useMemo(() => modelStats(models as ModelRec[]), [models]);
  const provs = useMemo(() => distinctProviders(models as ModelRec[]), [models]);
  const shown = useMemo(() => filterModels(models as ModelRec[], { query, provider: provFilter }) as ModelConfig[], [models, query, provFilter]);
  const selectedProvider = useMemo(
    () => providers.find(provider => provider.id === form.provider),
    [providers, form.provider],
  );

  const load = useCallback(async () => {
    const [modelResult, providerResult] = await Promise.allSettled([
      api.listModels(), api.getModelProviders(),
    ]);
    if (modelResult.status === "fulfilled") setModels(Array.isArray(modelResult.value) ? modelResult.value : []);
    const discoveredProviders = providerResult.status === "fulfilled"
      ? providerResult.value?.providers
      : null;
    if (Array.isArray(discoveredProviders) && discoveredProviders.length) {
      setProviders(discoveredProviders);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  function selectProvider(id: string) {
    const provider = providers.find(item => item.id === id);
    if (provider) setForm({
      ...form,
      provider: id,
      base_url: provider.base_url,
      name: provider.name,
      model_name: "",
      wire_api: provider.default_wire_api,
      config: {
        ...form.config,
        supports_reasoning: false,
        supports_parallel_tools: false,
        supports_structured_output: Boolean(provider.capabilities?.json_schema),
      },
    });
    setTestResult(null); setError("");
    setDiscoveredModels([]);
  }

  async function discoverModels() {
    if (!form.base_url.trim()) { setError("请先填写 Base URL"); return; }
    if (selectedProvider?.auth !== "optional" && !form.api_key.trim()) { setError("请先填写 API Key"); return; }
    setDiscovering(true); setError("");
    try {
      const result = await api.discoverProviderModels(form);
      setDiscoveredModels(result.models || []);
      if (!result.ok || !result.supported) setError(result.message || "该服务商不支持自动读取模型列表");
      else if (!result.models?.length) setError("接口未返回模型 ID，请从服务商控制台复制");
    } catch (e: unknown) {
      setError((e as Error).message || "读取模型列表失败");
    } finally {
      setDiscovering(false);
    }
  }

  async function testConnection() {
    if (!form.model_name.trim()) { setError("请填写服务商控制台中的精确模型或部署 ID"); return; }
    if (!form.base_url.trim()) { setError("请填写 Base URL"); return; }
    if (selectedProvider?.auth !== "optional" && !form.api_key.trim()) { setError("请填写 API Key"); return; }
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
      setForm(EMPTY_FORM);
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
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>服务商</label>
              <select value={form.provider} onChange={event => selectProvider(event.target.value)}
                className="w-full h-9 px-3 rounded-lg text-xs outline-none"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                {providers.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>接口协议</label>
              <select value={form.wire_api} onChange={event => setForm({ ...form, wire_api: event.target.value as ModelWireApi })}
                disabled={(selectedProvider?.wire_apis?.length || 0) <= 1}
                className="w-full h-9 px-3 rounded-lg text-xs outline-none disabled:opacity-70"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                {(selectedProvider?.wire_apis || ["chat_completions"]).map(wire => (
                  <option key={wire} value={wire}>{wire === "chat_completions" ? "Chat Completions" : wire === "responses" ? "Responses" : "Anthropic Messages"}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="rounded-lg p-3 space-y-3" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
            <div>
              <div className="text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>运行角色与能力契约</div>
              <div className="mt-0.5 text-[10px] leading-4" style={{ color: "var(--text-tertiary)" }}>
                HashMM 不根据模型名称猜能力。只勾选该模型官方文档与账号实测都支持的能力。
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>自动路由角色</label>
                <select value={form.config.routing_tier}
                  onChange={e => setForm({ ...form, config: { ...form.config, routing_tier: e.target.value as "fast" | "auto" | "deep" } })}
                  className="w-full h-9 px-3 rounded-lg text-xs outline-none"
                  style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                  <option value="fast">快速：高频、低延迟</option>
                  <option value="auto">均衡：默认任务</option>
                  <option value="deep">深度：复杂推理与验收</option>
                </select>
              </div>
              <div>
                <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>输入上下文上限</label>
                <input type="number" min="0" step="1024" value={form.config.max_input_tokens}
                  onChange={e => setForm({ ...form, config: { ...form.config, max_input_tokens: Math.max(0, +e.target.value || 0) } })}
                  placeholder="0 表示未知，不猜测"
                  className="w-full h-9 px-3 rounded-lg text-xs outline-none"
                  style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              </div>
            </div>
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              <label className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
                <input type="checkbox" checked={form.config.supports_reasoning}
                  onChange={e => setForm({ ...form, config: { ...form.config, supports_reasoning: e.target.checked } })} />
                原生推理控制
              </label>
              <label className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
                <input type="checkbox" checked={form.config.supports_parallel_tools}
                  onChange={e => setForm({ ...form, config: { ...form.config, supports_parallel_tools: e.target.checked } })} />
                并行工具调用
              </label>
              <label className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
                <input type="checkbox" checked={form.config.supports_structured_output}
                  disabled={!selectedProvider?.capabilities?.json_schema}
                  onChange={e => setForm({ ...form, config: { ...form.config, supports_structured_output: e.target.checked } })} />
                结构化输出
              </label>
            </div>
            {(form.wire_api === "responses" || form.provider === "anthropic") && (
              <div className="grid grid-cols-2 gap-3">
                {form.wire_api === "responses" && (
                  <>
                    <div>
                      <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>回答详细度</label>
                      <select value={form.config.text_verbosity || "medium"}
                        onChange={e => setForm({ ...form, config: { ...form.config, text_verbosity: e.target.value as "low" | "medium" | "high" } })}
                        className="w-full h-9 px-3 rounded-lg text-xs outline-none"
                        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                        <option value="low">精简</option><option value="medium">均衡</option><option value="high">详细</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>提示缓存保留</label>
                      <select value={form.config.prompt_cache_retention || ""}
                        onChange={e => setForm({ ...form, config: { ...form.config, prompt_cache_retention: e.target.value as "" | "in_memory" | "24h" } })}
                        className="w-full h-9 px-3 rounded-lg text-xs outline-none"
                        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                        <option value="">服务商默认</option><option value="in_memory">内存</option><option value="24h">24 小时</option>
                      </select>
                    </div>
                  </>
                )}
                {form.provider === "anthropic" && form.config.supports_reasoning && (
                  <div>
                    <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>思考 Token 预算</label>
                    <input type="number" min="1024" step="1024" value={form.config.anthropic_thinking_budget_tokens || 4096}
                      onChange={e => setForm({ ...form, config: { ...form.config, anthropic_thinking_budget_tokens: Math.max(1024, +e.target.value || 4096) } })}
                      className="w-full h-9 px-3 rounded-lg text-xs outline-none"
                      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                  </div>
                )}
              </div>
            )}
          </div>
          {selectedProvider && (
            <div className="flex items-start justify-between gap-3 px-3 py-2.5 rounded-lg" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}>
              <div className="min-w-0">
                <div className="text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
                  {selectedProvider.local ? "本地推理服务" : "云端模型服务"}
                </div>
                <div className="mt-0.5 text-[10px] leading-4" style={{ color: "var(--text-tertiary)" }}>
                  {selectedProvider.endpoint_note || "模型与部署权限以服务商控制台为准，HashMM 不猜测账号可用模型。"}
                </div>
              </div>
              <div className="flex flex-wrap justify-end gap-1 flex-shrink-0">
                {selectedProvider.capabilities?.tools && <Badge>工具</Badge>}
                {selectedProvider.capabilities?.streaming && <Badge>流式</Badge>}
                {selectedProvider.capabilities?.vision && <Badge>视觉</Badge>}
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>配置名称 *</label>
              <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="w-full h-9 px-3 rounded-lg text-xs outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
            </div>
            <div>
              <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>模型或部署 ID *</label>
              <input value={form.model_name} onChange={e => setForm({ ...form, model_name: e.target.value })}
                list="hashmm-provider-model-hints" placeholder="从服务商控制台复制"
                className="w-full h-9 px-3 rounded-lg text-xs outline-none" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
              <datalist id="hashmm-provider-model-hints">
                {[...(selectedProvider?.model_hints || []), ...discoveredModels]
                  .filter((model, index, all) => all.indexOf(model) === index)
                  .map(model => <option key={model} value={model} />)}
              </datalist>
              <button type="button" onClick={discoverModels} disabled={discovering || selectedProvider?.capabilities?.model_discovery === false}
                className="mt-1.5 text-[10.5px] disabled:opacity-50"
                style={{ color: "var(--accent)" }}>
                {discovering ? "正在读取…" : discoveredModels.length ? `已读取 ${discoveredModels.length} 个模型` : "从当前账号读取模型 ID"}
              </button>
            </div>
          </div>
          <div>
            <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>Base URL *</label>
            <input value={form.base_url} onChange={e => setForm({ ...form, base_url: e.target.value })} className="w-full h-9 px-3 rounded-lg text-xs outline-none font-mono" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          </div>
          <div>
            <label className="block text-[11px] font-medium mb-1" style={{ color: "var(--text-tertiary)" }}>API Key {selectedProvider?.auth === "optional" ? "（可选）" : "*"}</label>
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
              <div className="text-[11px] font-mono truncate" style={{ color: "var(--text-tertiary)" }}>{m.model_name} · {modelWire(m)} · {m.base_url}</div>
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
