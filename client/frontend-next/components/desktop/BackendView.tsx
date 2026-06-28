"use client";
/** components/desktop/BackendView.tsx — 后端连接/切换（V103.91 接入 PanelKit 设计系统）。 */
import { useEffect, useState } from "react";
import { getDesktop } from "@/lib/desktop";
import { Plug, Home, Download, Check } from "lucide-react";
import { SemanticCard } from "./SemanticCard";
import { PanelShell, PageHeader, Card, Badge } from "./ui/PanelKit";

/** V103: 按需下载本地 Python 运行时（安装包不再内置，瘦身）。装好后 runtimeDir() 自动指向它，
 *  本地后端零环境直启；不装则后端回退「系统 Python + venv」。后端 IPC：pack:status / pack:install。 */
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
            本地知识库需要 Python 运行时。安装包已不再内置（更小巧），需要时按需下载即可。
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

  return (
    <PanelShell>
      <div style={{ maxWidth: 600 }}>
        <PageHeader icon={Plug} title="后端连接" subtitle="管理与切换 HashMM 后端 · 本地 / 远程"
          actions={<Badge tone={statusTone}><span className="w-1.5 h-1.5 rounded-full" style={{ background: statusDot }} />{statusText}</Badge>} />

        <div className="flex flex-col gap-4">
          <Card>
            <div className="flex items-center gap-2">
              <div className="text-[14px] font-bold" style={{ color: "var(--text-primary)" }}>当前后端</div>
              <span className="flex-1" />
              <span className="w-2 h-2 rounded-full" style={{ background: statusDot }} />
              <span className="text-[11px]" style={{ color: hkind === "ok" ? "var(--success)" : hkind === "offline" ? "var(--error)" : "var(--text-tertiary)" }}>{statusText}</span>
            </div>
            <div className="flex items-center gap-2 mt-2">
              <span className="text-[13px] font-mono truncate" style={{ color: "var(--text-primary)" }}>{short(cur) || "未连接"}</span>
              {ms != null && <span className="text-[10.5px] font-mono flex-shrink-0" style={{ color: msColor(ms) }}>{ms}ms</span>}
            </div>
            {health && health.components && Object.keys(health.components).length > 0 && (
              <div className="mt-3 pt-3 flex flex-col gap-1.5" style={{ borderTop: "1px solid var(--hairline)" }}>
                {Object.entries(health.components as Record<string, string>).slice(0, 5).map(([k, v]) => {
                  const val = String(v);
                  const isErr = /error|fail|unavailable|✗/i.test(val);
                  const isPending = /not loaded|loading|pending|未加载/i.test(val);
                  const hint = /^not loaded$/i.test(val.trim()) ? "（按需加载，有数据时自动就绪）" : "";
                  return (
                    <div key={k} className="flex items-center gap-2 text-[11px]">
                      <span className="flex-shrink-0" style={{ color: "var(--text-tertiary)", minWidth: 64 }}>{COMP_LABEL[k] || k}</span>
                      <span className="font-mono truncate" style={{ color: isErr ? "var(--error)" : isPending ? "var(--text-tertiary)" : "var(--text-secondary)" }}>{val}</span>
                      {hint && <span className="text-[10px] flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>{hint}</span>}
                    </div>
                  );
                })}
              </div>
            )}
            {health && health.features && health.features.preset && (
              <div className="mt-3 pt-3 flex items-center gap-2 text-[11px]" style={{ borderTop: "1px solid var(--hairline)" }}>
                <span className="flex-shrink-0" style={{ color: "var(--text-tertiary)", minWidth: 64 }}>功能档位</span>
                <span className="font-mono" style={{ color: health.features.preset === "basic" ? "var(--text-tertiary)" : "var(--accent)" }}>
                  {(({ basic: "基础", recommended: "推荐", max: "满血" } as Record<string, string>)[health.features.preset]) || health.features.preset}
                  {typeof health.features.advanced_on_count === "number" && ` · ${health.features.advanced_on_count}/${health.features.advanced_total} 项高级功能开启`}
                </span>
              </div>
            )}
            {health && health.features && health.features.preset && (
              <div className="mt-2.5 flex items-center gap-1.5 flex-wrap text-[11px]">
                {([["basic", "基础"], ["recommended", "推荐"], ["max", "全开"]] as const).map(([p, label]) => {
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
                      className="px-3 py-1 rounded-lg transition-colors"
                      style={{ border: "1px solid var(--border)", background: onNow ? "var(--accent)" : "transparent", color: onNow ? "#fff" : "var(--text-secondary)", opacity: switching ? 0.6 : 1 }}>
                      {switching === p ? "切换中…" : label}
                    </button>
                  );
                })}
                <span className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                  {switching ? "正在重启本地后端以应用（约 10–30 秒）…" : "全开＝解锁全部高级检索/记忆能力（更慢更准）"}
                </span>
              </div>
            )}
            {cur && hkind === "loading" && (
              <div className="mt-2 text-[11px]" style={{ color: "var(--warning)" }}>后端正在加载（模型 / 索引），稍候即就绪…</div>
            )}
          </Card>

          <SemanticCard />
          <RuntimePackCard backendReady={hkind === "ok"} />

          <Card>
            <div className="text-[14px] font-bold mb-0.5" style={{ color: "var(--text-primary)" }}>切换后端</div>
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
        </div>
      </div>
    </PanelShell>
  );
}
