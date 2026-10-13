"use client";
/** WelcomeModal — V97 安装类型感知欢迎（微信式区分新装/更新）。
 *  fresh（全新安装）→ 欢迎 + 三步上手；update（更新后首启）→ "已更新到 vX"；
 *  normal → 不显示。每个版本只弹一次（localStorage 记 last_welcomed_version）。 */
import { useEffect, useState } from "react";
import { getDesktop } from "@/lib/desktop";
import { X, Sparkles, ArrowUpCircle, Zap, FolderInput, Cpu } from "lucide-react";

type InstallInfo = { type: "fresh" | "update" | "normal"; previousVersion: string; currentVersion: string };

export function WelcomeModal() {
  const [info, setInfo] = useState<InstallInfo | null>(null);
  const [show, setShow] = useState(false);

  useEffect(() => {
    const d = getDesktop() as unknown as { installInfo?: () => Promise<InstallInfo> } | null;
    if (!d?.installInfo) return;
    d.installInfo().then((ii) => {
      if (!ii || ii.type === "normal") return;
      const key = "hmm_welcomed_" + ii.currentVersion;
      if (typeof window !== "undefined" && localStorage.getItem(key) === ii.type) return; // 本版本已弹过
      setInfo(ii); setShow(true);
      try { localStorage.setItem(key, ii.type); } catch { /* */ }
    }).catch(() => { /* */ });
  }, []);

  if (!show || !info) return null;
  const isFresh = info.type === "fresh";
  const close = () => setShow(false);

  return (
    <div className="fixed inset-0 z-[95] flex items-center justify-center anim-fade-up"
         style={{ background: "rgba(0,0,0,.4)", backdropFilter: "blur(2px)" }} onClick={close}>
      <div className="relative rounded-2xl px-7 py-7 w-[420px] max-w-[92vw]"
           style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
           onClick={(e) => e.stopPropagation()}>
        <button onClick={close} className="absolute right-3.5 top-3.5 p-1 rounded-lg hover:opacity-70" style={{ color: "var(--text-tertiary)" }}><X size={16} /></button>

        <div className="flex flex-col items-center mb-5">
          <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3"
               style={{ background: "linear-gradient(135deg, var(--accent), #7c3aed)" }}>
            {isFresh ? <Sparkles size={26} className="text-white" /> : <ArrowUpCircle size={26} className="text-white" />}
          </div>
          <h2 className="text-[18px] font-bold" style={{ color: "var(--text-primary)" }}>
            {isFresh ? "欢迎使用 HashMM" : `已更新到 v${info.currentVersion}`}
          </h2>
          <p className="text-[12px] mt-1" style={{ color: "var(--text-tertiary)" }}>
            {isFresh ? "企业级 RAG + 自我进化 Agent · 本地优先"
                     : (info.previousVersion ? `从 v${info.previousVersion} 升级 · 数据与登录已保留` : "更新完成 · 数据与登录已保留")}
          </p>
        </div>

        {isFresh ? (
          <div className="space-y-2.5 mb-5">
            {[
              { icon: Zap, t: "登录即用", d: "本地模式首次登录 admin / admin123，或注册新账号" },
              { icon: Cpu, t: "完全本地运行", d: "内置运行时独立于系统，不安装任何东西到你的 Python 环境" },
              { icon: FolderInput, t: "文件你做主", d: "对话导出、生成文档可在 设置→存储空间 自选保存位置" },
            ].map(({ icon: Icon, t, d }) => (
              <div key={t} className="flex items-start gap-3 px-3 py-2.5 rounded-xl" style={{ background: "var(--bg-secondary)" }}>
                <Icon size={16} style={{ color: "var(--accent)" }} className="mt-0.5 flex-shrink-0" />
                <div>
                  <div className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>{t}</div>
                  <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{d}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-3 py-2.5 rounded-xl mb-5 text-[12px]" style={{ background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>
            本次更新优化了 Computer Use 稳定性、文件保存位置与跨会话记忆。详情见「关于 → 检查更新」或发行说明。
          </div>
        )}

        <button onClick={close}
                className="w-full py-2.5 rounded-xl text-[14px] font-semibold text-white transition-opacity hover:opacity-90"
                style={{ background: "var(--accent)" }}>
          {isFresh ? "开始使用" : "知道了"}
        </button>
      </div>
    </div>
  );
}
