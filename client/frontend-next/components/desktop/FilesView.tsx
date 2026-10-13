"use client";
/** components/desktop/FilesView.tsx — 全屏文件浏览 + 预览（V98 自 DesktopPanel 拆出）。
 *  左列复用 FileBrowser，右侧大区预览，行为与 V97 等价。 */
import { useState } from "react";
import { getLocal, type LocalItem } from "@/lib/desktop";
import { FolderOpen } from "lucide-react";
import { FileBrowser } from "./FileBrowser";

export function FilesView() {
  const [sel, setSel] = useState<LocalItem | null>(null);
  const [pv, setPv] = useState<{ kind?: string; text?: string; dataUrl?: string; error?: string } | null>(null);

  const preview = async (it: LocalItem) => {
    const L = getLocal();
    if (!L) return;
    setSel(it); setPv(null);
    const r = await L.read(it.path);
    if (!r || !r.ok) { setPv({ error: (r && r.error) || "无法预览" }); return; }
    setPv(r);
  };

  return (
    <div className="flex flex-1 min-h-0">
      <div className="w-[260px] flex-shrink-0 flex flex-col" style={{ borderRight: "1px solid var(--border)" }}>
        <FileBrowser selPath={sel?.path} onSelect={preview} />
      </div>
      <div className="flex-1 min-w-0 flex flex-col">
        <div className="px-4 py-2.5 text-[11px] font-mono truncate" style={{ borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          {sel ? sel.path : "选择左侧文件预览 · agent 改完代码在这里直接看"}
        </div>
        <div className="flex-1 overflow-auto">
          {!sel && (
            <div className="h-full flex flex-col items-center justify-center gap-2" style={{ color: "var(--text-tertiary)" }}>
              <FolderOpen size={28} strokeWidth={1.5} />
              <div className="text-[14px] font-semibold" style={{ color: "var(--text-secondary)" }}>本机文件</div>
              <div className="text-[12px]">浏览 / 预览 / 搜索本机文件</div>
            </div>
          )}
          {sel && !pv && <div className="p-6 text-[12px]" style={{ color: "var(--text-tertiary)" }}>读取中…</div>}
          {pv?.error && <div className="p-6 text-[12px]" style={{ color: "var(--text-tertiary)" }}>无法预览：{pv.error}</div>}
          {pv?.kind === "image" && <img src={pv.dataUrl} alt="" className="max-w-full block mx-auto my-5 rounded-xl" style={{ boxShadow: "var(--shadow-lg, 0 8px 28px rgba(0,0,0,.12))" }} />}
          {pv?.kind === "text" && (
            <pre className="m-0 p-5 text-[12px] leading-relaxed whitespace-pre-wrap break-words font-mono" style={{ color: "var(--text-primary)" }}>{pv.text}</pre>
          )}
        </div>
      </div>
    </div>
  );
}
