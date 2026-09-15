/**
 * desktop/services/window-service.js — 窗口服务（V101，从 main.js 抽出）。
 *
 * 窗口管理里**可测的纯决策逻辑**抽到这里（与 electron 解耦）：
 *   - nextToggleAction：Alt+H 切换 → 该 show 还是 hide
 *   - shouldMinimizeToTray：点 X 时该最小化到托盘还是真关闭
 *   - sanitizeBounds：保存的窗口尺寸做下限/合法性兜底
 *   - clampBoundsToDisplays：保存的位置若整块跑到屏幕外（如拔了外接显示器），拉回可见区
 *
 * BrowserWindow 实例仍由 main.js 持有（与全局状态耦合深，不硬拆）；main.js 的切换/关闭/
 * 恢复尺寸三处调用点委托到这里的纯函数，行为不变、逻辑可单测。
 */
"use strict";

const DEFAULT_BOUNDS = { width: 1280, height: 860 };
const MIN = { width: 900, height: 600 };

/** Alt+H 切换：当前可见且非最小化 → 收起；否则 → 显示并聚焦。 */
function nextToggleAction(state = {}) {
  if (state.visible && !state.minimized) return "hide";
  return "show";
}

/** 点 X：非真退出 且 勾了"关闭最小化到托盘" → 最小化（隐藏）；否则真关闭。 */
function shouldMinimizeToTray(isQuitting, trayOnClose) {
  return !isQuitting && !!trayOnClose;
}

/**
 * V103.90 点 X 的三态决策（替代静默的 shouldMinimizeToTray）：
 *   - 真退出（isQuitting）→ "quit"（放行）
 *   - trayOnClose === true  → "tray"（最小化，记住过）
 *   - trayOnClose === false → "quit"（退出，记住过）
 *   - 其它（未设/undefined）→ "ask"（弹窗让用户选）
 * trayOnClose 即用户「记住的选择」：true=最小化、false=退出、未设=每次问。
 */
function decideCloseAction(isQuitting, trayOnClose) {
  if (isQuitting) return "quit";
  if (trayOnClose === true) return "tray";
  if (trayOnClose === false) return "quit";
  return "ask";
}

/** 尺寸兜底：缺失用默认；小于下限提到下限；非数字纠正。 */
function sanitizeBounds(bounds, min = MIN) {
  const b = bounds || {};
  const w = Number.isFinite(b.width) ? b.width : DEFAULT_BOUNDS.width;
  const h = Number.isFinite(b.height) ? b.height : DEFAULT_BOUNDS.height;
  const out = { width: Math.max(min.width, Math.round(w)), height: Math.max(min.height, Math.round(h)) };
  if (Number.isFinite(b.x)) out.x = Math.round(b.x);
  if (Number.isFinite(b.y)) out.y = Math.round(b.y);
  return out;
}

/** 两个矩形是否有交叠（用于判断窗口是否还落在某个显示器的可见区内）。 */
function _intersects(a, b) {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

/**
 * 把窗口边界夹回可见屏幕：若 (x,y,w,h) 与任一显示器工作区都无足够交叠（窗口跑到屏外，
 * 比如拔了外接屏），则在主显示器居中。displays = [{x,y,width,height}]（工作区）。
 * 无坐标（首次启动让系统居中）时原样返回。
 */
function clampBoundsToDisplays(bounds, displays) {
  const b = sanitizeBounds(bounds);
  if (!Number.isFinite(b.x) || !Number.isFinite(b.y)) return b; // 没存位置 → 交给系统居中
  const list = Array.isArray(displays) && displays.length ? displays : [];
  if (!list.length) return b;
  // 要求至少有 80px×80px 的可见交叠，否则视为"跑到屏外"
  const probe = { x: b.x, y: b.y, width: Math.min(b.width, 80), height: Math.min(b.height, 80) };
  const visible = list.some((d) => _intersects(probe, d));
  if (visible) return b;
  // 在主显示器（取第一个）居中
  const primary = list[0];
  return {
    width: b.width, height: b.height,
    x: Math.round(primary.x + (primary.width - b.width) / 2),
    y: Math.round(primary.y + (primary.height - b.height) / 2),
  };
}

class WindowService {
  constructor(opts = {}) { this.name = "window"; this.log = opts.logger || { info() {} }; }
}

module.exports = {
  WindowService, nextToggleAction, shouldMinimizeToTray, decideCloseAction, sanitizeBounds, clampBoundsToDisplays,
  DEFAULT_BOUNDS, MIN,
};
