"use client";
/** AboutModal — 桌面端「关于 HashMM」（V91，对标大厂客户端的关于页）。
 *  版本来自 Electron 主进程（hashmmDesktop.appVersion），
 *  「检查更新」走既有 electron-updater 通道（hashmm:checkUpdate IPC）。 */
import { useEffect, useState } from "react";
import { X, RefreshCw, Loader2 } from "lucide-react";
import { getDesktop } from "@/lib/desktop";
import HashMascot from "./HashMascot";

export function AboutModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [version, setVersion] = useState<string>("");
  const [platform, setPlatform] = useState<string>("");
  const [checking, setChecking] = useState(false);
  const [updateMsg, setUpdateMsg] = useState<string>("");

  useEffect(() => {
    if (!open) return;
    const d = getDesktop() as unknown as {
      appVersion?: () => Promise<string>; platform?: string;
    } | null;
    setPlatform(d?.platform || "");
    d?.appVersion?.().then((v) => setVersion(v || "")).catch(() => setVersion(""));
  }, [open]);

  async function checkUpdate() {
    const d = getDesktop() as unknown as { checkUpdate?: () => Promise<{ ok: boolean; message: string }> } | null;
    if (!d?.checkUpdate) { setUpdateMsg("当前环境不支持自动更新"); return; }
    setChecking(true); setUpdateMsg("");
    try {
      const r = await d.checkUpdate();
      setUpdateMsg(r?.message || (r?.ok ? "已是最新版本" : "检查失败"));
    } catch (e) {
      setUpdateMsg("检查失败：" + ((e as Error)?.message || e));
    }
    setChecking(false);
  }

  if (!open) return null;
  const platformName = platform === "win32" ? "Windows" : platform === "darwin" ? "macOS" : platform || "—";

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center" style={{ background: "rgba(0,0,0,.35)" }} onClick={onClose}>
      <div
        className="rounded-2xl p-6 w-[360px] anim-fade-up"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>关于</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:opacity-70" style={{ color: "var(--text-tertiary)" }}><X size={16} /></button>
        </div>

        <div className="flex flex-col items-center gap-2 mb-5">
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <HashMascot size={52} />
          </div>
          <div className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>HashMM</div>
          <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>企业级 RAG + 自我进化 Agent · 本地优先</div>
        </div>

        <div className="rounded-xl px-4 py-3 mb-3 flex items-center justify-between" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
          <div>
            <div className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>当前版本 {version ? `v${version}` : "—"}</div>
            <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{platformName} 桌面版</div>
          </div>
          <button
            onClick={checkUpdate}
            disabled={checking}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium text-white disabled:opacity-60"
            style={{ background: "var(--accent)" }}
          >
            {checking ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} 检查更新
          </button>
        </div>
        {updateMsg && (
          <div className="text-[11px] px-1 mb-1" style={{ color: "var(--text-secondary)" }}>{updateMsg}</div>
        )}
        <div className="text-center text-[10px] mt-3" style={{ color: "var(--text-tertiary)" }}>本地数据存储于本机 · LLM 走你配置的 API</div>
      </div>
    </div>
  );
}
