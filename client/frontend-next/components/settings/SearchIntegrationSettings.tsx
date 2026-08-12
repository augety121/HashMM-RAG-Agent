"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ArrowRight, Check, CircleAlert, Globe2, KeyRound, Loader2,
  RefreshCw, Search, SlidersHorizontal, Trash2,
} from "lucide-react";
import {
  getSearchIntegration, removeSearchIntegration, saveSearchIntegration,
  testSearchIntegration, type SearchIntegration,
} from "@/lib/api";
import {
  Badge, Button, Card, Field, Toggle, inputClass, inputStyle,
} from "@/components/desktop/ui/PanelKit";

type Props = {
  scopeLabel?: string;
  showChatAction?: boolean;
  onTryInChat?: () => void;
};

function readableSearchError(error: unknown, fallback: string): string {
  const status = typeof error === "object" && error !== null && "status" in error
    ? Number((error as { status?: unknown }).status)
    : 0;
  if (status === 404) return "当前服务器还没有联网搜索配置接口，请更新服务器包后重试。";
  if (status === 401 || status === 403) return "请先登录当前账号，再管理联网搜索。";
  if (error instanceof Error && error.message === "未登录") return "请先登录当前账号，再管理联网搜索。";
  return error instanceof Error && error.message ? error.message : fallback;
}

export function SearchIntegrationSettings({
  scopeLabel = "当前账号",
  showChatAction = false,
  onTryInChat,
}: Props) {
const [integration, setIntegration] = useState<SearchIntegration | null>(null);
  const [provider, setProvider] = useState("doubao");
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"neutral" | "success" | "error">("neutral");

  const load = useCallback(async () => {
    setLoading(true);
    setMessage("");
    try {
      setIntegration(await getSearchIntegration(provider));
    } catch (error) {
      setIntegration(null);
      setMessage(readableSearchError(error, "暂时无法读取联网搜索设置。"));
      setMessageTone("error");
    } finally {
      setLoading(false);
    }
  }, [provider]);

  useEffect(() => { void load(); }, [load]);

  const updateConfig = <K extends keyof SearchIntegration["config"]>(
    key: K,
    value: SearchIntegration["config"][K],
  ) => {
    setIntegration(current => current ? {
      ...current,
      config: { ...current.config, [key]: value },
    } : current);
  };

  const save = async () => {
    if (!integration) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await saveSearchIntegration(provider, {
        api_key: apiKey.trim() || undefined,
        enabled: integration.enabled,
        config: integration.config,
      });
      setIntegration(result.integration);
      setApiKey("");
      setMessage("设置已保存。深度检索会在当前账号下使用这项服务，并把来源带回原对话。");
      setMessageTone("success");
    } catch (error) {
      setMessage(readableSearchError(error, "保存失败，请检查服务地址和凭据。"));
      setMessageTone("error");
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    if (!integration?.configured || !integration.enabled) return;
    setBusy(true);
    setMessage("正在通过 Chat 实际使用的检索链路测试…");
    setMessageTone("neutral");
    try {
      const result = await testSearchIntegration(provider);
      setMessage(`连接正常，返回 ${result.result_count} 条结果，用时 ${result.latency_ms} 毫秒。`);
      setMessageTone("success");
    } catch (error) {
      setMessage(readableSearchError(error, "连接测试失败，请检查服务状态和当前额度。"));
      setMessageTone("error");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm("移除当前账号的联网搜索配置？移除后 Chat 将不再使用这项服务。")) return;
    setBusy(true);
    setMessage("");
    try {
      await removeSearchIntegration(provider);
      setApiKey("");
      await load();
      setMessage("已移除当前账号的联网搜索配置。");
      setMessageTone("success");
    } catch (error) {
      setMessage(readableSearchError(error, "移除失败，请稍后重试。"));
      setMessageTone("error");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <Card className="flex min-h-[180px] items-center justify-center">
        <div className="flex items-center gap-2 text-[12px]" style={{ color: "var(--text-tertiary)" }}>
          <Loader2 size={15} className="animate-spin" /> 正在读取联网搜索设置
        </div>
      </Card>
    );
  }

  if (!integration) {
    return (
      <Card className="flex min-h-[180px] flex-col items-center justify-center text-center">
        <CircleAlert size={20} style={{ color: "var(--text-tertiary)" }} />
        <div className="mt-2 text-[12px]" style={{ color: "var(--text-secondary)" }}>{message || "暂时无法读取联网搜索设置"}</div>
        <Button variant="secondary" icon={RefreshCw} size="sm" onClick={() => void load()}>重新读取</Button>
      </Card>
    );
  }

  const ready = integration.configured && integration.enabled;
  const providerOptions = [
    ["doubao", "豆包兼容（Beta）"], ["baidu", "百度千帆"], ["brave", "Brave"],
    ["exa", "Exa"], ["gemini", "Google Grounding"], ["serper", "Serper"], ["tavily", "Tavily"],
  ];
  return (
    <Card padding="p-0" className="overflow-hidden">
      <div className="flex items-start gap-3 px-5 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
          style={{ color: "var(--accent)", background: "var(--accent-light)" }}>
          <Globe2 size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>Agent Retrieval Fabric</h3>
            <Badge tone={ready ? "success" : "neutral"}>{ready ? "已接入 Chat" : integration.configured ? "已暂停" : "等待配置"}</Badge>
            <Badge>{scopeLabel}</Badge>
          </div>
          <p className="mt-1 max-w-[720px] text-[10.8px] leading-5" style={{ color: "var(--text-tertiary)" }}>
            多提供商公开信息检索、RAG 对照和证据互证。互证不等于事实真伪裁决；网页内容始终按不可信数据处理。
          </p>
        </div>
        <Toggle checked={integration.enabled}
          onChange={() => setIntegration(current => current ? { ...current, enabled: !current.enabled } : current)}
          ariaLabel="启用联网搜索" />
      </div>

      <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(250px,.7fr)]">
        <div className="space-y-4">
          <Field label="搜索提供商" hint="每个账号、每个提供商的凭据独立加密保存">
            <select value={provider} onChange={event => { setProvider(event.target.value); setApiKey(""); }}
              className={inputClass} style={inputStyle}>
              {providerOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </Field>
          <Field label="搜索服务凭据"
            hint={integration.configured
              ? `已安全保存 ${integration.masked_api_key}；留空不会替换现有凭据`
              : "凭据只在服务端加密保存，桌面端不会再次读取明文"}>
            <div className="relative">
              <KeyRound size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-tertiary)" }} />
              <input type="password" autoComplete="off" value={apiKey} onChange={event => setApiKey(event.target.value)}
                placeholder={integration.configured ? "留空表示保留现有凭据" : "填写搜索服务 API Key"}
                className={`${inputClass} pl-9`} style={inputStyle} />
            </div>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="每次返回">
              <select value={integration.config.count}
                onChange={event => updateConfig("count", Number(event.target.value))}
                className={inputClass} style={inputStyle}>
                {[5, 8, 10, 15, 20].map(value => <option key={value} value={value}>{value} 条结果</option>)}
              </select>
            </Field>
            {provider === "doubao" ? <Field label="摘要长度">
              <select value={integration.config.snippet_length ?? 800}
                onChange={event => updateConfig("snippet_length", Number(event.target.value))}
                className={inputClass} style={inputStyle}>
                {[400, 800, 1200, 1600, 2000].map(value => <option key={value} value={value}>最多 {value} 字</option>)}
              </select>
            </Field> : provider === "gemini" ? <Field label="Grounding 模型">
              <input value={integration.config.model ?? "gemini-2.5-flash"}
                onChange={event => updateConfig("model", event.target.value)}
                className={inputClass} style={inputStyle} />
            </Field> : <Field label="接入类型">
              <div className={`${inputClass} flex items-center`} style={inputStyle}>官方或托管搜索 API</div>
            </Field>}
          </div>
        </div>

        <div className="rounded-2xl p-4" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2 text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>
            <Search size={14} style={{ color: "var(--accent)" }} /> Chat 中如何使用
          </div>
          <div className="mt-3 space-y-2 text-[10.8px] leading-5" style={{ color: "var(--text-tertiary)" }}>
            <p>选择“深度检索”，或直接要求联网核对公开事实。</p>
            <p>HashMM 会记录实际来源；没有检索到的内容不会伪装成已确认事实。</p>
            <p>账号之间配置隔离，管理员看不到已保存凭据的明文。</p>
          </div>
          <div className="mt-4 flex items-center gap-2">
            <span className="h-2 w-2 rounded-full" style={{ background: ready ? "var(--success)" : "var(--text-tertiary)" }} />
            <span className="text-[10.5px]" style={{ color: "var(--text-secondary)" }}>
              {ready ? "可用于当前账号的 Chat" : integration.configured ? "保存启用状态后生效" : "填写凭据并测试后生效"}
            </span>
          </div>
        </div>
      </div>

      {provider === "doubao" && <details className="mx-5 mb-4 rounded-xl" style={{ border: "1px solid var(--border)" }}>
        <summary className="flex cursor-pointer items-center gap-2 px-3 py-2.5 text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
          <SlidersHorizontal size={13} /> 高级连接设置
        </summary>
        <div className="grid gap-3 border-t p-3 sm:grid-cols-2" style={{ borderColor: "var(--border)" }}>
          <Field label="检索范围">
            <select value={integration.config.version ?? "global"}
              onChange={event => updateConfig("version", event.target.value as "global" | "custom")}
              className={inputClass} style={inputStyle}>
              <option value="global">全网检索</option>
              <option value="custom">自定义范围检索</option>
            </select>
          </Field>
          <Field label="授权级别（可选）">
            <select value={integration.config.auth_level ?? ""}
              onChange={event => updateConfig("auth_level", event.target.value ? Number(event.target.value) : null)}
              className={inputClass} style={inputStyle}>
              <option value="">使用服务默认值</option>
              {[1, 2, 3, 4].map(value => <option key={value} value={value}>级别 {value}</option>)}
            </select>
          </Field>
          <Field label="兼容服务地址">
            <input value={integration.config.base_url}
              onChange={event => updateConfig("base_url", event.target.value)}
              className={`${inputClass} font-mono text-[10.5px]`} style={inputStyle} />
          </Field>
          <div className="flex items-end text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>
            仅在服务商要求时修改；错误地址会导致真实连接测试失败。
          </div>
        </div>
      </details>}

      {message && (
        <div className="mx-5 mb-4 rounded-xl px-3 py-2 text-[10.8px] leading-5"
          style={{
            color: messageTone === "error" ? "var(--error)" : messageTone === "success" ? "var(--success)" : "var(--text-secondary)",
            background: "var(--bg-secondary)", border: "1px solid var(--border)",
          }}>
          {message}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 px-5 py-4" style={{ borderTop: "1px solid var(--border)" }}>
        <Button variant="primary" icon={Check} size="sm" busy={busy} onClick={() => void save()}>保存设置</Button>
        <Button variant="secondary" size="sm" busy={busy} disabled={!ready} onClick={() => void test()}>测试真实连接</Button>
        {showChatAction && (
          <Button variant="secondary" icon={ArrowRight} size="sm" disabled={!ready} onClick={onTryInChat}>在 Chat 中试用</Button>
        )}
        {integration.configured && (
          <Button variant="ghost" icon={Trash2} size="sm" disabled={busy} onClick={() => void remove()}>移除凭据</Button>
        )}
      </div>
    </Card>
  );
}
