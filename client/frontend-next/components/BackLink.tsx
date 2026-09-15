"use client";
/** BackLink — V103.90 协议/隐私页的"返回"链接。
 *  这两个页面是从主界面 window.open 弹出的独立窗口；点返回应「关闭本窗口」回到主界面，
 *  而不是把 href="/" 整个 app 重新加载进这个小窗（那会看着像又开了一个软件实例 = bug）。 */
import type { CSSProperties, MouseEvent } from "react";

export function BackLink({ className, style }: { className?: string; style?: CSSProperties }) {
  const goBack = (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    if (typeof window === "undefined") return;
    window.close();
    // 兜底：极少数环境下 window.close 对非脚本打开的窗口无效 → 再退回首页，避免点了没反应
    setTimeout(() => { try { if (!window.closed) window.location.href = "/"; } catch { /* */ } }, 150);
  };
  return <a href="/" onClick={goBack} className={className} style={style}>← 返回 HashMM-RAG</a>;
}
