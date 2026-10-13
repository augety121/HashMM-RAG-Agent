"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, CircleGauge, KeyRound, Loader2, Network, Plus, Route, Server, Trash2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  activateProviderConnection, addProviderChannel, addProviderConnection, deleteProviderConnection,
  previewProviderRoute, probeProviderConnection, providerFabric,
  type ProviderConnection, type ProviderFabricOverview,
} from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  official: "官方 API", sub2api: "Sub2API", openai_compatible: "OpenAI 兼容",
  anthropic_compatible: "Anthropic 兼容", local: "本地运行时",
};

export default function ProviderFabricPanel() {
  const [data, setData] = useState<ProviderFabricOverview | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [channelFor, setChannelFor] = useState("");
  const [channel, setChannel] = useState({ model_alias: "default", upstream_model: "", priority: 100, weight: 100, max_concurrency: 4 });
  const [routeProof, setRouteProof] = useState("");
  const [form, setForm] = useState({ name: "", kind: "sub2api", base_url: "", upstream_model: "", api_key: "", wire_api: "chat_completions" });
  const load = async () => { try { setData(await providerFabric()); setError(""); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); }, []);
  const act = async (id: string, kind: "probe" | "activate" | "delete") => {
    if (kind === "delete" && !window.confirm("删除这条连接及其模型通道？此操作不会删除上游账号。")) return;
    setBusy(`${kind}:${id}`); setError("");
    try {
      if (kind === "probe") {
        const result = await probeProviderConnection(id);
        setRouteProof(`连接检查通过${typeof result.latency_ms === "number" ? ` · ${result.latency_ms} ms` : ""}`);
      }
      if (kind === "activate") {
        await activateProviderConnection(id);
        setRouteProof("已明确启用到 Chat；新请求将使用这条个人连接，流式开始后不会切换上游。");
      }
      if (kind === "delete") await deleteProviderConnection(id);
      await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };
  const create = async () => {
    if (!form.name.trim() || !form.base_url.trim() || !form.upstream_model.trim()) { setError("名称、Base URL 和上游模型为必填"); return; }
    setBusy("create"); setError("");
    try { await addProviderConnection({ ...form, model_alias: "default" }); setOpen(false); setForm({ ...form, name: "", base_url: "", upstream_model: "", api_key: "" }); await load(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };
  const createChannel = async () => {
    if (!channelFor || !channel.upstream_model.trim()) { setError("通道的上游模型不能为空"); return; }
    setBusy(`channel:${channelFor}`); setError("");
    try { await addProviderChannel(channelFor, channel); setChannelFor(""); setChannel({ ...channel, upstream_model: "" }); await load(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };
  const preview = async (alias: string) => {
    setBusy(`route:${alias}`); setError("");
    try { const proof = await previewProviderRoute(alias); setRouteProof(`${proof.channel.connection_name || "连接"} → ${proof.channel.upstream_model} · ${proof.retry_boundary}`); }
    catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };
  const card = "rounded-xl border p-4";
  const input = "w-full rounded-lg px-3 py-2 text-[12px] outline-none";
  const summaries: Array<[string, number, LucideIcon]> = data ? [
    ["连接", data.summary.connections, Server], ["通道", data.summary.channels, Network],
    ["健康", data.summary.healthy, CheckCircle2], ["Sub2API", data.summary.sub2api, CircleGauge],
  ] : [];
  return <div className="max-w-[880px] space-y-4">
    <div className="flex items-start justify-between gap-4">
      <div><h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>Provider Fabric</h3>
        <p className="mt-1 text-[12px] leading-5" style={{ color: "var(--text-secondary)" }}>连接你有权使用的官方 API、Sub2API 或兼容网关。上游密钥加密保存，与 HashMM 对外 API Key 完全隔离。</p></div>
      <button onClick={() => setOpen(v => !v)} className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-[12px] font-medium text-white" style={{ background: "var(--accent)" }}><Plus size={14} />添加连接</button>
    </div>
    {data && <div className="grid grid-cols-4 gap-2">
      {summaries.map(([label, value, Icon]) => <div key={label} className={card} style={{ borderColor: "var(--border)", background: "var(--bg-primary)" }}><Icon size={15} style={{ color: "var(--accent)" }} /><div className="mt-2 text-[20px] font-semibold">{value}</div><div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{label}</div></div>)}
    </div>}
    {open && <div className={card} style={{ borderColor: "var(--border)", background: "var(--bg-secondary)" }}>
      <div className="grid grid-cols-2 gap-2.5">
        <input className={input} style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }} placeholder="连接名称" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
        <select className={input} style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }} value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })}>{Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <input className={input} style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }} placeholder="https://gateway.example.com/v1" value={form.base_url} onChange={e => setForm({ ...form, base_url: e.target.value })} />
        <input className={input} style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }} placeholder="上游模型 ID" value={form.upstream_model} onChange={e => setForm({ ...form, upstream_model: e.target.value })} />
        <input className={input} style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }} type="password" placeholder="上游 API Key（不会回显）" value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} />
        <select className={input} style={{ border: "1px solid var(--border)", background: "var(--bg-primary)" }} value={form.wire_api} onChange={e => setForm({ ...form, wire_api: e.target.value })}><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option><option value="anthropic_messages">Anthropic Messages</option></select>
      </div>
      <div className="mt-3 flex justify-end"><button disabled={busy === "create"} onClick={create} className="rounded-lg px-4 py-2 text-[12px] font-medium text-white disabled:opacity-50" style={{ background: "var(--accent)" }}>{busy === "create" ? "保存中…" : "保存连接"}</button></div>
    </div>}
    {error && <div className="rounded-lg px-3 py-2 text-[12px]" style={{ background: "color-mix(in srgb,#b42318 9%,transparent)", color: "#b42318" }}>{error}</div>}
    <div className="space-y-2">
      {(data?.connections || []).map((item: ProviderConnection) => <div key={item.id} className={card} style={{ borderColor: "var(--border)", background: "var(--bg-primary)" }}><div className="flex items-center gap-4">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>{item.has_credential ? <KeyRound size={17} /> : <Server size={17} />}</div>
        <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="text-[13px] font-medium">{item.name}</span><span className="rounded px-1.5 py-0.5 text-[10px]" style={{ background: item.status === "healthy" ? "#e8f8ee" : "var(--bg-tertiary)", color: item.status === "healthy" ? "#16803c" : "var(--text-tertiary)" }}>{item.status}</span></div><div className="truncate text-[11px]" style={{ color: "var(--text-tertiary)" }}>{KIND_LABEL[item.kind]} · {item.channels[0]?.upstream_model || "未配置通道"} · {item.base_url}</div></div>
        <button onClick={() => void preview(item.channels[0]?.model_alias || "default")} className="rounded-lg px-2.5 py-1.5 text-[11px]" style={{ background: "var(--bg-tertiary)" }}>{busy.startsWith("route:") ? <Loader2 size={13} className="animate-spin" /> : <><Route size={13} className="inline mr-1" />路由</>}</button>
        <button onClick={() => void act(item.id, "probe")} className="rounded-lg px-2.5 py-1.5 text-[11px]" style={{ background: "var(--bg-tertiary)" }}>{busy === `probe:${item.id}` ? <Loader2 size={13} className="animate-spin" /> : "检查"}</button>
        <button onClick={() => void act(item.id, "activate")} className="rounded-lg px-2.5 py-1.5 text-[11px] text-white" style={{ background: "var(--accent)" }}>用于 Chat</button>
        <button aria-label="删除连接" onClick={() => void act(item.id, "delete")} className="rounded-lg p-1.5" style={{ color: "#b42318" }}><Trash2 size={14} /></button>
      </div><div className="mt-3 border-t pt-3" style={{ borderColor: "var(--border)" }}><div className="flex items-center justify-between"><span className="text-[12px] font-medium">模型通道 · {item.channels.length}</span><button onClick={() => setChannelFor(channelFor === item.id ? "" : item.id)} className="text-[11px]" style={{ color: "var(--accent)" }}>+ 添加通道</button></div>{item.channels.map(ch => <div key={ch.id} className="mt-2 grid grid-cols-[1fr_auto_auto_auto] items-center gap-3 text-[11px]"><span className="truncate">{ch.model_alias} → {ch.upstream_model}</span><span style={{ color: "var(--text-tertiary)" }}>优先级 {ch.priority}</span><span style={{ color: "var(--text-tertiary)" }}>权重 {ch.weight}</span><span style={{ color: "var(--text-tertiary)" }}>{ch.active_leases || 0}/{ch.max_concurrency}</span></div>)}{channelFor === item.id && <div className="mt-3 grid grid-cols-5 gap-2"><input className={input} style={{ border: "1px solid var(--border)" }} value={channel.model_alias} onChange={e => setChannel({ ...channel, model_alias: e.target.value })} placeholder="别名" /><input className={`${input} col-span-2`} style={{ border: "1px solid var(--border)" }} value={channel.upstream_model} onChange={e => setChannel({ ...channel, upstream_model: e.target.value })} placeholder="上游模型 ID" /><input className={input} type="number" style={{ border: "1px solid var(--border)" }} value={channel.weight} onChange={e => setChannel({ ...channel, weight: Number(e.target.value) })} aria-label="通道权重" /><button onClick={() => void createChannel()} className="rounded-lg text-[11px] text-white" style={{ background: "var(--accent)" }}>保存</button></div>}</div></div>)}
      {data && data.connections.length === 0 && <div className="rounded-xl border border-dashed py-10 text-center text-[12px]" style={{ borderColor: "var(--border)", color: "var(--text-tertiary)" }}>还没有 Provider 连接。添加后先检查，再明确启用到 Chat。</div>}
    </div>
    {routeProof && <div className="rounded-lg px-3 py-2 text-[12px]" style={{ background: "var(--accent-light)", color: "var(--text-secondary)" }}>路由演练：{routeProof}</div>}
    <div className="text-[11px] leading-5" style={{ color: "var(--text-tertiary)" }}>合规边界：只连接你有权使用的服务；HashMM 不提供消费者订阅共享、账号池绕过或授权规避。流式输出开始后不会切换上游通道。</div>
  </div>;
}
