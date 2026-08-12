/**
 * desktop/services/tray-service.js — 托盘服务（V101，从 main.js 抽出）。
 *
 * 托盘菜单的**模板构建是纯逻辑**（给定状态 + 回调 → 菜单模板数组），可单测、与 electron 解耦。
 * TrayService 管理 Tray 生命周期，electron 的 Tray/Menu/nativeImage 由调用方注入，便于测试。
 * main.js 的 createTray 增量委托到这里，行为不变。
 */
"use strict";

/**
 * 构建托盘菜单模板（纯函数，无 electron 依赖）。
 * @param {object} state    { running, port, trayOnClose }
 * @param {object} handlers { toggle, stopBackend, startBackend, setTrayOnClose, quit }
 * @returns {Array} Menu.buildFromTemplate 可用的模板数组
 */
function buildTrayMenuTemplate(state = {}, handlers = {}) {
  const h = handlers;
  return [
    { label: "显示 / 隐藏窗口（Alt+H）", click: h.toggle },
    { type: "separator" },
    state.running
      ? { label: `停止本地后端（端口 ${state.port}）`, click: h.stopBackend }
      : { label: "启动本地后端", click: h.startBackend },
    { type: "separator" },
    { label: "关闭窗口时最小化到托盘", type: "checkbox", checked: !!state.trayOnClose,
      click: (item) => h.setTrayOnClose && h.setTrayOnClose(item.checked) },
    { label: "保持电脑唤醒（防休眠）", type: "checkbox", checked: !!state.keepAwake,
      click: (item) => h.toggleKeepAwake && h.toggleKeepAwake(item.checked) },
    (state.displays && state.displays.length > 1) ? {
      label: "截屏 / 电脑操作屏幕",
      submenu: [{ label: "主屏（默认）", type: "radio", checked: state.targetDisplay == null,
                  click: () => h.setDisplay && h.setDisplay(null) }]
        .concat(state.displays.slice(1).map((d) => ({
          label: d.label, type: "radio", checked: state.targetDisplay === d.idx,
          click: () => h.setDisplay && h.setDisplay(d.idx),
        }))),
    } : { label: "截屏屏幕：仅一块显示器", enabled: false },
    { label: "桌面安全档位（浏览器确认）",
      submenu: [
        { label: "均衡（默认）——只拦支付/转账类", type: "radio", checked: (state.safetyMode || "balanced") === "balanced",
          click: () => h.setSafetyMode && h.setSafetyMode("balanced") },
        { label: "严格——登录/支付页都先确认", type: "radio", checked: state.safetyMode === "strict",
          click: () => h.setSafetyMode && h.setSafetyMode("strict") },
        { label: "关闭——不做浏览器确认", type: "radio", checked: state.safetyMode === "off",
          click: () => h.setSafetyMode && h.setSafetyMode("off") },
      ] },
    { type: "separator" },
    { label: "退出 HashMM", click: h.quit },
  ];
}

class TrayService {
  /** @param {object} deps { Tray, Menu, nativeImage, iconDataUrl, tooltip, logger } */
  constructor(deps = {}) {
    this.name = "tray";
    this.Tray = deps.Tray; this.Menu = deps.Menu; this.nativeImage = deps.nativeImage;
    this.iconDataUrl = deps.iconDataUrl; this.tooltip = deps.tooltip || "HashMM";
    this.log = deps.logger || { warn() {} };
    this.tray = null;
  }

  /**
   * 创建托盘。getState() 返回当前状态，handlers 是各菜单项回调。
   * onClick 单击行为（默认 handlers.toggle）。
   */
  create(getState, handlers, onClick) {
    if (this.tray) return this.tray;
    if (!this.Tray || !this.Menu) return null;
    try {
      const img = this.nativeImage ? this.nativeImage.createFromDataURL(this.iconDataUrl) : undefined;
      this.tray = new this.Tray(img);
      this.tray.setToolTip(this.tooltip);
      const rebuild = () => this.tray.setContextMenu(this.Menu.buildFromTemplate(buildTrayMenuTemplate(getState(), handlers)));
      rebuild();
      this.tray.on("click", onClick || handlers.toggle);
      this.tray.on("right-click", rebuild); // 打开菜单前刷新状态项
      this._rebuild = rebuild;
      return this.tray;
    } catch (e) { this.log.warn("[tray] 创建失败", { err: (e && e.message) || String(e) }); return null; }
  }

  rebuild() { if (this._rebuild) this._rebuild(); }
  destroy() { if (this.tray) { try { this.tray.destroy(); } catch (_) { /* */ } this.tray = null; } }
}

module.exports = { TrayService, buildTrayMenuTemplate };
