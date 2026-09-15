"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2, Globe2, Loader2, RefreshCw, Save, Search, ServerCog, XCircle,
} from "lucide-react";
import { SearchIntegrationSettings } from "@/components/settings/SearchIntegrationSettings";
import {
  Badge, Button, Card, Field, inputClass, inputStyle,
} from "@/components/desktop/ui/PanelKit";

interface Setting {
  key: string;
  description: string;
  is_secret: boolean;
  configured: boolean;
  display: string;
  source: string;
}

const PLATFORM_KEYS = [
  "search_backend",
  "web_fallback_enabled",
  "baidu_search_api_key",
  "brave_search_api_key",
  "exa_api_key",
  "gemini_api_key",
  "serper_api_key",
  "tavily_api_key",
] as const;

const FIELD_COPY: Record<string, { label: string; hint: string }> = {
  search_backend: {
    label: "平台默认检索服务",
    hint: "仅用于没有账号级搜索配置的服务端流程；留空时由系统按可用性选择。",
  },
  web_fallback_enabled: {
    label: "允许服务端联网兜底",
    hint: "知识库没有命中时是否允许平台检索公开网页。关闭不会影响用户主动发起的账号级深度检索。",
  },
  serper_api_key: { label: "Serper 凭据", hint: "平台级兼容检索服务，凭据保存后不会返回明文。" },
  tavily_api_key: { label: "Tavily 凭据", hint: "平台级 Tavily 检索凭据。" },
  baidu_search_api_key: { label: "百度千帆检索凭据", hint: "百度 AI Search 官方接口的平台级凭据。" },
  brave_search_api_key: { label: "Brave Search 凭据", hint: "独立 Web 索引的平台级搜索凭据。" },
  exa_api_key: { label: "Exa 凭据", hint: "语义与内容检索的平台级凭据。" },
  gemini_api_key: { label: "Google Grounding 凭据", hint: "Google Search Grounding 托管检索凭据。" },
};

export function SettingsTab() {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; result: string } | null>(null);
  const [message, setMessage] = useState("");

  const headers = useCallback(() => {
    const value: Record<string, string> = { "Content-Type": "application/json" };
    const token = localStorage.getItem("hmm_token");
    if (token) value.Authorization = `Bearer ${token}`;
    return value;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setMessage("");
    try {
      const response = await fetch("/api/admin/settings", { headers: headers() });
      if (!response.ok) throw new Error(`读取失败（${response.status}）`);
      const data = await response.json();
      setSettings(Array.isArray(data.settings) ? data.settings : []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "暂时无法读取平台检索设置。");
    } finally {
      setLoading(false);
    }
  }, [headers]);

  useEffect(() => { void load(); }, [load]);

  const platformSettings = useMemo(
    () => PLATFORM_KEYS.map(key => settings.find(setting => setting.key === key)).filter((setting): setting is Setting => Boolean(setting)),
    [settings],
  );

  const save = async (setting: Setting) => {
    const value = edits[setting.key];
    if (value === undefined || value === "") return;
    setSaving(setting.key);
    setMessage("");
    try {
      const response = await fetch("/api/admin/settings", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ key: setting.key, value }),
      });
      if (!response.ok) throw new Error(`保存失败（${response.status}）`);
      setEdits(current => {
        const next = { ...current };
        delete next[setting.key];
        return next;
      });
      setMessage(`${FIELD_COPY[setting.key]?.label || setting.key}已保存。`);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败，请稍后重试。");
    } finally {
      setSaving("");
    }
  };

  const testPlatformSearch = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const response = await fetch("/api/admin/settings/test-search", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ query: "苹果公司 2024 营收" }),
      });
      const data = await response.json();
      setTestResult({
        ok: response.ok && data.ok === true,
        result: typeof data.result === "string" ? data.result : response.ok ? "服务没有返回可展示内容。" : "测试请求失败。",
      });
    } catch {
      setTestResult({ ok: false, result: "无法连接服务端，请检查服务器状态后重试。" });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-8">
      <section>
        <div className="mb-4 flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
            style={{ color: "var(--accent)", background: "var(--accent-light)" }}><Globe2 size={17} /></span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>Chat 联网搜索</h3>
              <Badge tone="accent">账号级</Badge>
            </div>
            <p className="mt-1 max-w-[760px] text-[11px] leading-5" style={{ color: "var(--text-tertiary)" }}>
              与插件中心“联网搜索”使用同一份配置。这里保存后无需再去插件页重复填写，桌面端和 App 会按同一账号使用。
            </p>
          </div>
        </div>
        <SearchIntegrationSettings scopeLabel="当前管理员账号" />
      </section>

      <section>
        <div className="mb-4 flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
              style={{ color: "var(--text-secondary)", background: "var(--bg-tertiary)" }}><ServerCog size={17} /></span>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-[15px] font-semibold" style={{ color: "var(--text-primary)" }}>平台检索兜底</h3>
                <Badge>管理员</Badge>
              </div>
              <p className="mt-1 max-w-[760px] text-[11px] leading-5" style={{ color: "var(--text-tertiary)" }}>
                这是服务器级兼容配置，供没有账号级搜索配置的后台流程使用。修改会影响其他用户，应与账号级搜索分开管理。
              </p>
            </div>
          </div>
          <Button variant="secondary" icon={RefreshCw} size="sm" busy={loading} onClick={() => void load()}>刷新</Button>
        </div>

        <Card padding="p-0" className="overflow-hidden">
          {loading ? (
            <div className="flex min-h-[150px] items-center justify-center gap-2 text-[12px]" style={{ color: "var(--text-tertiary)" }}>
              <Loader2 size={15} className="animate-spin" /> 正在读取平台设置
            </div>
          ) : (
            <div className="divide-y" style={{ borderColor: "var(--border)" }}>
              {platformSettings.map(setting => {
                const copy = FIELD_COPY[setting.key] || { label: setting.key, hint: setting.description };
                const value = edits[setting.key] ?? "";
                const status = setting.configured
                  ? setting.source === "ui" ? "界面配置" : "环境变量"
                  : "未配置";
                return (
                  <div key={setting.key} className="grid items-center gap-3 px-4 py-3 lg:grid-cols-[minmax(220px,.8fr)_minmax(300px,1.2fr)_auto]">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>{copy.label}</span>
                        <Badge tone={setting.configured ? "success" : "neutral"}>{status}</Badge>
                      </div>
                      <p className="mt-1 text-[10.5px] leading-5" style={{ color: "var(--text-tertiary)" }}>{copy.hint}</p>
                    </div>
                    <Field>
                      {setting.key === "search_backend" ? (
                        <select value={value} onChange={event => setEdits(current => ({ ...current, [setting.key]: event.target.value }))}
                          className={inputClass} style={inputStyle}>
                          <option value="">自动选择</option>
                          <option value="baidu">百度千帆</option>
                          <option value="brave">Brave</option>
                          <option value="exa">Exa</option>
                          <option value="gemini">Google Grounding</option>
                          <option value="serper">Serper</option>
                          <option value="tavily">Tavily</option>
                          <option value="doubao">豆包兼容</option>
                          <option value="duckduckgo">DuckDuckGo</option>
                        </select>
                      ) : setting.key === "web_fallback_enabled" ? (
                        <select value={value} onChange={event => setEdits(current => ({ ...current, [setting.key]: event.target.value }))}
                          className={inputClass} style={inputStyle}>
                          <option value="">保持当前设置{setting.configured ? `（${setting.display === "1" ? "已开启" : "已关闭"}）` : ""}</option>
                          <option value="1">开启</option>
                          <option value="0">关闭</option>
                        </select>
                      ) : (
                        <input type="password" autoComplete="off" value={value}
                          placeholder={setting.configured ? `已保存 ${setting.display}；留空不更换` : "填写平台级 API Key"}
                          onChange={event => setEdits(current => ({ ...current, [setting.key]: event.target.value }))}
                          className={inputClass} style={inputStyle} />
                      )}
                    </Field>
                    <Button variant="secondary" icon={Save} size="sm" busy={saving === setting.key}
                      disabled={!value} onClick={() => void save(setting)}>保存</Button>
                  </div>
                );
              })}
              {platformSettings.length === 0 && (
                <div className="px-5 py-10 text-center text-[12px]" style={{ color: "var(--text-tertiary)" }}>
                  当前服务器没有返回可管理的检索配置。
                </div>
              )}
            </div>
          )}
        </Card>

        {message && (
          <div className="mt-3 rounded-xl px-3 py-2 text-[11px]" style={{ color: "var(--text-secondary)", background: "var(--bg-tertiary)" }}>
            {message}
          </div>
        )}

        <Card className="mt-3" padding="p-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-[240px] flex-1">
              <div className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>验证平台兜底链路</div>
              <p className="mt-1 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>发起一次真实服务端检索，不使用模型文本代替执行证据。</p>
            </div>
            <Button variant="secondary" icon={Search} size="sm" busy={testing} onClick={() => void testPlatformSearch()}>测试平台检索</Button>
          </div>
          {testResult && (
            <div className="mt-3 rounded-xl p-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2 text-[11.5px] font-medium"
                style={{ color: testResult.ok ? "var(--success)" : "var(--error)" }}>
                {testResult.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                {testResult.ok ? "平台检索链路可用" : "平台检索链路不可用"}
              </div>
              <pre className="mt-2 max-h-[180px] overflow-auto whitespace-pre-wrap text-[10.5px] leading-5"
                style={{ color: "var(--text-secondary)" }}>{testResult.result}</pre>
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}
