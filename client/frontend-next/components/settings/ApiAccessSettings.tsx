"use client";

import { useEffect, useMemo, useState } from "react";
import { BarChart3, Check, Clipboard, KeyRound, Loader2, Pencil, Plus, ShieldAlert, Trash2, X } from "lucide-react";
import * as api from "@/lib/api";


const SCOPES = [
  "models:read", "responses:read", "responses:write", "threads:read",
  "threads:write", "runs:read", "runs:write", "usage:read",
] as const;

type FormState = {
  name: string;
  scopes: string[];
  models: string;
  projects: string;
  allowIps: string;
  denyIps: string;
  quota: string;
  rpm: string;
  concurrency: string;
  expires: string;
};

const EMPTY: FormState = {
  name: "", scopes: ["models:read", "responses:read", "responses:write"],
  models: "", projects: "", allowIps: "", denyIps: "", quota: "", rpm: "60",
  concurrency: "4", expires: "",
};

function splitList(value: string): string[] {
  return value.split(/[\n,]/).map(item => item.trim()).filter(Boolean);
}

function optionalNumber(value: string): number | null {
  return value.trim() ? Number(value) : null;
}

function toPayload(form: FormState): api.PlatformApiKeyInput {
  return {
    name: form.name.trim(), scopes: form.scopes,
    allowed_models: splitList(form.models), allowed_projects: splitList(form.projects),
    ip_allowlist: splitList(form.allowIps), ip_denylist: splitList(form.denyIps),
    quota_limit: optionalNumber(form.quota), rpm_limit: optionalNumber(form.rpm),
    concurrent_limit: optionalNumber(form.concurrency),
    expires_at: form.expires ? new Date(form.expires).getTime() / 1000 : null,
  };
}

function fromKey(key: api.PlatformApiKey): FormState {
  const localExpiry = key.expires_at
    ? new Date(key.expires_at * 1000 - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
    : "";
  return {
    name: key.name, scopes: [...key.scopes], models: key.allowed_models.join(", "),
    projects: key.allowed_projects.join(", "), allowIps: key.ip_allowlist.join(", "),
    denyIps: key.ip_denylist.join(", "), quota: key.quota_limit?.toString() ?? "",
    rpm: key.rpm_limit?.toString() ?? "", concurrency: key.concurrent_limit?.toString() ?? "",
    expires: localExpiry,
  };
}

function formatTime(value: number | null | undefined): string {
  return value ? new Date(value * 1000).toLocaleString() : "—";
}

function KeyForm({ initial, saving, submitLabel, onSubmit, onCancel }: {
  initial: FormState;
  saving: boolean;
  submitLabel: string;
  onSubmit: (value: FormState) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState(initial);
  const valid = form.name.trim().length > 0 && form.scopes.length > 0 && [form.quota, form.rpm, form.concurrency]
    .every(value => !value || Number.isFinite(Number(value)) && Number(value) >= 0);
  const field = "w-full rounded-lg px-3 py-2 text-[12px] outline-none bg-[var(--bg-secondary)] border border-[var(--border)]";
  return (
    <div className="rounded-2xl p-4 space-y-4" style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
      <div className="flex items-center justify-between">
        <div className="text-[13px] font-semibold">{submitLabel}</div>
        <button aria-label="关闭" onClick={onCancel}><X size={15} /></button>
      </div>
      <label className="block text-[11px] text-[var(--text-secondary)]">名称
        <input className={`${field} mt-1`} value={form.name} maxLength={80}
          onChange={e => setForm({ ...form, name: e.target.value })} placeholder="例如：生产服务" />
      </label>
      <div>
        <div className="text-[11px] mb-2 text-[var(--text-secondary)]">权限 Scope（默认最小权限）</div>
        <div className="grid grid-cols-2 gap-2">
          {SCOPES.map(scope => <label key={scope} className="flex items-center gap-2 text-[11px]">
            <input type="checkbox" checked={form.scopes.includes(scope)} onChange={e => setForm({
              ...form, scopes: e.target.checked ? [...form.scopes, scope] : form.scopes.filter(item => item !== scope),
            })} /> {scope}
          </label>)}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-[11px] text-[var(--text-secondary)]">允许模型（逗号分隔，空=不限）
          <input className={`${field} mt-1`} value={form.models} onChange={e => setForm({ ...form, models: e.target.value })} />
        </label>
        <label className="text-[11px] text-[var(--text-secondary)]">允许项目（逗号分隔，空=不限）
          <input className={`${field} mt-1`} value={form.projects} onChange={e => setForm({ ...form, projects: e.target.value })} />
        </label>
        <label className="text-[11px] text-[var(--text-secondary)]">IP Allowlist（CIDR）
          <input className={`${field} mt-1`} value={form.allowIps} onChange={e => setForm({ ...form, allowIps: e.target.value })} />
        </label>
        <label className="text-[11px] text-[var(--text-secondary)]">IP Denylist（Deny 优先）
          <input className={`${field} mt-1`} value={form.denyIps} onChange={e => setForm({ ...form, denyIps: e.target.value })} />
        </label>
        <label className="text-[11px] text-[var(--text-secondary)]">费用配额（CNY）
          <input className={`${field} mt-1`} type="number" min="0" step="0.01" value={form.quota} onChange={e => setForm({ ...form, quota: e.target.value })} />
        </label>
        <label className="text-[11px] text-[var(--text-secondary)]">每分钟请求数
          <input className={`${field} mt-1`} type="number" min="1" value={form.rpm} onChange={e => setForm({ ...form, rpm: e.target.value })} />
        </label>
        <label className="text-[11px] text-[var(--text-secondary)]">最大并发
          <input className={`${field} mt-1`} type="number" min="1" value={form.concurrency} onChange={e => setForm({ ...form, concurrency: e.target.value })} />
        </label>
        <label className="text-[11px] text-[var(--text-secondary)]">过期时间
          <input className={`${field} mt-1`} type="datetime-local" value={form.expires} onChange={e => setForm({ ...form, expires: e.target.value })} />
        </label>
      </div>
      <div className="flex justify-end gap-2">
        <button className="px-3 py-2 rounded-lg text-[12px] border border-[var(--border)]" onClick={onCancel}>取消</button>
        <button disabled={!valid || saving} onClick={() => onSubmit(form)}
          className="px-3 py-2 rounded-lg text-[12px] text-white disabled:opacity-50 flex items-center gap-1.5 bg-[var(--accent)]">
          {saving && <Loader2 size={13} className="animate-spin" />}{submitLabel}
        </button>
      </div>
    </div>
  );
}

export function ApiAccessSettings() {
  const [keys, setKeys] = useState<api.PlatformApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<api.PlatformApiKey | null>(null);
  const [oneTimeSecret, setOneTimeSecret] = useState("");
  const [copied, setCopied] = useState(false);
  const [usageKeyId, setUsageKeyId] = useState("");
  const [usage, setUsage] = useState<Awaited<ReturnType<typeof api.getPlatformApiKeyUsage>> | null>(null);

  const activeCount = useMemo(() => keys.filter(key => key.status === "active").length, [keys]);
  const reload = async () => {
    setLoading(true); setError("");
    try { setKeys(await api.listPlatformApiKeys()); }
    catch (err) { setError(err instanceof Error ? err.message : "加载 API Key 失败"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void reload(); }, []);
  useEffect(() => () => setOneTimeSecret(""), []);

  const create = async (form: FormState) => {
    setSaving(true); setError("");
    try {
      const value = await api.createPlatformApiKey(toPayload(form));
      setOneTimeSecret(value.secret); setKeys(current => [value, ...current]); setCreating(false);
    } catch (err) { setError(err instanceof Error ? err.message : "创建失败"); }
    finally { setSaving(false); }
  };
  const update = async (form: FormState) => {
    if (!editing) return;
    setSaving(true); setError("");
    try {
      const value = await api.updatePlatformApiKey(editing.id, editing.revision, toPayload(form));
      setKeys(current => current.map(item => item.id === value.id ? value : item)); setEditing(null);
    } catch (err) {
      const conflict = (err as Error & { status?: number }).status === 409;
      setError(conflict ? "此 API Key 已在别处修改，已刷新，请重新编辑。" : err instanceof Error ? err.message : "更新失败");
      if (conflict) { setEditing(null); await reload(); }
    } finally { setSaving(false); }
  };
  const revoke = async (key: api.PlatformApiKey) => {
    if (!window.confirm(`撤销 ${key.name}？撤销后新请求会立即失败，且不可恢复。`)) return;
    setSaving(true); setError("");
    try {
      const value = await api.revokePlatformApiKey(key.id);
      setKeys(current => current.map(item => item.id === value.id ? value : item));
    } catch (err) { setError(err instanceof Error ? err.message : "撤销失败"); }
    finally { setSaving(false); }
  };
  const copySecret = async () => {
    if (!oneTimeSecret) return;
    await navigator.clipboard.writeText(oneTimeSecret);
    setCopied(true); window.setTimeout(() => setCopied(false), 1500);
  };
  const toggleUsage = async (key: api.PlatformApiKey) => {
    if (usageKeyId === key.id) { setUsageKeyId(""); setUsage(null); return; }
    setUsageKeyId(key.id); setUsage(null); setError("");
    try { setUsage(await api.getPlatformApiKeyUsage(key.id, 30)); }
    catch (err) { setError(err instanceof Error ? err.message : "加载用量失败"); setUsageKeyId(""); }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[15px] font-semibold">API 访问</h2>
          <p className="text-[11px] mt-1 max-w-[560px] leading-relaxed text-[var(--text-tertiary)]">
            为外部 Agent 和服务创建独立凭据。每个 Key 都有自己的权限、模型/项目边界、IP、速率、并发和费用配额。
          </p>
        </div>
        <button onClick={() => { setCreating(true); setEditing(null); }}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-[12px] text-white bg-[var(--accent)]">
          <Plus size={14} /> 创建 API Key
        </button>
      </div>

      <div className="rounded-xl px-3 py-2.5 flex items-center gap-2 text-[11px]"
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
        <KeyRound size={14} className="text-[var(--accent)]" /> 共 {keys.length} 个，{activeCount} 个启用
        <span className="ml-auto text-[var(--text-tertiary)]">完整密钥不会写入浏览器存储</span>
      </div>

      {error && <div className="rounded-xl p-3 text-[12px] flex gap-2 text-red-600 bg-red-50 border border-red-200">
        <ShieldAlert size={15} className="mt-0.5" />{error}
      </div>}

      {oneTimeSecret && <div className="rounded-2xl p-4 border border-amber-300 bg-amber-50 text-amber-950">
        <div className="font-semibold text-[13px]">请立即复制：完整密钥只显示这一次</div>
        <div className="text-[11px] mt-1">关闭后无法恢复；遗失时请创建新 Key 并撤销旧 Key。</div>
        <div className="mt-3 flex gap-2">
          <code className="flex-1 min-w-0 break-all rounded-lg p-2.5 bg-white border border-amber-200 text-[11px]">{oneTimeSecret}</code>
          <button onClick={copySecret} className="px-3 rounded-lg bg-white border border-amber-300 flex items-center gap-1.5 text-[11px]">
            {copied ? <Check size={13} /> : <Clipboard size={13} />}{copied ? "已复制" : "复制"}
          </button>
        </div>
        <button onClick={() => { setOneTimeSecret(""); setCopied(false); }} className="mt-3 text-[11px] underline">我已安全保存，关闭</button>
      </div>}

      {creating && <KeyForm initial={EMPTY} saving={saving} submitLabel="创建 API Key" onSubmit={create} onCancel={() => setCreating(false)} />}
      {editing && <KeyForm key={`${editing.id}:${editing.revision}`} initial={fromKey(editing)} saving={saving}
        submitLabel="保存策略" onSubmit={update} onCancel={() => setEditing(null)} />}

      {loading ? <div className="py-12 flex justify-center"><Loader2 className="animate-spin text-[var(--accent)]" /></div>
        : keys.length === 0 ? <div className="py-12 text-center text-[12px] text-[var(--text-tertiary)]">尚未创建 API Key</div>
        : <div className="space-y-3">{keys.map(key => <div key={key.id} className="rounded-2xl p-4"
            style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-[var(--bg-secondary)]"><KeyRound size={16} /></div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-semibold truncate">{key.name}</span>
                <span className={`text-[10px] px-2 py-0.5 rounded-full ${key.status === "active" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"}`}>{key.status}</span>
                <code className="text-[10px] text-[var(--text-tertiary)]">{key.prefix}…</code>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">{key.scopes.map(scope => <span key={scope}
                className="text-[9px] px-1.5 py-0.5 rounded bg-[var(--bg-secondary)] border border-[var(--border)]">{scope}</span>)}</div>
              <div className="mt-3 grid grid-cols-2 lg:grid-cols-4 gap-x-4 gap-y-2 text-[10px] text-[var(--text-tertiary)]">
                <span>RPM：{key.rpm_limit ?? "不限"}</span><span>并发：{key.concurrent_limit ?? "不限"}</span>
                <span>配额：{key.quota_limit == null ? "不限" : `${key.quota_used.toFixed(4)} / ${key.quota_limit}`}</span>
                <span>过期：{formatTime(key.expires_at)}</span><span>最后使用：{formatTime(key.last_used_at)}</span>
                <span>模型：{key.allowed_models.join(", ") || "不限"}</span><span>项目：{key.allowed_projects.join(", ") || "不限"}</span>
                <span>策略版本：{key.revision}</span>
              </div>
            </div>
            {key.status !== "revoked" && <div className="flex gap-1">
              <button title="查看 30 天用量" onClick={() => void toggleUsage(key)} className="p-2 rounded-lg hover:bg-[var(--bg-secondary)]"><BarChart3 size={14} /></button>
              <button title="编辑策略" onClick={() => { setEditing(key); setCreating(false); }} className="p-2 rounded-lg hover:bg-[var(--bg-secondary)]"><Pencil size={14} /></button>
              <button title="撤销" disabled={saving} onClick={() => revoke(key)} className="p-2 rounded-lg text-red-600 hover:bg-red-50"><Trash2 size={14} /></button>
            </div>}
          </div>
          {usageKeyId === key.id && <div className="mt-4 pt-3 border-t border-[var(--border)]">
            {!usage ? <div className="text-[11px] flex items-center gap-2 text-[var(--text-tertiary)]"><Loader2 size={12} className="animate-spin" />加载 30 天用量…</div>
              : <div>
                <div className="grid grid-cols-3 gap-3 text-[10px]">
                  <div><div className="text-[var(--text-tertiary)]">计量事件</div><div className="text-[13px] mt-1">{usage.totals.events}</div></div>
                  <div><div className="text-[var(--text-tertiary)]">计量数量</div><div className="text-[13px] mt-1">{Number(usage.totals.quantity || 0).toLocaleString()}</div></div>
                  <div><div className="text-[var(--text-tertiary)]">估算费用</div><div className="text-[13px] mt-1">¥{Number(usage.totals.estimated_cost || 0).toFixed(6)}</div></div>
                </div>
                {usage.data.length > 0 && <div className="mt-3 space-y-1">{usage.data.map((row, index) => <div key={`${row.model}:${row.service}:${index}`}
                  className="grid grid-cols-4 text-[9px] text-[var(--text-tertiary)]">
                  <span>{row.model || "默认模型"}</span><span>{row.service}</span><span>{row.quantity} {row.unit}</span><span>¥{Number(row.estimated_cost || 0).toFixed(6)}</span>
                </div>)}</div>}
                <div className="mt-2 text-[9px] text-[var(--text-tertiary)]">估算值不冒充供应商正式账单；provider reported 成本仅在上游实际返回时记录。</div>
              </div>}
          </div>}
        </div>)}</div>}
    </div>
  );
}
