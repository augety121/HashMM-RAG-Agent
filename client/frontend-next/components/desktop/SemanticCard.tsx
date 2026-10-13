"use client";
/** components/desktop/SemanticCard.tsx — 本地语义增强卡（V98 新增）。
 *
 *  把 V77 起就躺在 preload 里的 hashmmSemantic 哑能力接成真功能：
 *  设备能力检测（≥4G 内存 ≥2 核）→ 下载 bge-small-zh INT8（~24MB，国内镜像优先）
 *  → 一键开关"为本地后端提供嵌入服务"。开关持久化在桌面配置
 *  （semanticServe，默认关），下次启动本地后端时主进程注入
 *  HASHMM_LOCAL_EMBED_URL，后端检索主链 Step 6.5 据此做语义重排——
 *  FlagEmbedding 装不上的轻量机型也能享受语义排序，全程不出本机。
 */
import { useEffect, useState } from "react";
import { getSemantic } from "@/lib/desktop";
import { Cpu, Download } from "lucide-react";

export function SemanticCard() {
  const S = getSemantic();
  const [dev, setDev] = useState<any>(undefined);
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pct, setPct] = useState<{ stage: string; pct: number } | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!S) return;
    S.deviceCheck().then(setDev).catch(() => setDev(null));
    S.getServe?.().then(r => setOn(!!(r && r.on))).catch(() => {});
    const off = S.onProgress(m => setPct(m));
    return off;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!S) return null;   // web 端无桥：整卡不渲染

  const download = async () => {
    if (busy) return;
    setBusy(true); setNote(""); setPct({ stage: "vocab", pct: 0 });
    try {
      const r = await S.downloadModel();
      if (!r || !r.ok) setNote((r && (r as any).error) || "下载失败（可稍后重试，自动走镜像）");
      else { setNote("模型已就绪"); S.deviceCheck().then(setDev).catch(() => {}); }
    } catch (e: any) { setNote(e?.message || String(e)); }
    setBusy(false); setPct(null);
  };

  const toggle = async () => {
    if (busy || !S.setServe) return;
    setBusy(true); setNote("");
    try {
      const next = !on;
      const r = await S.setServe(next);
      if (r && r.ok) { setOn(!!r.on); setNote(next ? "已开启 · 重启本地后端后生效" : "已关闭 · 重启本地后端后生效"); }
      else setNote((r && (r as any).error) || "设置失败");
    } catch (e: any) { setNote(e?.message || String(e)); }
    setBusy(false);
  };

  const devOk = !!(dev && dev.ok);
  const ready = !!(dev && dev.modelDownloaded && dev.ortInstalled);

  return (
    <div className="pk-card rounded-2xl p-5" style={{ background: "var(--surface)" }}>
      <div className="text-[15px] font-bold mb-0.5" style={{ color: "var(--text-primary)" }}>本地语义增强</div>
      <div className="text-[11.5px] mb-3" style={{ color: "var(--text-tertiary)" }}>
        桌面小模型（bge-small-zh · INT8 量化 ~24MB）为本地后端的检索结果做语义重排，全程不出本机。
      </div>

      {/* 设备能力 */}
      <div className="flex items-center gap-2 text-[12px] py-2" style={{ borderBottom: "1px dashed var(--border)", color: "var(--text-secondary)" }}>
        <Cpu size={13} style={{ color: "var(--text-tertiary)" }} />
        {dev === undefined && <span style={{ color: "var(--text-tertiary)" }}>检测中…</span>}
        {dev === null && <span style={{ color: "var(--text-tertiary)" }}>无法检测设备</span>}
        {dev && (
          <>
            <span className="font-mono">{dev.memGB}GB 内存 · {dev.cores} 核</span>
            <span className="ml-auto text-[11px]" style={{ color: devOk ? "var(--success, #16A34A)" : "var(--text-tertiary)" }}>
              {devOk ? "满足要求" : (dev.advice || "配置偏低，建议保持关闭")}
            </span>
          </>
        )}
      </div>

      {/* 模型下载 */}
      <div className="flex items-center gap-2 text-[12px] py-2" style={{ borderBottom: "1px dashed var(--border)", color: "var(--text-secondary)" }}>
        <Download size={13} style={{ color: "var(--text-tertiary)" }} />
        <span>嵌入模型</span>
        <span className="ml-auto">
          {dev && dev.modelDownloaded ? (
            <span className="text-[11px]" style={{ color: "var(--success, #16A34A)" }}>已下载</span>
          ) : pct ? (
            <span className="text-[11px] font-mono" style={{ color: "var(--accent)" }}>
              {pct.stage === "vocab" ? "词表" : "模型"} {pct.pct}%
            </span>
          ) : (
            <button onClick={download} disabled={busy}
              className="px-3 py-1 rounded-lg text-[11.5px] font-medium text-white disabled:opacity-50"
              style={{ background: "var(--accent)" }}>下载（~24MB）</button>
          )}
        </span>
      </div>

      {/* 服务开关 */}
      <div className="flex items-center gap-2 text-[12px] py-2" style={{ color: "var(--text-secondary)" }}>
        <span>为本地后端提供嵌入服务</span>
        <button onClick={toggle} disabled={busy || !ready || !S.setServe}
          className="ml-auto relative rounded-full transition-colors disabled:opacity-40"
          style={{ width: 36, height: 20, background: on ? "var(--accent)" : "var(--bg-tertiary)", border: "1px solid var(--border)" }}
          title={!ready ? "需先下载模型" : ""}>
          <span className="absolute top-[1.5px] rounded-full transition-all"
            style={{ width: 15, height: 15, left: on ? 18 : 2, background: "#fff", boxShadow: "0 1px 3px rgba(0,0,0,.25)" }} />
        </button>
      </div>
      {dev && !dev.ortInstalled && (
        <div className="text-[10.5px] mt-1" style={{ color: "var(--text-tertiary)" }}>
          onnxruntime 未安装：正式安装包已内置；开发模式需在 desktop/ 执行 npm i onnxruntime-node。
        </div>
      )}
      {note && <div className="text-[10.5px] mt-1.5" style={{ color: "var(--accent)" }}>{note}</div>}
    </div>
  );
}
