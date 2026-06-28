"use client";
import { useState, useEffect, useCallback } from "react";
import * as api from "@/lib/api";
import { Plus, Trash2, RefreshCw, Pencil, X, Server, CheckCircle2, Plug } from "lucide-react";

type Cfg = api.MCPServerConfig;

const EMPTY: Cfg = { name: "", transport: "http", endpoint: "", headers: {}, enabled: true };

export function MCPServersPanel() {
  const [servers, setServers] = useState<Cfg[]>([]);
  const [editing, setEditing] = useState<Cfg | null>(null);
  const [headersText, setHeadersText] = useState("{}");
  const [testResult, setTestResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { const r = await api.listMcpServers(); setServers(r.servers || []); } catch {}
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
    try {
      if (cfg.id) await api.updateMcpServer(cfg.id, cfg);
      else {
        const r = await api.createMcpServer(cfg);
        alert(`已连接，发现 ${r.discovered ?? 0} 个工具`);
      }
      setEditing(null); load();
    } catch (e) { alert("保存失败：" + String(e)); }
    setBusy(false);
  }
  async function runTest() {
    const cfg = buildCfg(); if (!cfg) return;
    setBusy(true); setTestResult(null);
    try {
      const r = await api.testMcpServer(cfg);
      if (r.ok) setTestResult(`✓ 连接成功 (${r.elapsed_ms}ms)，发现 ${r.tool_count} 个工具：\n` +
        (r.tools || []).map((t: { name: string; description: string }) => `· ${t.name} — ${t.description}`).join("\n"));
      else setTestResult("✗ " + r.error);
    } catch (e) { setTestResult("测试失败：" + String(e)); }
    setBusy(false);
  }
  async function refresh(id?: string) {
    if (!id) return;
    try { const r = await api.refreshMcpServer(id); alert(`刷新完成，${r.count ?? 0} 个工具`); load(); } catch {}
  }
  async function del(id?: string) {
    if (!id || !confirm("确认断开并删除该 MCP 服务器？")) return;
    try { await api.deleteMcpServer(id); load(); } catch {}
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
              </div>
              <div className="text-[10px] mt-0.5 truncate" style={{ color: "var(--text-tertiary)" }}>
                {s.endpoint} · {(s.tools_cache?.length ?? 0)} 个工具
              </div>
            </div>
            <button onClick={() => refresh(s.id)} title="刷新工具" style={{ color: "var(--text-tertiary)" }}><RefreshCw size={14} /></button>
            <button onClick={() => openEdit(s)} title="编辑" style={{ color: "var(--text-tertiary)" }}><Pencil size={15} /></button>
            <button onClick={() => del(s.id)} title="删除" style={{ color: "var(--text-tertiary)" }}><Trash2 size={15} /></button>
          </div>
        ))}
        {servers.length === 0 && (
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
