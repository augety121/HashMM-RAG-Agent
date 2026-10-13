/**
 * desktop/modules/ipc-guard.js — IPC sender 校验 + 敏感通道分类（V306，修 DESK-P0-05 / REM-08）。
 *
 * 背景：contextBridge/preload 暴露了文件、Shell、终端、Computer Use、远控等大量能力，但 150+
 * 个 ipcMain 处理器**没有统一的 sender 校验**。一旦渲染层被注入（Markdown/SVG/HTML XSS），
 * 注入脚本就能调用这些高权限 IPC，形成"注入→高权限 IPC→文件/Shell/CU"的链。
 *
 * 本模块提供纯逻辑，供 main.js 在**单一集中处**（monkey-patch ipcMain.handle/on）拦截：
 *   · isSensitiveChannel(channel)：该通道是否属于"写文件/Shell/终端/CU/远控"等高权限类；
 *   · isSenderTrusted(senderUrl, ctx)：sender 页面是否可信（app 目录内 file:// 或受信后端 origin）；
 *   · decide(channel, senderUrl, ctx)：{allowed, sensitive, reason} —— 敏感通道 sender 不可信=拒绝，
 *     非敏感通道一律放行（零行为变化），sender 无法判定时放行但可记日志（不因判定失败弄坏 App）。
 *
 * 复用 nav-guard 的同源/目录判定口径，避免两套实现漂移。纯逻辑、零 electron 依赖 → tests-node 直跑。
 */
"use strict";

let _isNavAllowed;
try { _isNavAllowed = require("./nav-guard").isNavigationAllowed; } catch (_e) { _isNavAllowed = null; }

// 高权限通道前缀（写文件 / Shell / 终端 / Computer Use / 远控）
const _SENSITIVE_PREFIXES = ["cu:", "browser:", "shell:", "term:", "fs:", "git:", "ckpt:", "localrag:", "remote-host", "remote:", "workspace-cache:", "auth-session:"];
// 高权限具名通道（前缀不好覆盖的混合类）
const _SENSITIVE_EXACT = new Set([
  "local:write", "local:read", "local:grep", "local:search", "local:list", "local:roots", "local:recent",
  "files:save", "files:openPath", "files:revealInFolder", "files:chooseSaveDir", "files:setSaveMode",
  "files:officeHandoffOpen", "files:officeHandoffStatus", "files:officeHandoffRead", "files:officeHandoffAcknowledge",
  "export:savePdf", "export:saveText",
  "config:exportToFile", "config:importFromFile",
  "conv:save", "hashmm:openTerminal", "semantic:downloadModel",
  "project:pickSourceFolders", "project:activateSource",
  "remote-handoff", "app:desktopPromptDecision", "app:desktopPromptReady", "app:desktopPromptList", "app:desktopPromptPresent", "app:closeChoice",
]);

function isSensitiveChannel(channel) {
  const ch = String(channel || "");
  if (_SENSITIVE_EXACT.has(ch)) return true;
  return _SENSITIVE_PREFIXES.some((p) => ch.startsWith(p));
}

/** sender 页面是否可信：app 目录内 file:// 或受信后端/壳层 origin。 */
function isSenderTrusted(senderUrl, ctx) {
  if (!senderUrl) return false;
  if (typeof _isNavAllowed === "function") return _isNavAllowed(senderUrl, ctx || {});
  // nav-guard 不可用时的保守内联：仅放行 app 目录内 file://
  try {
    const u = new URL(senderUrl);
    return u.protocol === "file:";
  } catch (_e) { return false; }
}

/**
 * @param {string} channel
 * @param {string|null} senderUrl  event.senderFrame?.url
 * @param {object} ctx { appDir, trustedOrigins }
 * @returns {{allowed:boolean, sensitive:boolean, reason:string}}
 */
function decide(channel, senderUrl, ctx) {
  const sensitive = isSensitiveChannel(channel);
  if (!sensitive) return { allowed: true, sensitive: false, reason: "" };
  // V308 修 P0-5（fail-closed）：敏感通道遇到【无法判定的 sender】此前是 allowed:true
  // （fail-open）——注入脚本从一个 sender 信息缺失的 frame 发起，即可绕过校验调用
  // 文件/Shell/CU/远控。安全边界必须 fail-closed：sender 未知或不可信 → 一律拒绝。
  // 合法窗口都有明确的 file:// URL，能被 nav-guard 判为可信；判不出 = 不放行。
  if (senderUrl == null || senderUrl === "") {
    return { allowed: false, sensitive: true, reason: "sender-unknown-denied" };
  }
  if (isSenderTrusted(senderUrl, ctx)) return { allowed: true, sensitive: true, reason: "" };
  return { allowed: false, sensitive: true, reason: "untrusted-sender" };
}

function _senderUrlOf(event) {
  try {
    return (event && event.senderFrame && event.senderFrame.url)
      || (event && event.sender && typeof event.sender.getURL === "function" && event.sender.getURL())
      || null;
  } catch (_e) { return null; }
}

/**
 * 在给定 ipcMain 上安装 sender 校验：monkey-patch handle/on，敏感通道拒绝不可信 sender。
 * 抽成可导出函数后，wrapping 逻辑本身可用 mock ipcMain 单测（见 test_ipc_contract.js）。
 * @param {object} ipcMain  具备 handle/on 的对象（真 electron.ipcMain 或测试 mock）
 * @param {function} ctxProvider  () => { appDir, trustedOrigins }（惰性求值）
 * @param {object} [log]  可选日志器（默认 console）
 * @returns {{deniedCount: () => number}}  可观测：被拒次数（测试/监控用）
 */
function installGuard(ipcMain, ctxProvider, log) {
  const logger = log || console;
  let denied = 0;
  const ctxOf = typeof ctxProvider === "function" ? ctxProvider : () => ({});

  const origHandle = ipcMain.handle.bind(ipcMain);
  ipcMain.handle = (channel, listener) => origHandle(channel, (event, ...args) => {
    const d = decide(channel, _senderUrlOf(event), ctxOf());
    if (!d.allowed) {
      denied++;
      try { logger.error(`[ipc-guard] 拒绝敏感通道 ${channel}（${d.reason}）`); } catch (_e) { /* */ }
      return { ok: false, error: "IPC 来源未通过安全校验（sender 不可信）" };
    }
    return listener(event, ...args);
  });

  const origOn = ipcMain.on.bind(ipcMain);
  ipcMain.on = (channel, listener) => origOn(channel, (event, ...args) => {
    const d = decide(channel, _senderUrlOf(event), ctxOf());
    if (!d.allowed) {
      denied++;
      try { logger.error(`[ipc-guard] 拒绝敏感通道(on) ${channel}（${d.reason}）`); } catch (_e) { /* */ }
      return;
    }
    return listener(event, ...args);
  });

  return { deniedCount: () => denied };
}

module.exports = { isSensitiveChannel, isSenderTrusted, decide, installGuard, _senderUrlOf, _SENSITIVE_EXACT, _SENSITIVE_PREFIXES };
