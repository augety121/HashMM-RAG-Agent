"use client";
import { useState, useEffect, useCallback } from "react";
import * as api from "@/lib/api";
import { Plus, Trash2, RefreshCw, Pencil, X, Server, CheckCircle2, Plug, AlertCircle } from "lucide-react";

type Cfg = api.MCPServerConfig;

const EMPTY: Cfg = { name: "", transport: "http", endpoint: "", headers: {}, enabled: true };

export function MCPServersPanel() {
  const [servers, setServers] = useState<Cfg[]>([]);
  const [editing, setEditing] = useState<Cfg | null>(null);
  const [headersText, setHeadersText] = useState("{}");
  const [testResult, setTestResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastVerifiedAt, setLastVerifiedAt] = useState<number | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await api.listMcpServers(); setServers(r.servers || []); setLastVerifiedAt(Date.now());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "MCP 服务器列表读取失败");
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  function openNew() { setEditing({ ...EMPTY }); setHeadersText("{}"); setTestResult(null); }
  function openEdit(s: Cfg) { setEditing({ ...s }); setHeadersText(JSON.stringify(s.headers || {}, null, 2)); setTestResult(null); }
  function setField<K extends keyof Cfg>(k: K, v: Cfg[K]) { setEditing(e => e ? { ...e, [k]: v } : e); }

  function buildCfg(): Cfg | null {
    if (!editing) return null;
    let h = {};
    try { h = JSON.parse(headersText || "{}"); } catch { alert("Headers 不是合法 JSON"); return null; }
    return { ...editing, headers: h };
  }

  async function save() {
    const cfg = buildCfg(); if (!cfg) return;
    if (!cfg.name || !cfg.endpoint) { alert("名称和端点不能为空"); return; }
    setBusy(true);
    setError(null); setNotice(null);
    try {
      if (cfg.id) {
        await api.updateMcpServer(cfg.id, cfg);
        setNotice("配置已保存，并取得服务器回执。");
      }
      else {
        const r = await api.createMcpServer(cfg);
        setNotice(r.status === "error"
          ? `配置已保存，但握手失败：${r.discovery_error || "未知错误"}`
          : `已连接，发现 ${r.discovered ?? 0} 个工具`);
      }
      setEditing(null); await load();
    } catch (e) { setError("保存失败：" + (e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  async function runTest() {
    const cfg = buildCfg(); if (!cfg) return;
    setBusy(true); setTestResult(null);
    try {
      const r = await api.testMcpServer(cfg);
      if (r.ok) setTestResult(`连接成功 (${r.elapsed_ms}ms) · 协议 ${r.protocol_version || "未报告"} · ${r.server_info?.name || "MCP Server"}\n发现 ${r.tool_count} 个工具：\n` +
        (r.tools || []).map((t: { name: string; description: string }) => `· ${t.name} — ${t.description}`).join("\n"));
      else setTestResult("连接失败：" + r.error);
    } catch (e) { setTestResult("测试失败：" + String(e)); }
    setBusy(false);
  }
  async function refresh(id?: string) {
    if (!id || actionId) return;
    setActionId(id); setError(null); setNotice(null);
    try { const r = await api.refreshMcpServer(id); setNotice(`刷新完成，服务器确认 ${r.count ?? 0} 个工具`); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "刷新未取得服务端回执"); }
    finally { setActionId(null); }
  }
  async function del(id?: string) {
    if (!id || actionId || !confirm("确认断开并删除该 MCP 服务器？")) return;
    setActionId(id); setError(null); setNotice(null);
    try { await api.deleteMcpServer(id); setNotice("服务器已确认断开并删除。"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "删除未取得服务端回执"); }
    finally { setActionId(null); }
  }

  const card = { background: "var(--bg-secondary)", border: "1px solid var(--border)" };
  const input = "w-full px-2.5 py-1.5 rounded-lg text-[12px]";
  const inputStyle = { background: "var(--bg-tertiary)", border: "1px solid var(--border)", color: "var(--text-primary)" };

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h4 className="text-sm font-semibold flex items-center gap-1.5" style={{ color: "var(--text-primary)" }}>
            <Plug size={14} /> MCP 服务器
          </h4>
          <p className="text-[11px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
            连接标准 MCP 服务器，一次接入一整批工具（对标 Claude Code 连接器）
          </p>
        </div>
        <button onClick={openNew}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium text-white"
          style={{ background: "var(--accent)" }}>
          <Plus size={14} /> 连接 MCP 服务器
        </button>
      </div>

      {error && <div className="mb-3 flex items-start gap-3 rounded-xl px-4 py-3" style={{ background: "rgba(180,35,24,.06)", border: "1px solid rgba(180,35,24,.16)" }}><AlertCircle size={15} className="mt-0.5 flex-shrink-0" style={{ color: "#b42318" }} /><div className="flex-1"><div className="text-[12px]" style={{ color: "#b42318" }}>{error}</div><div className="text-[10.5px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{servers.length ? "当前保留最近一次成功读取的连接。" : "当前没有取得可验证的连接列表。"}</div></div><button onClick={load} disabled={loading} className="inline-flex items-center gap-1 text-[11px]" style={{ color: "var(--accent)" }}><RefreshCw size={12} />重试</button></div>}
      {notice && <div className="mb-3 rounded-xl px-4 py-2.5 text-[11.5px]" style={{ background: "var(--accent-light)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>{notice}</div>}
      {lastVerifiedAt && !error && <div className="mb-3 text-[10px]" style={{ color: "var(--text-tertiary)" }}>本次已验证 · {new Date(lastVerifiedAt).toLocaleTimeString()}</div>}

      <div className="space-y-1">
        {servers.map(s => (
          <div key={s.id} className="flex items-center gap-3 px-4 py-3 rounded-xl"
            style={{ ...card, opacity: s.enabled ? 1 : 0.55 }}>
            <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
              style={{ background: "var(--accent-light)" }}>
              <Server size={14} style={{ color: "var(--accent)" }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-semibold font-mono" style={{ color: "var(--text-primary)" }}>
                {s.name} <span className="text-[10px] font-normal px-1.5 py-0.5 rounded"
                  style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>{s.transport}</span>
                <span className="ml-1.5 text-[10px] font-normal px-1.5 py-0.5 rounded"
                  style={{ background: s.last_status === "error" ? "rgba(180,35,24,.08)" : "var(--bg-tertiary)", color: s.last_status === "error" ? "#b42318" : s.last_status === "ready" ? "#15803d" : "var(--text-tertiary)" }}>
                  {s.last_status === "ready" ? "已协商" : s.last_status === "error" ? "连接异常" : "待检测"}
                </span>
              </div>
              <div className="text-[10px] mt-0.5 truncate" style={{ color: "var(--text-tertiary)" }}>
                {s.endpoint} · {(s.tools_cache?.length ?? 0)} 个工具{s.protocol_version ? ` · ${s.protocol_version}` : ""}
              </div>
              {s.last_status === "error" && s.last_error ? <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "#b42318" }}>{s.last_error}</div> : null}
            </div>
            <button disabled={!!actionId} onClick={() => refresh(s.id)} title="刷新工具" className="disabled:opacity-40" style={{ color: "var(--text-tertiary)" }}><RefreshCw size={14} /></button>
            <button onClick={() => openEdit(s)} title="编辑" style={{ color: "var(--text-tertiary)" }}><Pencil size={15} /></button>
            <button disabled={!!actionId} onClick={() => del(s.id)} title="删除" className="disabled:opacity-40" style={{ color: "var(--text-tertiary)" }}><Trash2 size={15} /></button>
          </div>
        ))}
        {!loading && servers.length === 0 && !error && (
          <div className="text-center py-8 rounded-xl" style={card}>
            <Plug size={28} style={{ color: "var(--text-tertiary)", opacity: 0.3 }} className="mx-auto mb-2" />
            <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>还没有连接 MCP 服务器</p>
          </div>
        )}
      </div>

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.5)" }} onClick={() => setEditing(null)}>
          <div className="w-full max-w-lg rounded-2xl p-5"
            style={{ background: "var(--bg-primary)", border: "1px solid var(--border)" }}
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                {editing.id ? "编辑" : "连接"} MCP 服务器
              </h3>
              <button onClick={() => setEditing(null)} style={{ color: "var(--text-tertiary)" }}><X size={18} /></button>
            </div>

            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>服务器名（字母/数字/下划线）</label>
                  <input className={input} style={inputStyle} value={editing.name}
                    onChange={e => setField("name", e.target.value)} placeholder="weather" />
                </div>
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>传输方式</label>
                  <select className={input} style={inputStyle} value={editing.transport}
                    onChange={e => setField("transport", e.target.value)}>
                    <option value="http">http (JSON-RPC)</option>
                    <option value="sse">sse</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>端点 URL</label>
                <input className={`${input} font-mono`} style={inputStyle} value={editing.endpoint}
                  onChange={e => setField("endpoint", e.target.value)}
                  placeholder="https://mcp.example.com/rpc" />
              </div>
              <div>
                <label className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>请求头 Headers（JSON，放鉴权）</label>
                <textarea className={`${input} font-mono`} style={{ ...inputStyle, minHeight: 50 }}
                  value={headersText} onChange={e => setHeadersText(e.target.value)}
                  placeholder='{"Authorization": "Bearer xxx"}' />
                <div className="mt-1 text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
                  已保存的值显示为 &lt;redacted&gt;；原值不会返回前端，保持该占位符即可保留凭据。
                </div>
              </div>
              <label className="flex items-center gap-2 text-[12px]" style={{ color: "var(--text-secondary)" }}>
                <input type="checkbox" checked={editing.enabled}
                  onChange={e => setField("enabled", e.target.checked)} /> 启用（工具对 AI 可见）
              </label>

              <div>
                <button onClick={runTest} disabled={busy}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px]"
                  style={{ background: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
                  <CheckCircle2 size={12} /> {busy ? "测试中…" : "测试连接"}
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
              <button onClick={save} disabled={busy}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] text-white"
                style={{ background: "var(--accent)" }}>
                <CheckCircle2 size={14} /> 保存并连接
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
