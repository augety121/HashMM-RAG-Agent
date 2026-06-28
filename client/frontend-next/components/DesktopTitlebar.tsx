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
        position: "fixed", top: 0, left: 0, right: 146, height: 12,
        zIndex: 9999, WebkitAppRegion: "drag",
      } as any}
    />
  );
}
