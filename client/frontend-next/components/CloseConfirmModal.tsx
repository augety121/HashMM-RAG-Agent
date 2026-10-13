"use client";
/** CloseConfirmModal — V103.90 与主界面同风格的"关闭确认"弹窗（替代系统原生蓝框）。
 *  主进程拦截点 X → 经 hashmmDesktop.onConfirmClose 通知 → 这里弹窗 →
 *  用户选「最小化到托盘 / 退出程序 / 取消」(可记住) → closeChoice 回传主进程执行。 */
import { useEffect, useState } from "react";
import { getDesktop } from "@/lib/desktop";
import { X, Minimize2, Power } from "lucide-react";

type Choice = "tray" | "quit" | "cancel";

export function CloseConfirmModal() {
  const [show, setShow] = useState(false);
  const [remember, setRemember] = useState(false);

  useEffect(() => {
    const d = getDesktop();
    if (!d || !d.onConfirmClose) return;
    const off = d.onConfirmClose(() => { setRemember(false); setShow(true); });
    return off;
  }, []);

  if (!show) return null;

  const pick = (choice: Choice) => {
    const d = getDesktop();
    try { d?.closeChoice?.(choice, remember); } catch { /* */ }
    setShow(false);
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center anim-fade-up"
         style={{ background: "rgba(0,0,0,.4)", backdropFilter: "blur(2px)" }}
         onClick={() => pick("cancel")}>
      <div className="relative rounded-2xl px-7 py-6 w-[400px] max-w-[92vw]"
           style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
           onClick={(e) => e.stopPropagation()}>
        <button onClick={() => pick("cancel")} className="absolute right-3.5 top-3.5 p-1 rounded-lg hover:opacity-70"
                style={{ color: "var(--text-tertiary)" }}><X size={16} /></button>

        <h2 className="text-[16px] font-bold mb-1.5" style={{ color: "var(--text-primary)" }}>关闭 HashMM</h2>
        <p className="text-[12.5px] leading-relaxed mb-5" style={{ color: "var(--text-secondary)" }}>
          你想最小化到托盘，还是退出程序？最小化后程序在后台继续运行，可从右下角托盘图标恢复；退出则完全关闭。
        </p>

        <div className="flex flex-col gap-2 mb-4">
          <button onClick={() => pick("tray")}
                  className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl text-[13px] font-medium transition-colors text-white"
                  style={{ background: "var(--accent)" }}>
            <Minimize2 size={15} /> 最小化到托盘
          </button>
          <button onClick={() => pick("quit")}
                  className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl text-[13px] font-medium transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ background: "var(--bg-secondary)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
            <Power size={15} /> 退出程序
          </button>
        </div>

        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-[12px] cursor-pointer select-none" style={{ color: "var(--text-secondary)" }}>
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)}
                   className="w-3.5 h-3.5 cursor-pointer" style={{ accentColor: "var(--accent)" }} />
            记住我的选择，不再询问
          </label>
          <button onClick={() => pick("cancel")} className="text-[12px] px-2.5 py-1 rounded-lg hover:bg-[var(--bg-tertiary)]"
                  style={{ color: "var(--text-tertiary)" }}>取消</button>
        </div>
      </div>
    </div>
  );
}
