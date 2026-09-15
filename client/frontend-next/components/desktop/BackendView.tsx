"use client";
/** components/desktop/BackendView.tsx — 后端连接（V203 重构）。
 *
 * 布局对标大厂"连接/环境"页三段式：
 *   ① Hero 当前后端卡 —— 状态图标块 + 地址/延迟 + 组件健康 chips + 功能档位切换；
 *   ② 能力区（并排小卡）—— 语义缓存 / 本地运行时；
 *   ③ 切换后端 —— 最近列表（带活性探测）+ 新地址表单 + 断开。
 * 全部逻辑不变：8s 心跳 probe、d.connect 整窗切换、pack:install、setPreset。
 */
import { useEffect, useState } from "react";
import { getDesktop } from "@/lib/desktop";
import * as api from "@/lib/api";
import { Plug, Home, Download, Check, Server, Activity, Cloud, RefreshCw } from "lucide-react";
import { SemanticCard } from "./SemanticCard";
import { PanelShell, PageHeader, Card, Badge, SectionTitle } from "./ui/PanelKit";

/** V334: installer-native 内置已校验的 Python 运行时；下载只作为修复/升级兜底。 */
function RuntimePackCard({ backendReady }: { backendReady?: boolean }) {
  const [installed, setInstalled] = useState<boolean | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const [showAnyway, setShowAnyway] = useState(false);   // 后端已就绪时，下载按钮默认收起

  useEffect(() => {
    const d = getDesktop();
    if (!d || !d.packStatus) { setInstalled(false); return; }
    d.packStatus("python-runtime").then(s => setInstalled(!!(s && s.installed))).catch(() => setInstalled(false));
    const off = d.onPackProgress?.((p) => { if (p && p.id === "python-runtime") setPct(p.pct); });
    return () => { if (off) off(); };
  }, []);

  const install = async () => {
    const d = getDesktop();
    if (!d || !d.packInstall || pct != null) return;
    setErr(""); setPct(0);
    try {
      const r = await d.packInstall("python-runtime");
      if (r && r.ok) { setInstalled(true); setPct(null); }
      else { setErr((r && r.error) || "下载失败"); setPct(null); }
    } catch (e: any) { setErr(e?.message || String(e)); setPct(null); }
  };

  if (installed === null) return null; // 状态未知时不闪

  // 已安装：直接报喜
  if (installed) {
    return (
      <Card>
        <div className="text-[14px] font-bold mb-0.5" style={{ color: "var(--text-primary)" }}>本地运行时</div>
        <div className="flex items-center gap-1.5 text-[12px] mt-1" style={{ color: "var(--success)" }}>
          <Check size={14} /> 已安装 · 本地后端可零环境直接启动
        </div>
      </Card>
    );
  }

  const downloadBtn = (
    pct != null ? (
      <div>
        <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--surface-2)" }}>
          <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: "var(--accent)" }} />
        </div>
        <div className="text-[11px] mt-1.5" style={{ color: "var(--text-tertiary)" }}>正在下载本地运行时… {pct}%</div>
      </div>
    ) : (
      <button onClick={install}
        className="w-full px-3 py-2 rounded-xl text-[12px] font-medium text-white flex items-center justify-center gap-1.5 transition-all hover:brightness-110"
        style={{ background: "var(--accent)", boxShadow: "var(--shadow-sm)" }}>
        <Download size={13} /> 下载本地运行时
      </button>
    )
  );

  // V103.16: 后端已就绪 → 本地运行时只是「想完全离线自带 Python」时才用，不该用会失败的红按钮吓人。
  return (
    <Card>
      <div className="text-[14px] font-bold mb-0.5" style={{ color: "var(--text-primary)" }}>本地运行时</div>
      {backendReady ? (
        <>
          <div className="flex items-center gap-1.5 text-[12px] mt-1 mb-1.5" style={{ color: "var(--success)" }}>
            <Check size={14} /> 你的后端已就绪 —— 当前无需本地运行时
          </div>
          <div className="text-[11.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
            本地运行时只在你想让 HashMM <b>自带 Python、完全离线</b>启动后端时才需要。你现在已连上可用后端，直接用即可。
          </div>
          {!showAnyway ? (
            <button onClick={() => setShowAnyway(true)} className="mt-2 text-[11.5px] underline" style={{ color: "var(--text-tertiary)" }}>
              仍要下载本地运行时（需自托管发布服务器）
            </button>
          ) : (
            <div className="mt-2.5">{downloadBtn}</div>
          )}
        </>
      ) : (
        <>
          <div className="text-[11.5px] mb-3" style={{ color: "var(--text-tertiary)" }}>
            当前安装中未检测到完整运行时。可以下载修复包，或重新运行 V334+ 安装器。
          </div>
          {downloadBtn}
        </>
      )}
      {err && (
        <div className="text-[11px] mt-2 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
          {/fetch failed|ENOTFOUND|ECONNREFUSED|ETIMEDOUT|network|HTTP 404|HTTP 5\d\d/i.test(err)
            ? "运行时下载源未配置：HashMM 默认从你自己的后端/发布服务器取运行时包，而公共环境没有这个包源。你的后端已在运行的话就无需它；要自托管，把环境变量 HASHMM_PACKS_BASE 指向你放置 python-runtime 包的服务器再下载。"
            : err}
        </div>
      )}
    </Card>
  );
}

function CloudWorkspaceProviderCard() {
  const [provider, setProvider] = useState<api.WorkspaceRuntimeProvider | null>(null);
  const [checking, setChecking] = useState(false);
  const [reachable, setReachable] = useState<boolean | null>(null);

  const load = async (probe = false) => {
    setChecking(true);
    try {
      const data = await api.workspaceRuntimeProviders();
      const item = data.providers.find(p => p.id === "cloudflare_computer_worker_shell") || null;
      setProvider(item);
      if (probe && item?.configured) {
        try {
          const health = await api.cloudflareComputerHealth();
          setProvider(health);
          setReachable(Boolean(health.reachable && health.ready));
        } catch { setReachable(false); }
      } else if (!item?.configured) {
        setReachable(null);
      }
    } catch {
      setProvider(null);
      setReachable(false);
    } finally { setChecking(false); }
  };

  useEffect(() => { void load(false); }, []);
  const configured = Boolean(provider?.configured);
  const tone = reachable === true ? "var(--success)" : reachable === false ? "var(--error)" : configured ? "var(--warning)" : "var(--text-tertiary)";
  const label = reachable === true ? "已连接" : reachable === false ? "不可达" : configured ? "待检查" : "未配置";
  const reasons: Record<string, string> = {
    disabled: "服务端未启用",
    missing_url: "缺少 Worker 地址",
    insecure_or_invalid_url: "地址必须使用 HTTPS",
    missing_token: "缺少网关密钥",
    missing_or_weak_handle_secret: "缺少独立工作区映射密钥",
  };

  return (
    <Card>
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: "var(--surface-2)", color: tone }}>
          <Cloud size={18} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <div className="text-[14px] font-bold" style={{ color: "var(--text-primary)" }}>云工作区运行环境</div>
            <Badge tone="neutral">Preview</Badge>
          </div>
          <div className="mt-1 text-[11.5px]" style={{ color: tone }}>{label} · Cloudflare Computer</div>
        </div>
        <button onClick={() => void load(true)} disabled={checking || !configured}
          title={configured ? "检查云运行环境" : "请先在 HashMM 服务端配置环境变量"}
          className="w-8 h-8 rounded-lg flex items-center justify-center disabled:opacity-40"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          <RefreshCw size={13} className={checking ? "animate-spin" : ""} />
        </button>
      </div>
      <div className="mt-3 text-[11px] leading-5" style={{ color: "var(--text-tertiary)" }}>
        {configured
          ? `${provider?.endpoint || "服务端已配置"} · ${provider?.protocol || "hashmm-cloud-workspace/1.0"}`
          : (provider?.problems || []).map(code => reasons[code] || code).join("；") || "后端未返回运行环境配置"}
      </div>
      <div className="mt-2 text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
        云端密钥仅保存在 HashMM 后端，不下发到桌面渲染进程或 App。
      </div>
    </Card>
  );
}

const COMP_LABEL: Record<string, string> = {
  database: "数据库", faiss_index: "知识库", llm: "模型", gpu: "GPU",
  retrieval: "检索", bm25: "BM25", embedding: "向量", redis: "缓存",
};

export function BackendView() {
  const [cur, setCur] = useState("");
  const [ms, setMs] = useState<number | null>(null);
  const [health, setHealth] = useState<any>(null);   // V103: /api/health 正文
  const [hkind, setHkind] = useState<string>("");    // ok | loading | offline
  const [recent, setRecent] = useState<{ url: string; token?: string }[]>([]);
  const [probes, setProbes] = useState<Record<string, number | null>>({});
  const [input, setInput] = useState("");
  const [tokenIn, setTokenIn] = useState("");
  const [busy, setBusy] = useState(false);
  const [switching, setSwitching] = useState("");   // V103.14: 正在切换的功能档位
  const [err, setErr] = useState("");

  useEffect(() => {
    const d = getDesktop();
    if (!d) return;
    d.getConfig().then((cfg: any) => {
      const url = (cfg && cfg.url) || "";
      setCur(url);
      setRecent(((cfg && cfg.recent) || []).slice(0, 5));
      if (url) d.probe(url).then(p => setMs(p && p.ok ? p.ms : null)).catch(() => {});
      for (const r of ((cfg && cfg.recent) || []).slice(0, 5)) {
        d.probe(r.url).then(p => setProbes(prev => ({ ...prev, [r.url]: p && p.ok ? p.ms : null })))
          .catch(() => setProbes(prev => ({ ...prev, [r.url]: null })));
      }
    }).catch(() => {});
  }, []);

  // V103: 当前后端实时心跳——每 8s 探一次，状态随后端起伏即时反映。
  useEffect(() => {
    if (!cur) { setMs(null); setHealth(null); setHkind(""); return; }
    let alive = true;
    const d = getDesktop();
    if (!d) return;
    const tick = () => {
      d.probe(cur).then((p: any) => {
        if (!alive) return;
        if (p && p.ok) {
          setMs(p.ms); setHealth(p.detail || null);
          setHkind(p.detail && p.detail.ready === false ? "loading" : "ok");
        } else { setMs(null); setHealth(null); setHkind("offline"); }
      }).catch(() => { if (alive) { setMs(null); setHkind("offline"); } });
    };
    tick();
    const iv = setInterval(tick, 8000);
    return () => { alive = false; clearInterval(iv); };
  }, [cur]);

  const connect = async (url: string, token: string) => {
    const d = getDesktop();
    if (!d || busy || !url) return;
    setBusy(true); setErr("");
    try {
      const r = await d.connect({ url, token });
      if (!r || !r.success) { setErr((r && r.error) || "连接失败"); setBusy(false); }
      // 成功：主进程整窗切换，本页即将被替换
    } catch (e: any) { setErr(e?.message || String(e)); setBusy(false); }
  };
  const short = (u: string) => u.replace(/^https?:\/\//, "");
  const msColor = (m: number | null) => m == null ? "var(--text-tertiary)" : m < 120 ? "var(--success)" : m < 400 ? "var(--warning)" : "var(--error)";
  const statusDot = hkind === "ok" ? "var(--success)" : hkind === "loading" ? "var(--warning)" : "var(--text-tertiary)";
  const statusTone: "success" | "error" | "warning" | "neutral" =
    hkind === "ok" ? "success" : hkind === "offline" ? "error" : hkind === "loading" ? "warning" : "neutral";
  const statusText = !cur ? "未连接" : hkind === "ok" ? "就绪" : hkind === "loading" ? "加载中…" : hkind === "offline" ? "离线" : "—";
  const inputCls = "px-3 py-2 rounded-[10px] text-[12px] outline-none transition-colors focus:border-[var(--accent)]";
  const inputSty = { background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" } as const;

  const components: [string, string][] = health && health.components ? Object.entries(health.components as Record<string, string>).slice(0, 8).map(([k, v]) => [k, String(v)]) : [];

  return (
    <PanelShell>
      <div style={{ maxWidth: 860 }}>
        <PageHeader icon={Plug} title="后端连接" subtitle="管理与切换 HashMM 后端 · 本地 / 远程"
          actions={<Badge tone={statusTone}><span className="w-1.5 h-1.5 rounded-full" style={{ background: statusDot }} />{statusText}</Badge>} />

        {/* ── ① Hero：当前后端 ── */}
        <Card padding="p-5" className="mb-4">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-[13px] flex items-center justify-center flex-shrink-0"
              style={{ background: hkind === "ok" ? "color-mix(in srgb, var(--success) 13%, transparent)" : "var(--surface-2)" }}>
              <Server size={20} style={{ color: hkind === "ok" ? "var(--success)" : "var(--text-tertiary)" }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-[15px] font-bold font-mono truncate" style={{ color: "var(--text-primary)" }}>{short(cur) || "未连接"}</span>
                {ms != null && <span className="text-[10.5px] font-mono flex-shrink-0 px-1.5 py-0.5 rounded-md" style={{ color: msColor(ms), background: "var(--surface-2)" }}>{ms}ms</span>}
              </div>
              <div className="flex items-center gap-1.5 mt-1 text-[11px]" style={{ color: hkind === "ok" ? "var(--success)" : hkind === "offline" ? "var(--error)" : "var(--text-tertiary)" }}>
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: statusDot }} />
                {statusText}{cur && hkind === "loading" && " —— 后端正在加载模型 / 索引，稍候即就绪"}
              </div>
            </div>
          </div>

          {/* 组件健康 chips */}
          {components.length > 0 && (
            <div className="mt-4 pt-3.5 flex flex-wrap gap-1.5" style={{ borderTop: "1px solid var(--hairline)" }}>
              {components.map(([k, val]) => {
                const isErr = /error|fail|unavailable/i.test(val);
                const isPending = /not loaded|loading|pending|未加载/i.test(val);
                const dot = isErr ? "var(--error)" : isPending ? "var(--text-tertiary)" : "var(--success)";
                const hint = /^not loaded$/i.test(val.trim()) ? "按需加载" : val;
                return (
                  <span key={k} title={`${COMP_LABEL[k] || k}：${val}${isPending ? "（有数据时自动就绪）" : ""}`}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10.5px]"
                    style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                    <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: dot }} />
                    <span style={{ color: "var(--text-tertiary)" }}>{COMP_LABEL[k] || k}</span>
                    <span className="font-mono truncate" style={{ maxWidth: 120 }}>{hint}</span>
                  </span>
                );
              })}
            </div>
          )}

          {/* 功能档位 */}
          {health && health.features && health.features.preset && (
            <div className="mt-3.5 pt-3.5 flex items-center gap-2 flex-wrap" style={{ borderTop: "1px solid var(--hairline)" }}>
              <span className="inline-flex items-center gap-1.5 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                <Activity size={12} /> 功能档位
                {typeof health.features.advanced_on_count === "number" && (
                  <span className="font-mono">{health.features.advanced_on_count}/{health.features.advanced_total} 项高级功能开启</span>
                )}
              </span>
              <span className="flex-1" />
              <div className="inline-flex rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
                {([["basic", "基础"], ["recommended", "推荐"], ["max", "全开"]] as const).map(([p, label], i) => {
                  const onNow = health.features.preset === p;
                  return (
                    <button key={p} disabled={!!switching}
                      onClick={async () => {
                        const d = getDesktop();
                        if (!d || !(d as any).setPreset) { setErr("此版本桌面端不支持 UI 切档位，请重装"); return; }
                        setSwitching(p);
                        try { await (d as any).setPreset(p); } catch (_e) { /* 忽略，下方提示重启中 */ }
                        setTimeout(() => setSwitching(""), 9000);
                      }}
                      className="px-3 py-1 text-[11px] transition-colors"
                      style={{ background: onNow ? "var(--accent)" : "transparent", color: onNow ? "#fff" : "var(--text-secondary)", borderLeft: i ? "1px solid var(--border)" : "none", opacity: switching ? 0.6 : 1 }}>
                      {switching === p ? "切换中…" : label}
                    </button>
                  );
                })}
              </div>
              <span className="w-full text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                {switching ? "正在重启本地后端以应用（约 10–30 秒）…" : "全开＝解锁全部高级检索/记忆能力（更慢更准）"}
              </span>
            </div>
          )}
        </Card>

        {/* ── ② 能力区 ── */}
        <div className="grid gap-4 mb-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
          <SemanticCard />
          <RuntimePackCard backendReady={hkind === "ok"} />
          <CloudWorkspaceProviderCard />
        </div>

        {/* ── ③ 切换后端 ── */}
        <SectionTitle>切换后端</SectionTitle>
        <Card>
          <div className="text-[11.5px] mb-3" style={{ color: "var(--text-tertiary)" }}>连上即整窗切换到该后端的工作台。直接填 IP:端口。</div>
          {recent.map(r => (
            <button key={r.url} onClick={() => connect(r.url, r.token || "")}
              className="w-full flex items-center gap-2 px-3 py-2 mb-1.5 rounded-xl text-left transition-colors hover:bg-[var(--surface-2)]"
              style={{ border: "1px solid var(--border)" }}>
              <Plug size={12} style={{ color: "var(--text-tertiary)" }} />
              <span className="flex-1 text-[12px] font-mono truncate" style={{ color: "var(--text-primary)" }}>{short(r.url)}</span>
              <span className="text-[10px] font-mono" style={{ color: probes[r.url] != null ? "var(--success)" : "var(--text-tertiary)" }}>
                {probes[r.url] != null ? probes[r.url] + "ms" : probes[r.url] === null ? "离线" : "…"}
              </span>
            </button>
          ))}
          <div className="flex gap-1.5 mt-2">
            <input value={input} onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") connect(input.trim(), tokenIn.trim()); }}
              placeholder="新地址 IP:端口" className={`flex-1 font-mono ${inputCls}`} style={inputSty} />
            <input value={tokenIn} onChange={e => setTokenIn(e.target.value)} type="password"
              placeholder="令牌" className={`w-[90px] ${inputCls}`} style={inputSty} />
            <button onClick={() => connect(input.trim(), tokenIn.trim())} disabled={busy}
              className="px-4 py-2 rounded-[10px] text-[12px] font-medium text-white disabled:opacity-50 transition-all hover:brightness-110"
              style={{ background: "var(--accent)", boxShadow: "var(--shadow-sm)" }}>
              {busy ? "连接中…" : "连接"}
            </button>
          </div>
          {err && <div className="text-[11px] mt-2" style={{ color: "var(--error)" }}>{err}</div>}
          <button onClick={() => getDesktop()?.goLocal?.()}
            className="w-full mt-3 px-3 py-2 rounded-xl text-[11.5px] flex items-center justify-center gap-1.5 transition-colors hover:bg-[var(--surface-2)]"
            style={{ border: "1px dashed var(--border)", color: "var(--text-tertiary)" }}>
            <Home size={12} /> 断开连接 · 回连接页
          </button>
        </Card>

        <SectionTitle>会话运维</SectionTitle>
        <SessionOpsCard inputCls={inputCls} inputSty={inputSty} />
      </div>
    </PanelShell>
  );
}

/** V204 会话运维（对标 Qoder Cloud Agents）：
 *  ① 运行时补丁——运行中的会话直接改 模型/温度/临时指令/工具白黑名单，PATCH 后下一轮生效、上下文不丢；
 *  ② 诊断助手——会话跑挂/卡住时一键收集 turns/tool_calls/trace，识别报错、推断根因、给修复建议，报告可复制。 */
function SessionOpsCard({ inputCls, inputSty }: { inputCls: string; inputSty: React.CSSProperties }) {
  const [convs, setConvs] = useState<Array<{ id: string; title?: string }>>([]);
  const [cid, setCid] = useState("");
  const [model, setModel] = useState("");
  const [temp, setTemp] = useState("");
  const [sysAppend, setSysAppend] = useState("");
  const [allow, setAllow] = useState("");
  const [deny, setDeny] = useState("");
  const [busy, setBusy] = useState<"" | "load" | "patch" | "diag">("");
  const [msg, setMsg] = useState("");
  const [report, setReport] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const r = await api.listConversations();
        const list = (r?.conversations || []).slice(0, 20).map((c: { id: string; title?: string }) => ({ id: c.id, title: c.title }));
        setConvs(list);
        if (list[0] && !cid) setCid(list[0].id);
      } catch { /* 离线时留空，可手填 */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadRuntime(id: string) {
    setCid(id); setMsg(""); setReport("");
    if (!id) return;
    setBusy("load");
    try {
      const r = await api.getSessionRuntime(id);
      const ov = r?.overrides || {};
      setModel(ov.model || ""); setTemp(ov.temperature != null ? String(ov.temperature) : "");
      setSysAppend(ov.system_append || "");
      setAllow((ov.tools_allow || []).join(", ")); setDeny((ov.tools_deny || []).join(", "));
    } catch { setMsg("读取补丁失败：确认后端已升级到 V204"); }
    setBusy("");
  }

  async function doPatch() {
    if (!cid) { setMsg("先选/填一个会话 ID"); return; }
    setBusy("patch"); setMsg("");
    const p: Record<string, unknown> = {
      model: model.trim() || null,
      temperature: temp.trim() === "" ? null : Number(temp),
      system_append: sysAppend.trim() || null,
      tools_allow: allow.trim() ? allow.split(",").map(s => s.trim()).filter(Boolean) : null,
      tools_deny: deny.trim() ? deny.split(",").map(s => s.trim()).filter(Boolean) : null,
    };
    try {
      const r = await api.patchSessionRuntime(cid, p);
      setMsg(r?.ok ? "已生效：该会话下一轮 turn 起按新配置运行（上下文不丢）" : "保存失败");
    } catch { setMsg("保存失败：网络或后端版本问题"); }
    setBusy("");
  }

  async function doClear() {
    if (!cid) return;
    setBusy("patch");
    try { await api.clearSessionRuntime(cid); setModel(""); setTemp(""); setSysAppend(""); setAllow(""); setDeny(""); setMsg("已清空该会话全部补丁"); } catch { setMsg("清空失败"); }
    setBusy("");
  }

  async function doDiagnose() {
    if (!cid) { setMsg("先选/填一个会话 ID"); return; }
    setBusy("diag"); setMsg(""); setReport("");
    try {
      const r = await api.diagnoseConversation(cid);
      setReport(r?.report_md || "");
      if (!r?.report_md) setMsg("诊断没有返回报告");
    } catch { setMsg("诊断失败：确认后端已升级到 V204"); }
    setBusy("");
  }

  return (
    <Card>
      <div className="flex items-center gap-2 mb-3">
        <Activity size={14} style={{ color: "var(--accent)" }} />
        <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>运行时补丁 & 诊断</div>
        <Badge tone="neutral">下一轮生效 · 上下文不丢</Badge>
      </div>
      <div className="flex gap-1.5 mb-2">
        <select value={cid} onChange={e => loadRuntime(e.target.value)}
          className={`flex-1 ${inputCls}`} style={inputSty}>
          <option value="">选择会话（或在右侧手填 ID）</option>
          {convs.map(c => <option key={c.id} value={c.id}>{(c.title || "未命名").slice(0, 24)} · {c.id.slice(0, 8)}</option>)}
        </select>
        <input value={cid} onChange={e => setCid(e.target.value)} placeholder="会话 ID"
          className={`w-[150px] font-mono ${inputCls}`} style={inputSty} />
      </div>
      <div className="grid grid-cols-2 gap-1.5 mb-2">
        <input value={model} onChange={e => setModel(e.target.value)} placeholder="模型偏好（可空）" className={inputCls} style={inputSty} />
        <input value={temp} onChange={e => setTemp(e.target.value)} placeholder="温度 0~2（可空）" className={inputCls} style={inputSty} />
        <input value={allow} onChange={e => setAllow(e.target.value)} placeholder="tools_allow 逗号分隔（空=不限）" className={inputCls} style={inputSty} />
        <input value={deny} onChange={e => setDeny(e.target.value)} placeholder="tools_deny 逗号分隔" className={inputCls} style={inputSty} />
      </div>
      <textarea value={sysAppend} onChange={e => setSysAppend(e.target.value)} rows={2}
        placeholder="system_append：本会话临时指令（A/B 调 prompt 主力，留空=删除）"
        className={`w-full resize-y ${inputCls}`} style={inputSty} />
      <div className="flex gap-1.5 mt-2">
        <button onClick={doPatch} disabled={busy !== ""}
          className="px-4 py-2 rounded-[10px] text-[12px] font-medium text-white disabled:opacity-50 transition-all hover:brightness-110"
          style={{ background: "var(--accent)", boxShadow: "var(--shadow-sm)" }}>
          {busy === "patch" ? "应用中…" : "应用补丁"}
        </button>
        <button onClick={doClear} disabled={busy !== ""}
          className="px-3 py-2 rounded-[10px] text-[12px] transition-colors hover:bg-[var(--surface-2)]"
          style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>清空</button>
        <div className="flex-1" />
        <button onClick={doDiagnose} disabled={busy !== ""}
          className="px-4 py-2 rounded-[10px] text-[12px] font-medium transition-colors hover:bg-[var(--surface-2)]"
          style={{ border: "1px solid var(--accent)", color: "var(--accent)" }}>
          {busy === "diag" ? "诊断中…" : "一键诊断"}
        </button>
      </div>
      {msg && <div className="text-[11px] mt-2" style={{ color: "var(--text-tertiary)" }}>{msg}</div>}
      {report && (
        <div className="mt-3 rounded-xl p-3" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
          <div className="flex items-center justify-between mb-2">
            <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>诊断报告</div>
            <button onClick={() => { try { navigator.clipboard.writeText(report); setMsg("报告已复制，可直接贴工单/群里"); } catch { setMsg("复制失败"); } }}
              className="px-2.5 py-1 rounded-lg text-[11px] transition-colors hover:bg-[var(--surface-1)]"
              style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}>复制</button>
          </div>
          <pre className="text-[11px] whitespace-pre-wrap leading-relaxed max-h-[300px] overflow-auto"
            style={{ color: "var(--text-secondary)", fontFamily: "inherit" }}>{report}</pre>
        </div>
      )}
    </Card>
  );
}
