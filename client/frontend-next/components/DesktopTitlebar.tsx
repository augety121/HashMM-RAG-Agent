"use client";
/** DesktopTitlebar — V68：hydration-safe 隐形拖拽条。
 *
 * 无缝形态（无可见顶栏），只负责 12px 隐形拖拽区。
 * V67 缺陷修复：isDesktop() 在 SSR=false / 客户端=true，直接条件渲染会造成
 * Next.js hydration 不一致（组件可能不渲染——V66 顶栏失踪的根因）。
 * 标准修法：useEffect 后置判定，SSR 与首次水合一致输出 null。
 */
import { useEffect, useState } from "react";
import { isDesktop } from "@/lib/desktop";

export function DesktopTitlebar() {
  const [show, setShow] = useState(false);
  useEffect(() => { setShow(isDesktop()); }, []);
  if (!show) return null;
  return (
    <div
      id="hashmm-desktop-titlebar"
      style={{
        position: "fixed", top: 0, left: 0,
        // V239: 右边界用 Window Controls Overlay 变量（各 OS/缩放自适应），不再死值 146；
        // 高度收到 8px——足够拖动窗口，又不盖住下方画布工具栏（此前 12px 吞掉工具栏顶部点击）。
        right: "max(146px, calc(100vw - env(titlebar-area-x, 0px) - env(titlebar-area-width, 100vw)))",
        // V241: z-index 降到 1——拖拽区只负责"空白顶栏可拖动"，绝不能盖在任何按钮之上。
        // 此前 9990 高于画布工具栏，其覆盖的顶部 8px 会吞掉工具栏顶部按钮点击（画布按钮点不动根因）。
        height: 6, zIndex: 1, WebkitAppRegion: "drag",
        background: "transparent", border: "none", pointerEvents: "auto",
      } as any}
    />
  );
}
