"use client";
/** components/desktop/util.ts — 桌面端视图共享工具（V98 模块化拆分）。
 *  原 DesktopPanel.tsx 顶部的零散函数集中于此，FilesView/TerminalView/
 *  WorkbenchView 等共用。 */

/** 主终端会话 id：终端视图与工作台共享同一个 PTY（main.js 对同 id 复用+回放） */
export const TID = "hashmm-main";

export function fmtSize(b: number) {
  return b > 1048576 ? (b / 1048576).toFixed(1) + "M" : b > 1024 ? (b / 1024).toFixed(0) + "K" : b + "B";
}
export function fmtTok(n: number) {
  return n > 1e6 ? (n / 1e6).toFixed(2) + "M" : n > 1e3 ? (n / 1e3).toFixed(1) + "K" : String(n || 0);
}

export function loadScript(src: string): Promise<void> {
  return new Promise((res, rej) => {
    if (document.querySelector(`script[src="${src}"]`)) return res();
    const s = document.createElement("script");
    s.src = src; s.onload = () => res(); s.onerror = () => rej(new Error("load " + src));
    document.head.appendChild(s);
  });
}
export function loadCss(href: string) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href;
  document.head.appendChild(l);
}

/** 拼本机路径（跟随浏览目录的分隔符风格，Windows 反斜杠） */
export function joinPath(dir: string, name: string) {
  const sep = dir.includes("\\") ? "\\" : "/";
  return dir.replace(/[\\/]+$/, "") + sep + name;
}
