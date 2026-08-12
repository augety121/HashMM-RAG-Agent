/**
 * desktop/modules/nav-guard.js — 窗口导航白名单守卫（V306，修 DESK-P0-02 导航面）。
 *
 * 背景：桌面端此前只有 setWindowOpenHandler（挡"新开窗口"），但没有 will-navigate /
 * will-redirect 守卫。这意味着一旦渲染层出现注入（Markdown/SVG/HTML XSS，见 DESK-P0-03），
 * 恶意脚本可以直接 `location = "https://evil.com"` 把**当前窗口**（尤其是带 Node 权限的
 * 隐藏窗口）导航到远程内容，再借宽 IPC 面形成"注入→高权限 IPC→文件/Shell/CU"的可组合链。
 *
 * 本模块提供纯函数 isNavigationAllowed(targetUrl, ctx)：只放行
 *   · file:// 且落在 app 目录内（防目录穿越到任意本地文件）；
 *   · http(s):// 且 origin 命中受信任来源（本地壳层 / 当前后端）；
 *   · about:（空白 iframe 常见）。
 * 其余（data: / javascript: / blob: / 任意远程 origin）一律拒绝。
 *
 * 纯逻辑、零 electron 依赖 → tests-node 直接冒烟。
 */
"use strict";

function _origin(u) {
  try { return new URL(u).origin; } catch { return null; }
}

function _normPath(s) {
  // 统一分隔符 + 去 Windows file:///C:/ 的前导斜杠 + 小写（Windows 路径大小写不敏感）
  return String(s || "")
    .replace(/\\/g, "/")
    .replace(/^\/([A-Za-z]:)/, "$1")
    .toLowerCase();
}

/**
 * @param {string} targetUrl 即将导航到的 URL
 * @param {object} ctx { appDir: string(本地 app 目录绝对路径), trustedOrigins: string[] }
 * @returns {boolean} 是否允许导航
 */
function isNavigationAllowed(targetUrl, ctx) {
  ctx = ctx || {};
  let u;
  try { u = new URL(targetUrl); } catch { return false; }   // 非法 URL 一律拒绝

  const proto = u.protocol;

  if (proto === "file:") {
    if (!ctx.appDir) return true;                            // 未约束时不拦（调用方未提供 appDir）
    let p;
    try { p = decodeURIComponent(u.pathname); } catch { p = u.pathname; }
    // 加边界：appDir 归一后补尾斜杠，避免 ".../desktop" 命中 ".../desktop-evil/..."
    const base = _normPath(ctx.appDir).replace(/\/?$/, "/");
    const path = _normPath(p);
    return path.startsWith(base);
  }

  if (proto === "http:" || proto === "https:") {
    const origins = (ctx.trustedOrigins || []).map(_origin).filter(Boolean);
    return origins.includes(u.origin);
  }

  if (proto === "about:") return true;                       // about:blank 等

  return false;                                              // data:/javascript:/blob:/其它协议：拒绝
}

module.exports = { isNavigationAllowed };
