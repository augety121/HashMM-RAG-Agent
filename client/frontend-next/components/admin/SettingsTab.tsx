"use client";
import { useState, useEffect, useCallback } from "react";
import { Search, Loader2, CheckCircle2, XCircle, Save } from "lucide-react";

interface Setting {
  key: string;
  description: string;
  is_secret: boolean;
  configured: boolean;
  display: string;
  source: string;
}

export function SettingsTab() {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<string>("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; result: string } | null>(null);
  const [msg, setMsg] = useState("");

  const headers = useCallback(() => {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    const t = localStorage.getItem("hmm_token");
    if (t) h["Authorization"] = `Bearer ${t}`;
    return h;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/settings", { headers: headers() });
      const data = await res.json();
      setSettings(data.settings || []);
    } catch {
      setMsg("加载设置失败");
    } finally {
      setLoading(false);
    }
  }, [headers]);

  useEffect(() => { load(); }, [load]);

  const save = async (key: string) => {
    const value = edits[key] ?? "";
    setSaving(key);
    setMsg("");
    try {
      const res = await fetch("/api/admin/settings", {
        method: "POST", headers: headers(),
        body: JSON.stringify({ key, value }),
      });
      if (res.ok) {
        setMsg(`${key} 已保存`);
        setEdits((e) => { const n = { ...e }; delete n[key]; return n; });
        await load();
      } else {
        setMsg("保存失败");
      }
    } catch {
      setMsg("保存失败");
    } finally {
      setSaving("");
    }
  };

  const testSearch = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch("/api/admin/settings/test-search", {
        method: "POST", headers: headers(),
        body: JSON.stringify({ query: "苹果公司 2024 营收" }),
      });
      const data = await res.json();
      setTestResult({ ok: data.ok, result: data.result || "" });
    } catch {
      setTestResult({ ok: false, result: "测试请求失败" });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-[15px] font-semibold flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
          <Search size={18} /> 搜索 / 联网配置
        </h3>
        <p className="text-[12px] mt-1" style={{ color: "var(--text-tertiary)" }}>
          配置联网搜索后端。知识库检索不到时会用这里的搜索补充。推荐 Serper（serper.dev，国内可用，有免费额度）。
        </p>
      </div>

      {msg && <div className="text-[13px] px-3 py-2 rounded-lg" style={{ background: "var(--bg-secondary)" }}>{msg}</div>}

      {loading ? (
        <div className="flex items-center gap-2 text-[13px]" style={{ color: "var(--text-tertiary)" }}>
          <Loader2 size={16} className="animate-spin" /> 加载中...
        </div>
      ) : (
        <div className="space-y-3">
          {settings.map((s) => (
            <div key={s.key} className="p-3 rounded-xl" style={{ border: "1px solid var(--border)" }}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>{s.key}</span>
                {s.configured && (
                  <span className="text-[11px] px-2 py-0.5 rounded-full"
                    style={{ background: s.source === "ui" ? "#dcfce7" : "#fef3c7", color: s.source === "ui" ? "#166534" : "#92400e" }}>
                    {s.source === "ui" ? "已配置(界面)" : "已配置(环境变量)"}
                  </span>
                )}
              </div>
              <p className="text-[11px] mb-2" style={{ color: "var(--text-tertiary)" }}>{s.description}</p>
              <div className="flex gap-2">
                <input
                  type={s.is_secret ? "password" : "text"}
                  placeholder={s.configured ? `当前: ${s.display}` : "未配置"}
                  value={edits[s.key] ?? ""}
                  onChange={(e) => setEdits((p) => ({ ...p, [s.key]: e.target.value }))}
                  className="flex-1 px-3 py-1.5 text-[13px] rounded-lg"
                  style={{ border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)" }}
                />
                <button
                  onClick={() => save(s.key)}
                  disabled={saving === s.key || (edits[s.key] ?? "") === ""}
                  className="px-3 py-1.5 text-[13px] rounded-lg flex items-center gap-1 disabled:opacity-50"
                  style={{ background: "var(--accent)", color: "#fff" }}>
                  {saving === s.key ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                  保存
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="p-4 rounded-xl" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>测试联网搜索</p>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>用一个示例问题验证搜索后端是否真的能用</p>
          </div>
          <button onClick={testSearch} disabled={testing}
            className="px-4 py-2 text-[13px] rounded-lg flex items-center gap-2 disabled:opacity-50"
            style={{ background: "var(--accent)", color: "#fff" }}>
            {testing ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
            测试搜索
          </button>
        </div>
        {testResult && (
          <div className="mt-3 p-3 rounded-lg text-[12px]" style={{ background: "var(--bg-primary)" }}>
            <div className="flex items-center gap-2 mb-2 font-medium" style={{ color: testResult.ok ? "#059669" : "#dc2626" }}>
              {testResult.ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
              {testResult.ok ? "联网搜索可用" : "联网搜索不可用（检查 key 或网络）"}
            </div>
            <pre className="whitespace-pre-wrap" style={{ color: "var(--text-secondary)", maxHeight: 200, overflow: "auto" }}>{testResult.result}</pre>
          </div>
        )}
      </div>
    </div>
  );
}
