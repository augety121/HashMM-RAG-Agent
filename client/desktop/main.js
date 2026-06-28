// HashMM Desktop — Electron main process (thin client) · v1.1
//
// 瘦客户端定位不变（后端绑死 GPU，exe 只装 UI 外壳，运行时连远程后端）。
// v1.1 产品化升级（修复 + 体验，对标成熟桌面产品的标准要素）：
//   1. 【修 bug】远程加载失败（后端中途挂/断网）→ 自动回连接页并提示，
//      此前是白屏死页，用户只能强杀进程。
//   2. 单实例锁：重复双击图标聚焦已开窗口，不再开第二个。
//   3. 窗口状态记忆：位置/大小存配置，下次原样恢复。
//   4. 中文应用菜单：切换后端 / 刷新 / 缩放 / 全屏 / 开发者工具 / 关于。
//      （"切换后端"从此有可见入口，不再只靠 IPC reset()）
//   5. 多后端管理：最近连接列表（最多 5 个），连接页一键选择。
//
// 配置仍存 userData/hashmm-config.json（非 localStorage）。
// 静态验证：node --check 通过；实跑/打包需在有桌面环境的机器上（见 README）。

const { app, BrowserWindow, Menu, dialog, shell, ipcMain, session } = require("electron");
const path = require("path");
const http = require("http");
const https = require("https");
const fs = require("fs");
const os = require("os");

// ── V99 全局异常网（必须在任何业务 require 之前注册）──
// V98 事故：modules/semantic-serve 漏打进 app.asar，顶层 require 直接抛，
// 而此前的 uncaughtException 监听器注册在文件 1500+ 行、晚于业务 require，
// 完全兜不住——用户看到的是 Electron 默认的英文崩溃框（无法理解、无法自救）。
// 现在把网前置到最顶端，并在 GUI 就绪后用中文对话框呈现，附"打开日志"。
let __earlyCrash = null;
// V100: 结构化日志系统（对标 Marvis logs/）。懒加载单例——userData 就绪后落盘到
// logs/hashmm.log（分级/轮转/脱敏），未就绪时仅控制台+环形缓冲。全 try/catch。
let __logger = null;
function __log() {
  if (__logger) return __logger;
  try {
    const { createLogger } = require("./logging/logger");
    let dir = null;
    try { dir = path.join(app.getPath("userData"), "logs"); } catch (_) { /* 未就绪 */ }
    __logger = createLogger({ name: "hashmm", dir, file: "hashmm.log", maxSizeBytes: 5 * 1024 * 1024, maxFiles: 5 });
  } catch (_) { __logger = { error() {}, warn() {}, info() {}, child() { return this; }, recent() { return []; } }; }
  return __logger;
}

function __logCrash(tag, err) {
  const line = `[${new Date().toISOString()}] ${tag}: ${(err && err.stack) || err}\n`;
  try { fs.appendFileSync(path.join(app.getPath("userData"), "crash.log"), line); } catch (_) { /* */ }
  try { console.error("[hashmm]", tag, err && (err.stack || err.message) || err); } catch (_) { /* */ }
  try { __log().error(err instanceof Error ? err : String(err), { tag }); } catch (_) { /* */ }
}
function __showCrashDialog(err) {
  // GUI 未就绪时先记下，whenReady 后再弹；已就绪则立即弹中文框。
  if (!app.isReady()) {
    __earlyCrash = __earlyCrash || err;
    // 关键：不能只依赖 main.js 后续代码里的 whenReady 来补弹——顶层 require 崩溃会中断
    // 后续求值，那个 whenReady 可能根本没注册上，导致早期崩溃静默吞掉（用户看到"双击没反应"）。
    // 这里就地挂一次性 whenReady（app 已在文件顶部 require，随时可用），保证早期崩溃必然可见。
    try {
      app.whenReady().then(() => {
        if (__earlyCrash) { const e = __earlyCrash; __earlyCrash = null; __showCrashDialog(e); }
      });
    } catch (_) { /* app 都不可用就只剩日志了 */ }
    return;
  }
  let logp = "";
  try { logp = path.join(app.getPath("userData"), "crash.log"); } catch (_) { /* */ }
  try {
    const r = dialog.showMessageBoxSync({
      type: "error",
      title: "HashMM 遇到问题",
      message: "HashMM 启动时遇到一个内部错误",
      detail: `${(err && err.message) || err}\n\n这通常不影响您的数据。您可以查看日志或重启应用。`
        + (logp ? `\n\n日志位置：\n${logp}` : ""),
      buttons: logp ? ["重启 HashMM", "打开日志文件夹", "关闭"] : ["重启 HashMM", "关闭"],
      defaultId: 0, cancelId: logp ? 2 : 1, noLink: true,
    });
    if (r === 1 && logp) { try { shell.showItemInFolder(logp); } catch (_) { /* */ } }
    if (r === 0) { try { app.relaunch(); } catch (_) { /* */ } app.exit(0); }
  } catch (_) { /* dialog 也失败就只剩日志了 */ }
}
process.on("uncaughtException", (err) => { __logCrash("uncaught", err); __showCrashDialog(err); });
process.on("unhandledRejection", (reason) => { __logCrash("unhandledRejection", reason); });

// node-pty 是原生模块（需 electron-rebuild 编译过）；未就绪时终端能力降级、app 仍可用。
// 内嵌终端驾驶舱思路适配自 fanbox（com.huashu.fanbox）。
let pty = null;
try { pty = require("node-pty"); }
catch (e) { console.error("[hashmm] node-pty 未就绪（跑 npm run rebuild）：", e.message); }

const isDev = !app.isPackaged;
let mainWindow = null;
let currentBackend = null; // { url, token }
const terminals = new Map(); // id -> pty process
const termBufs = new Map();  // id -> 输出回放缓冲（V98：视图切走再回来/工作台共享同一会话，重挂载时回放而不是双开 PTY）
const TERM_BUF_CAP = 120000; // 字符数上限（~120KB，够回放几屏）

// ── V87: Marvis 架构对应层 ──
// shellserver  = MarvisNode 网关：内置 webui 静态托管 + /api 同源反代（SSE 直通）
// backendmgr   = MarvisNode KnowledgeBase：本机 Python 后端 sidecar（venv + uvicorn）
const { Tray, nativeImage, globalShortcut, Notification } = require("electron");
// V103.90 桌面通知与未读：决策/未读计数纯逻辑在 notify-core，主进程做实际弹窗/角标/闪烁。
const NOTIFY = require("./notify-core.js");
let _unread = 0;
function _winFgState() {
  const w = mainWindow;
  if (!w || w.isDestroyed()) return { focused: false, visible: false, minimized: false };
  return { focused: w.isFocused(), visible: w.isVisible(), minimized: w.isMinimized() };
}
function _applyBadge() {
  try { if (typeof app.setBadgeCount === "function") app.setBadgeCount(_unread); } catch (_) {}
  // Windows 任务栏 overlay 角标（有未读时画一个小红点数字）
  try {
    const w = mainWindow; if (!w || w.isDestroyed()) return;
    if (_unread > 0 && process.platform === "win32") {
      const txt = NOTIFY.badgeText(_unread);
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><circle cx="16" cy="16" r="15" fill="#ff3b30"/><text x="16" y="22" font-size="${txt.length > 2 ? 13 : 17}" fill="#fff" text-anchor="middle" font-family="Arial">${txt}</text></svg>`;
      const img = nativeImage.createFromDataURL("data:image/svg+xml;base64," + Buffer.from(svg).toString("base64"));
      w.setOverlayIcon(img, _unread + " 条未读");
    } else if (process.platform === "win32") {
      w.setOverlayIcon(null, "");
    }
  } catch (_) {}
}
function _clearUnread() { if (_unread !== 0) { _unread = 0; _applyBadge(); } }
ipcMain.handle("notify:assistantDone", (_e, { preview } = {}) => {
  try {
    const cfg = loadConfig();
    const st = Object.assign({ enabled: cfg.notifyEnabled !== false }, _winFgState());
    if (NOTIFY.shouldCountUnread(st)) { _unread = NOTIFY.addUnread(_unread, 1); _applyBadge(); }
    if (NOTIFY.shouldNotify(st)) {
      if (Notification && Notification.isSupported && Notification.isSupported()) {
        const { title, body } = NOTIFY.notificationContent(preview, {});
        const n = new Notification({ title, body, silent: false });
        n.on("click", () => { try { if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.show(); mainWindow.focus(); } } catch (_) {} });
        n.show();
      }
      try { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.flashFrame(true); } catch (_) {}
    }
    return { ok: true, unread: _unread };
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});
ipcMain.handle("notify:clearUnread", () => { _clearUnread(); return { ok: true }; });
ipcMain.handle("notify:setEnabled", (_e, on) => { try { saveConfig({ notifyEnabled: !!on }); } catch (_) {} return { ok: true }; });
ipcMain.handle("notify:getEnabled", () => { try { return { ok: true, enabled: loadConfig().notifyEnabled !== false }; } catch (_) { return { ok: true, enabled: true }; } });
const { createShellServer } = require("./shellserver");
const { BackendManager, DEFAULT_PORT: LOCAL_BACKEND_PORT } = require("./backendmgr");
let shellSrv = null;
let updateFeedReady = false;   // V91: setupAutoUpdate 配置过更新源后才允许手动检查
let tray = null;               // V93: 系统托盘
let isQuitting = false;        // V93: 区分"关闭最小化"与真正退出
const TRAY_ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAALUlEQVR42mNgAILc2G//ycEMlGiGG0J1A2CAWPHhbAAuQD8DRnIs0D8vUJqdAdWR75Oygn4UAAAAAElFTkSuQmCC";

// V93: 托盘 + 全局快捷键（Marvis 式原生体验）
function toggleMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  // V101: 切换动作交给 WindowService 纯逻辑判定（可单测，行为不变）
  const { nextToggleAction } = require("./services/window-service");
  const action = nextToggleAction({ visible: mainWindow.isVisible(), minimized: mainWindow.isMinimized() });
  if (action === "hide") { mainWindow.hide(); }
  else { mainWindow.show(); mainWindow.focus(); }
}

function createTray() {
  if (tray) return;
  try {
    const img = nativeImage.createFromDataURL("data:image/png;base64," + TRAY_ICON_B64);
    tray = new Tray(img);
    tray.setToolTip("HashMM");
    // V101: 菜单模板交给 TrayService 的纯函数构建（逻辑迁服务、可单测，行为不变）
    const { buildTrayMenuTemplate } = require("./services/tray-service");
    const rebuild = () => {
      const cfg = loadConfig();
      const st = backendMgr.status(backendHome());
      const { Menu } = require("electron");
      const template = buildTrayMenuTemplate(
        { running: st.running, port: st.port, trayOnClose: cfg.trayOnClose },
        {
          toggle: toggleMainWindow,
          stopBackend: () => { backendMgr.stop(); setTimeout(rebuild, 400); },
          startBackend: async () => {
            const r = await backendMgr.start({ home: backendHome(), srcDir: backendSrcDir(),
                                               rtDir: runtimeDir(), port: cfg.localPort || LOCAL_BACKEND_PORT });
            if (r.ok && mainWindow && !mainWindow.isDestroyed()) loadRemoteRef(r.url, "");
            rebuild();
          },
          setTrayOnClose: (checked) => saveConfig({ trayOnClose: checked }),
          quit: () => { isQuitting = true; app.quit(); },
        }
      );
      tray.setContextMenu(Menu.buildFromTemplate(template));
    };
    rebuild();
    tray.on("click", toggleMainWindow);
    tray.on("right-click", rebuild);   // 打开菜单前刷新后端状态项
  } catch (e) { console.warn("[tray] 创建失败:", e.message); }
}
let loadRemoteRef = () => {};  // boot 内赋值（loadRemote 是闭包内函数）
const backendMgr = new BackendManager({ log: (s) => console.log(s) });
backendMgr.onLog((line) => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try { mainWindow.webContents.send("backend:log", line); } catch (_) { /* */ }
  }
});

// 资源根：打包后在 resources/，开发模式直接指向仓库
function webuiDir() {
  return app.isPackaged ? path.join(process.resourcesPath, "webui")
                        : path.join(__dirname, "..", "frontend-next", "out");
}
function backendSrcDir() {
  // V101: 路径解析交给 BackendService 纯逻辑（可单测）
  return require("./services/backend-service").backendSrcDir(
    app.isPackaged ? process.resourcesPath : "", path.join(__dirname, ".."));
}
function installDir() { return path.dirname(app.getPath("exe")); }  // 安装目录 = 主 exe 所在目录
function backendHome() {
  // V101: 后端数据（解析文档/知识库）默认落安装位置 <installDir>\local-backend，可在设置里改。
  const cfg = loadConfig();
  return require("./services/backend-service").resolveBackendHome({
    configuredDir: cfg.backendDataDir, installDir: installDir(), userDataDir: app.getPath("userData"),
  });
}
// V102: 可选能力包管理器（按需下载 Python 运行时 / OCR，替代随包内置）。
const { CapabilityPackManager } = require("./services/capability-pack");
let _capPacks = null;
function capPacks() {
  if (!_capPacks) _capPacks = new CapabilityPackManager({
    userData: app.getPath("userData"), log: (s) => console.log(s),
  });
  return _capPacks;
}
// V89/V102: 运行时目录——bundledPython() 据此找 <dir>/python/python.exe。
// 优先级：① 按需下载的能力包 userData/packs/python-runtime  ②（V102 已不再产出的）随包 runtime
//        ③ 开发目录。三者都没有时，backendmgr 自动回退「系统 Python + venv」模式（不崩）。
function runtimeDir() {
  try {
    const p = capPacks().installedPath("python-runtime");
    if (p) return p;
  } catch (_) { /* 能力包未装，回退 */ }
  return app.isPackaged ? path.join(process.resourcesPath, "runtime")
                        : path.join(__dirname, "runtime");
}

// ───────────────────────── 配置（含窗口状态与最近后端） ─────────────────────────

function configPath() {
  return path.join(app.getPath("userData"), "hashmm-config.json");
}
function loadConfig() {
  try { return JSON.parse(fs.readFileSync(configPath(), "utf-8")); }
  catch (_) { return {}; }
}
function saveConfig(patch) {
  try {
    const cfg = Object.assign(loadConfig(), patch);
    fs.writeFileSync(configPath(), JSON.stringify(cfg, null, 2), "utf-8");
    return cfg;
  } catch (e) { console.error("saveConfig failed:", e); return patch; }
}
// V103.90 Computer Use 写文件的默认目录：优先用户配置 cuFileDir，否则系统下载文件夹，再兜底主目录。
function cuDefaultDir() {
  try { const c = loadConfig(); if (c.cuFileDir && String(c.cuFileDir).trim()) return String(c.cuFileDir); } catch (_e) { /* */ }
  try { return app.getPath("downloads"); } catch (_e) { /* */ }
  try { return os.homedir(); } catch (_e) { return process.cwd(); }
}
function rememberBackend(url, token) {
  const cfg = loadConfig();
  const recent = (cfg.recent || []).filter((r) => r.url !== url);
  recent.unshift({ url, token: token || "", ts: Date.now() });
  saveConfig({ url, token: token || "", recent: recent.slice(0, 5) });
}

// ───────────────────────── 健康检查 ─────────────────────────

// URL 清洗：修「https://http://」叠加、补 scheme、去尾斜杠（与连接页前端同逻辑，双保险）
function normalizeUrl(raw) {
  let u = String(raw || "").trim().replace(/\s+/g, "");
  if (!u) return "";
  const m = u.match(/^(https?:\/\/)+/i);
  if (m) {
    const schemes = u.match(/https?:\/\//gi) || [];
    u = schemes[schemes.length - 1] + u.slice(m[0].length);
  } else {
    u = "http://" + u;
  }
  return u.replace(/\/+$/, "");
}

function checkHealth(baseUrl, timeoutMs = 8000) {
  const url = normalizeUrl(baseUrl) + "/api/health";
  const lib = url.startsWith("https:") ? https : http;
  const t0 = Date.now();
  return new Promise((resolve) => {
    const req = lib.get(url, (res) => {
      const ok = res.statusCode && res.statusCode >= 200 && res.statusCode < 500;
      // V103: 读取 /api/health 正文（之前 res.resume() 直接丢弃了）——里面有就绪状态、
      // 知识库向量数、模型、GPU 等真实信息，供连接页展示，让"绿点"变成"看得见后端是什么"。
      let buf = "";
      res.on("data", (c) => { if (buf.length < 65536) buf += String(c); });
      res.on("end", () => {
        let detail = null;
        try { detail = buf ? JSON.parse(buf) : null; } catch (_e) { /* 非 JSON 忽略 */ }
        resolve({ ok, ms: Date.now() - t0, kind: ok ? "ok" : "http", detail });
      });
      res.on("error", () => resolve({ ok, ms: Date.now() - t0, kind: ok ? "ok" : "http", detail: null }));
    });
    // 连接被拒/网络错误 = 后端真没了；超时 = 后端在听但忙（如解析大文件）。区分二者，
    // 让心跳只对"真下线"弹横幅，不被"忙"误判（修离线时误报后端断开）。
    req.on("error", (e) => resolve({ ok: false, ms: Date.now() - t0, kind: "refused", code: (e && e.code) || "" }));
    req.setTimeout(timeoutMs, () => { req.destroy(); resolve({ ok: false, ms: timeoutMs, kind: "timeout" }); });
  });
}

// 深度检索（Self-RAG）：POST 当前已连后端的 /api/deepsearch。照搬 checkHealth 的 http/https 选择。
// 后端该端点允许匿名，故未带 token 也能用；有 token 则附 Bearer。
function postBackendJSON(baseUrl, pathPart, bodyObj, token, timeoutMs = 180000) {
  return new Promise((resolve) => {
    let u;
    try { u = new URL(normalizeUrl(baseUrl) + pathPart); }
    catch (_e) { resolve({ ok: false, error: "后端地址无效" }); return; }
    const lib = u.protocol === "https:" ? https : http;
    const payload = Buffer.from(JSON.stringify(bodyObj || {}), "utf-8");
    const headers = { "Content-Type": "application/json", "Content-Length": payload.length };
    if (token) headers["Authorization"] = "Bearer " + token;
    const req = lib.request(
      { protocol: u.protocol, hostname: u.hostname, port: u.port,
        path: u.pathname + u.search, method: "POST", headers },
      (res) => {
        let buf = "";
        res.on("data", (c) => { if (buf.length < 2097152) buf += String(c); });
        res.on("end", () => {
          try { resolve({ ok: true, status: res.statusCode, data: buf ? JSON.parse(buf) : null }); }
          catch (_e) { resolve({ ok: false, error: "后端返回非 JSON", status: res.statusCode }); }
        });
      });
    req.on("error", (e) => resolve({ ok: false, error: (e && e.message) || "请求失败" }));
    req.setTimeout(timeoutMs, () => { req.destroy(); resolve({ ok: false, error: "深度检索超时" }); });
    req.write(payload); req.end();
  });
}

ipcMain.handle("backend:deepsearch", async (_e, o = {}) => {
  if (!currentBackend || !currentBackend.url) return { ok: false, error: "未连接后端，无法深度检索" };
  const body = {
    query: String(o.query || ""),
    top_k: o.top_k || 5,
    max_hops: o.max_hops || 3,
    max_rounds: o.max_rounds || 2,
  };
  return await postBackendJSON(currentBackend.url, "/api/deepsearch", body, currentBackend.token, 180000);
});

// ───────────────────────── 窗口 ─────────────────────────

function createShellWindow() {
  const cfg = loadConfig();
  // V101: 保存的窗口位置/尺寸经 WindowService 校验（下限兜底 + 屏外拉回可见区，
  // 例如拔了外接显示器后不至于把窗口恢复到看不见的地方）。
  const { clampBoundsToDisplays } = require("./services/window-service");
  let displays = [];
  try { displays = require("electron").screen.getAllDisplays().map((d) => d.workArea); } catch (_) { /* */ }
  const b = clampBoundsToDisplays(cfg.windowBounds || {}, displays);
  mainWindow = new BrowserWindow({
    width: b.width, height: b.height,
    x: b.x, y: b.y,
    minWidth: 900, minHeight: 600,
    title: "HashMM", backgroundColor: "#ffffff",
    // V65（Marvis 同款形态）：无系统标题栏——UI 里的 DesktopTitlebar 承担拖拽与品牌，
    // Windows 的 最小化/最大化/关闭 用系统 overlay 按钮（WCO），原生手感。
    titleBarStyle: "hidden",
    titleBarOverlay: { color: "#fafafa", symbolColor: "#52525B", height: 36 },
    autoHideMenuBar: true,    // 菜单栏隐藏（Alt 呼出，快捷键 Ctrl+Shift+B/T 仍有效）
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false,
      sandbox: false,          // app.html 需要 xterm + webview 同渲染进程
      webviewTag: true,        // v1.3: 「对话」页签用 <webview> 嵌远程 RAG UI
    },
  });

  // 窗口状态记忆（防抖落盘）
  let saveTimer = null;
  const persistBounds = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isMaximized()) {
        saveConfig({ windowBounds: mainWindow.getBounds() });
      }
    }, 600);
  };
  mainWindow.on("resize", persistBounds);
  mainWindow.on("move", persistBounds);
  // V103.90 窗口聚焦 → 清未读角标 + 停止任务栏闪烁
  mainWindow.on("focus", () => { _clearUnread(); try { mainWindow.flashFrame(false); } catch (_) {} });

  // v1.4: 远程 UI 加载失败/进程崩溃 → 回离线本机模式（不白屏）
  mainWindow.webContents.on("did-fail-load", (_e, code, desc, failedUrl, isMainFrame) => {
    if (!isMainFrame || code === -3) return;
    const fromBackend = currentBackend && failedUrl && failedUrl.startsWith(currentBackend.url);
    const fromShell = shellSrv && failedUrl && failedUrl.startsWith(shellSrv.origin);
    if (fromBackend || fromShell) {
      currentBackend = null;
      mainWindow.loadFile(path.join(__dirname, "app.html"),
        { search: "?err=" + encodeURIComponent("后端加载失败（" + (desc || code) + "）") });
    }
  });
  mainWindow.webContents.on("render-process-gone", (_e, details) => {
    if (details.reason !== "clean-exit" && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadFile(path.join(__dirname, "app.html"));
    }
  });
  // 外链治理：远程 UI 里的 window.open 非后端域 → 系统浏览器
  mainWindow.webContents.setWindowOpenHandler(({ url: target }) => {
    const fromBackend = currentBackend && target.startsWith(currentBackend.url);
    const fromShell = shellSrv && target.startsWith(shellSrv.origin);   // V87: 内置壳层同源弹窗
    if (fromBackend || fromShell) {
      return { action: "allow", overrideBrowserWindowOptions: {
        autoHideMenuBar: true, width: 880, height: 700,
        backgroundColor: "#ffffff",
        titleBarStyle: "hidden",                                  // 与主界面同形态
        titleBarOverlay: { color: "#fafafa", symbolColor: "#52525B", height: 36 },
      } };
    }
    if (/^https?:\/\//i.test(target)) shell.openExternal(target);
    return { action: "deny" };
  });

  mainWindow.on("close", (e) => {
    // V103.90 点 X：按 trayOnClose(记住的选择)决定，未记住则让"渲染层"弹出与主界面同风格的弹窗
    //（不再用系统原生蓝框）。决策纯函数仍在 WindowService，可单测。
    const { decideCloseAction } = require("./services/window-service");
    const act = decideCloseAction(isQuitting, loadConfig().trayOnClose);
    if (act === "quit") return;                                  // 放行关闭
    if (act === "tray") { e.preventDefault(); mainWindow.hide(); return; }
    // act === "ask" → 拦截关闭，通知前端弹出关闭确认弹窗；用户选择经 app:closeChoice 回传后再执行。
    e.preventDefault();
    try { mainWindow.webContents.send("app:confirmClose"); } catch (_e) { /* 发不出去就保持打开，避免误退 */ }
  });
  mainWindow.on("closed", () => { mainWindow = null; });
  return mainWindow;
}

// V103.90 关闭确认弹窗的用户选择回传（前端 app 风格弹窗 → 这里执行）。
ipcMain.on("app:closeChoice", (_e, payload) => {
  const choice = payload && payload.choice;       // "tray" | "quit" | "cancel"
  const remember = !!(payload && payload.remember);
  if (choice === "cancel" || !choice) return;     // 取消 → 保持打开
  if (remember && (choice === "tray" || choice === "quit")) {
    saveConfig({ trayOnClose: choice === "tray" });
  }
  if (choice === "tray") {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.hide();
  } else if (choice === "quit") {
    isQuitting = true;
    app.quit();
  }
});

// ───────────────────────── 应用菜单（中文） ─────────────────────────

function buildMenu() {
  const template = [
    {
      label: "HashMM",
      submenu: [
        { label: "关于 HashMM", click: () => dialog.showMessageBox({
            type: "info", title: "关于", message: "HashMM Desktop",
            detail: `版本 ${app.getVersion()}\n本地优先 RAG-Agent 的桌面客户端（瘦客户端）。\n当前后端：${currentBackend ? currentBackend.url : "未连接"}`,
          }) },
        { type: "separator" },
        { label: "连接 / 切换后端", accelerator: "CmdOrCtrl+Shift+B",
          click: () => { if (mainWindow) mainWindow.webContents.send("nav", "settings"); } },
        { label: "终端", accelerator: "CmdOrCtrl+Shift+T",
          click: () => { if (mainWindow) mainWindow.webContents.send("nav", "terminal"); } },
        { type: "separator" },
        { role: "quit", label: "退出" },
      ],
    },
    {
      label: "编辑",
      submenu: [
        { role: "undo", label: "撤销" }, { role: "redo", label: "重做" },
        { type: "separator" },
        { role: "cut", label: "剪切" }, { role: "copy", label: "复制" },
        { role: "paste", label: "粘贴" }, { role: "selectAll", label: "全选" },
      ],
    },
    {
      label: "视图",
      submenu: [
        { role: "reload", label: "刷新" },
        { role: "forceReload", label: "强制刷新" },
        { type: "separator" },
        { role: "zoomIn", label: "放大" }, { role: "zoomOut", label: "缩小" },
        { role: "resetZoom", label: "实际大小" },
        { type: "separator" },
        { role: "togglefullscreen", label: "全屏" },
        { role: "toggleDevTools", label: "开发者工具" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ───────────────────────── IPC ─────────────────────────

// ───────────────────────── 内嵌终端（node-pty，适配自 fanbox） ─────────────────────────
// 让桌面端不止"连后端看 RAG-Agent"，还能内嵌终端直接指挥 claude / codex 等 coding agent。

function defaultShell() {
  // V101: shell 选择交给 TerminalService 纯逻辑（可单测）
  return require("./services/terminal-service").defaultShell(process.platform, process.env);
}

ipcMain.handle("term:spawn", (_e, { id, cwd, cols, rows }) => {
  if (!pty) return { ok: false, error: "node-pty 未编译，请在 desktop/ 跑：npm run rebuild" };
  const TS = require("./services/terminal-service");
  // V98：同 id 已有活会话 → 复用 + 回放，而不是再 spawn 一个覆盖 Map
  const existing = terminals.get(id);
  if (existing) {
    try { if (cols && rows) existing.resize(cols, rows); } catch (_) { /* */ }
    return {
      ok: true, reused: true,
      cwd: existing._hmCwd || "", shell: existing._hmShell || "",
      replay: termBufs.get(id) || "",
    };
  }
  const shellPath = defaultShell();
  // V101: 起始目录解析 + 环境准备 交给 TerminalService 纯逻辑（可单测）
  const startCwd = TS.resolveStartCwd(cwd, (p) => fs.existsSync(p), os.homedir());
  const env = TS.buildShellEnv(process.env, process.platform);
  let p;
  try {
    p = pty.spawn(shellPath, [], {
      name: "xterm-256color",
      cols: cols || 80, rows: rows || 24, cwd: startCwd, env,
      useConpty: process.platform === "win32",  // Windows: 用 ConPTY
    });
  } catch (err) { return { ok: false, error: err.message }; }
  terminals.set(id, p);
  p._hmShell = shellPath; p._hmCwd = startCwd;       // V98 复用时回报
  termBufs.set(id, "");
  p.onData((data) => {
    // V101: 回放缓冲环形截尾交给 TerminalService.appendBuffer（可单测）
    termBufs.set(id, TS.appendBuffer(termBufs.get(id), data, TERM_BUF_CAP));
    if (mainWindow && !mainWindow.isDestroyed())
      mainWindow.webContents.send("term:data", { id, data });
  });
  p.onExit(({ exitCode }) => {
    terminals.delete(id);
    termBufs.delete(id);
    if (mainWindow && !mainWindow.isDestroyed())
      mainWindow.webContents.send("term:exit", { id, exitCode });
  });
  return { ok: true, cwd: startCwd, shell: shellPath };
});

ipcMain.on("term:input", (_e, { id, data }) => {
  const p = terminals.get(id); if (p) p.write(data);
});
ipcMain.on("term:resize", (_e, { id, cols, rows }) => {
  const p = terminals.get(id); if (p) { try { p.resize(cols, rows); } catch (_) { /* */ } }
});
ipcMain.on("term:kill", (_e, { id }) => {
  const p = terminals.get(id);
  if (p) { try { p.kill(); } catch (_) { /* */ } terminals.delete(id); }
  termBufs.delete(id);
});
// 前台进程名：判断当前是裸 shell 还是正跑着 claude / codex
ipcMain.handle("term:proc", (_e, { id }) => {
  const p = terminals.get(id);
  return p ? { ok: true, proc: p.process || "" } : { ok: false };
});
// 一键在指定终端启动某 agent（claude / codex / 自定义命令）
ipcMain.handle("term:runAgent", (_e, { id, agent }) => {
  const p = terminals.get(id);
  if (!p) return { ok: false, error: "终端不存在" };
  const cmd = { claude: "claude", codex: "codex" }[agent] || String(agent || "");
  if (!cmd) return { ok: false, error: "未知 agent" };
  p.write(cmd + "\r");
  return { ok: true };
});

// ───────────────────────── 文件监听（agent 改文件 → 通知前端，适配自 fanbox） ─────────────────────────
// "在桌面里指挥 agent 改本机代码"的核心体验：agent 写文件后，前端能感知并刷新。
const watchers = new Map(); // dir -> FSWatcher
function startWatch(dir) {
  if (watchers.has(dir) || !dir || !fs.existsSync(dir)) return;
  try {
    // macOS(FSEvents)/Windows 原生支持递归；Linux 递归不可靠 → 降级非递归
    const recursive = process.platform !== "linux";
    const w = fs.watch(dir, { persistent: false, recursive }, (evt, filename) => {
      const target = (mainWindow && !mainWindow.isDestroyed() ? mainWindow : null);
      if (target) target.webContents.send("fs:changed",
        { dir, filename: filename ? filename.toString() : null, evt });
    });
    watchers.set(dir, w);
  } catch (_) { /* 无权限等，跳过 */ }
}
ipcMain.handle("fs:watchSet", (_e, { dirs }) => {
  const want = new Set((dirs || []).filter(Boolean));
  for (const [dir, w] of watchers) {
    if (!want.has(dir)) { try { w.close(); } catch (_) { /* */ } watchers.delete(dir); }
  }
  for (const dir of want) startWatch(dir);
  return { ok: true, count: watchers.size };
});

// 安全打开外链：agent 输出/界面里的链接走系统浏览器（仅 http/https，防协议注入）
ipcMain.handle("shell:openExternal", (_e, { url }) => {
  try {
    if (/^https?:\/\//i.test(String(url || ""))) { shell.openExternal(url); return { ok: true }; }
    return { ok: false, error: "仅允许 http/https 链接" };
  } catch (e) { return { ok: false, error: e.message }; }
});

// ───────────────────────── 本机能力（fanbox 本体功能适配） ─────────────────────────
// 这是软件的"本机模式"：不连任何后端也能用——文件工作台 + agent 用量 + 终端。
// 适配自 fanbox server.js 的 /api/roots /api/list /api/read /api/agent-usage。

ipcMain.handle("local:roots", () => {
  const home = os.homedir();
  const roots = [{ name: "主目录", path: home }];
  for (const sub of ["Desktop", "Documents", "Downloads", "桌面", "文档", "下载"]) {
    const p = path.join(home, sub);
    if (fs.existsSync(p)) roots.push({ name: sub, path: p });
  }
  return { ok: true, roots };
});

ipcMain.handle("local:list", (_e, { dir }) => {
  try {
    const target = path.resolve(String(dir || os.homedir()));
    const names = fs.readdirSync(target, { withFileTypes: true });
    const items = [];
    for (const n of names) {
      if (n.name.startsWith(".")) continue;            // 隐藏文件不展示（fanbox 同款行为）
      const fp = path.join(target, n.name);
      let st = null;
      try { st = fs.statSync(fp); } catch (_) { continue; }
      items.push({
        name: n.name, path: fp, dir: n.isDirectory(),
        size: st.size, mtime: st.mtimeMs,
      });
    }
    items.sort((a, b) => (b.dir - a.dir) || a.name.localeCompare(b.name, "zh"));
    return { ok: true, dir: target, items: items.slice(0, 500) };
  } catch (e) { return { ok: false, error: e.message }; }
});

const TEXT_EXT = new Set([".txt",".md",".py",".js",".ts",".tsx",".jsx",".json",".yml",".yaml",  ".html",".css",".c",".cc",".cpp",".h",".hpp",".java",".go",".rs",".sh",".bat",".ps1",
  ".toml",".ini",".cfg",".log",".csv",".sql",".xml",".vue",".env",".gitignore"]);
const IMG_EXT = new Set([".png",".jpg",".jpeg",".gif",".webp",".svg",".bmp",".ico"]);

ipcMain.handle("local:read", (_e, { file }) => {
  try {
    const fp = path.resolve(String(file || ""));
    const st = fs.statSync(fp);
    if (st.isDirectory()) return { ok: false, error: "是目录" };
    const ext = path.extname(fp).toLowerCase();
    if (IMG_EXT.has(ext)) {
      if (st.size > 8 * 1024 * 1024) return { ok: false, error: "图片超过 8MB" };
      const b64 = fs.readFileSync(fp).toString("base64");
      const mime = ext === ".svg" ? "image/svg+xml" : "image/" + ext.slice(1).replace("jpg", "jpeg");
      return { ok: true, kind: "image", dataUrl: `data:${mime};base64,${b64}` };
    }
    if (!TEXT_EXT.has(ext) && st.size > 256 * 1024) return { ok: false, error: "二进制或过大文件" };
    if (st.size > 1024 * 1024) return { ok: false, error: "文本超过 1MB，请用编辑器打开" };
    const buf = fs.readFileSync(fp);
    if (buf.includes(0)) return { ok: false, error: "二进制文件" };   // NUL 字节=二进制
    return { ok: true, kind: "text", text: buf.toString("utf-8"), ext };
  } catch (e) { return { ok: false, error: e.message }; }
});

// ───────── 桌面文件投送（桌面→App，走云端 Supabase，不是局域网）─────────
// 用户在 App/网页聊天里说"把电脑/桌面的某文件发我"→后端写 file_requests 表→
// 这里常驻轮询(每4s)消费：在 桌面/下载/文档 里按文件名匹配→上传到该对话→回写助手消息(带下载链接)→标记完成。
// 鉴权：用最近的 Supabase access_token（_lastAcctToken）查 Supabase + 传后端；uid 从 JWT 解。
const _SBFR_URL = "";
const _SBFR_KEY = "";
let _fileReqTimer = null;
let _fileReqBusy = false;

function _jwtSub(token) {
  try {
    const p = String(token || "").split(".")[1];
    if (!p) return "";
    const json = Buffer.from(p.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf-8");
    return JSON.parse(json).sub || "";
  } catch (_e) { return ""; }
}

function _frRoots() {
  const home = os.homedir();
  const roots = [];
  for (const sub of ["Desktop", "桌面", "Downloads", "下载", "Documents", "文档"]) {
    const p = path.join(home, sub);
    try { if (fs.existsSync(p)) roots.push(p); } catch (_e) {}
  }
  return roots;
}

// 按"本地实际文件名"匹配用户原话：文件名(或去扩展名)作为子串出现在 query 里即命中，取最长匹配。
function _matchLocalFile(query) {
  const q = String(query || "").toLowerCase();
  let best = null, bestLen = 0;
  for (const root of _frRoots()) {
    let names = [];
    try { names = fs.readdirSync(root, { withFileTypes: true }); } catch (_e) { continue; }
    for (const n of names) {
      if (n.isDirectory() || n.name.startsWith(".")) continue;
      const base = n.name.toLowerCase();
      const stem = base.replace(/\.[^.]+$/, "");
      if (stem.length < 2) continue;
      if (q.includes(base) || q.includes(stem)) {
        if (stem.length > bestLen) { best = path.join(root, n.name); bestLen = stem.length; }
      }
    }
  }
  return best;
}

// ───── 模糊匹配：精确匹配不到时，找"名字相似"的文件让用户选/确认 ─────
// 用户原话里有很多口语词，先剥掉，提取"文件名核心"：
//   「给我把桌面上的1111文件发给我」→「1111」；「发我那个报告」→「报告」。
const _FR_FILLER = [
  "桌面上面的", "桌面上的", "桌面里面的", "桌面里的", "电脑上面的", "电脑上的", "电脑里面的", "电脑里的",
  "客户端上面的", "客户端上的", "客户端里的", "名字叫做", "名字叫", "名称叫", "叫做",
  "帮我找", "帮我", "给我发", "发给我", "传给我", "发过来", "传过来", "拿给我", "取给我", "丢给我", "甩给我",
  "桌面", "电脑", "客户端", "本机", "上面的", "下面的", "里面的", "那个", "这个", "一下", "看看",
  "文件名", "文件夹", "文件", "文档", "请", "把", "发送", "发我", "发到", "发", "传", "拿", "取", "给",
  "我", "的", "哦", "呀", "啊", "呢", "吧", "名字", "名称",
];
function _extractNameHint(query) {
  let s = String(query || "").trim();
  s = s.replace(/[\s，。、！？!?,.；;：:""''`（）()【】\[\]「」<>《》]/g, "");
  for (const w of _FR_FILLER) { if (w) s = s.split(w).join(""); }
  return s;
}
function _lev(a, b) {
  a = a || ""; b = b || "";
  const m = a.length, n = b.length;
  if (!m) return n; if (!n) return m;
  const dp = [];
  for (let j = 0; j <= n; j++) dp[j] = j;
  for (let i = 1; i <= m; i++) {
    let prev = dp[0]; dp[0] = i;
    for (let j = 1; j <= n; j++) {
      const tmp = dp[j];
      dp[j] = Math.min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = tmp;
    }
  }
  return dp[n];
}
// 精确匹配失败时，找相似文件名候选（文件名含核心 / 被核心包含 / 编辑距离接近），按相似度排序去重。
function _fuzzyCandidates(query, maxN) {
  maxN = maxN || 6;
  const hint = _extractNameHint(query).toLowerCase();
  if (!hint) return [];
  const out = [];
  for (const root of _frRoots()) {
    let names = [];
    try { names = fs.readdirSync(root, { withFileTypes: true }); } catch (_e) { continue; }
    for (const n of names) {
      if (n.isDirectory() || n.name.startsWith(".")) continue;
      const base = n.name.toLowerCase();
      const stem = base.replace(/\.[^.]+$/, "");
      if (stem.length < 1) continue;
      let score = 0;
      if (stem.includes(hint)) score = 100 + hint.length;            // 文件名含用户说的核心（最像）
      else if (hint.includes(stem) && stem.length >= 2) score = 90;   // 用户说的含文件名
      else {
        const d = _lev(hint, stem);
        const maxLen = Math.max(hint.length, stem.length);
        if (maxLen >= 2 && d <= Math.max(1, Math.floor(maxLen * 0.4))) score = 80 - d;  // 容错打字
      }
      if (score > 0) out.push({ name: n.name, root, score });
    }
  }
  out.sort((a, b) => b.score - a.score);
  const seen = new Set(); const res = [];
  for (const c of out) { if (seen.has(c.name)) continue; seen.add(c.name); res.push(c); if (res.length >= maxN) break; }
  return res;
}

async function _frUploadToConv(filePath, convId, backendUrl, token) {
  const data = fs.readFileSync(filePath);
  const fd = new FormData();
  fd.append("file", new Blob([data]), path.basename(filePath));
  const url = backendUrl.replace(/\/+$/, "") + "/api/conversations/" + encodeURIComponent(convId) + "/upload";
  const res = await fetch(url, { method: "POST", headers: token ? { Authorization: "Bearer " + token } : {}, body: fd });
  if (!res.ok) throw new Error("upload HTTP " + res.status);
  return await res.json().catch(() => ({}));
}

async function _frPostMsg(convId, content, files, backendUrl, token) {
  try {
    const url = backendUrl.replace(/\/+$/, "") + "/api/conversations/" + encodeURIComponent(convId) + "/assistant-message";
    const body = { content };
    if (Array.isArray(files) && files.length) body.files = files;
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
      body: JSON.stringify(body),
    });
  } catch (_e) {}
}

async function _frPatch(id, fields, token) {
  try {
    const url = _SBFR_URL + "/rest/v1/file_requests?id=eq." + encodeURIComponent(id);
    await fetch(url, {
      method: "PATCH",
      headers: { apikey: _SBFR_KEY, Authorization: "Bearer " + token, "Content-Type": "application/json", Prefer: "return=minimal" },
      body: JSON.stringify(fields),
    });
  } catch (_e) {}
}

async function _pollFileRequests() {
  if (_fileReqBusy) return;
  const token = _lastAcctToken || (currentBackend && currentBackend.token) || "";
  const backendUrl = (currentBackend && currentBackend.url) || "";
  if (!token || !backendUrl) return;
  const uid = _jwtSub(token);
  if (!uid) return;
  _fileReqBusy = true;
  try {
    const q = _SBFR_URL + "/rest/v1/file_requests?user_id=eq." + encodeURIComponent(uid) +
      "&status=eq.pending&or=(target.is.null,target.eq.desktop)&order=created_at.asc&limit=5";
    const res = await fetch(q, { headers: { apikey: _SBFR_KEY, Authorization: "Bearer " + token } });
    if (!res.ok) return;
    const rows = await res.json().catch(() => []);
    for (const r of (rows || [])) {
      if (!r || !r.id || !r.conv_id) continue;
      await _frPatch(r.id, { status: "processing" }, token);   // 占位，避免下一轮重复处理
      let fp = _matchLocalFile(r.query);
      if (!fp) {
        const cands = _fuzzyCandidates(r.query);
        if (cands.length === 1) {
          fp = path.join(cands[0].root, cands[0].name);   // 只有一个相似 → 多半就是它（名字没说全），直接发
        } else if (cands.length > 1) {
          const list = cands.map((c, i) => `${i + 1}. ${c.name}`).join("\n");
          await _frPostMsg(r.conv_id,
            `没精确匹配到你说的文件。桌面/下载/文档里相似的有：\n${list}\n` +
            `回复「发我 确切文件名」就给你（例如：发我 ${cands[0].name}）。`,
            null, backendUrl, token);
          await _frPatch(r.id, { status: "not_found" }, token);
          continue;
        } else {
          await _frPostMsg(r.conv_id, "没在你电脑的 桌面/下载/文档 里找到匹配或相似的文件。把文件名说全一点，或确认它在这几个目录里，再发一次。", null, backendUrl, token);
          await _frPatch(r.id, { status: "not_found" }, token);
          continue;
        }
      }
      try {
        let fsize = 0;
        try { fsize = fs.statSync(fp).size; } catch (_e) {}
        const up = await _frUploadToConv(fp, r.conv_id, backendUrl, token);
        const fn = (up && up.filename) || path.basename(fp);
        const rel = (up && up.download_url) || ("/api/conversations/" + r.conv_id + "/download/" + encodeURIComponent(fn));
        // 完整可点链接：后端公网地址 + 相对路径 + ?token（下载端点支持 query token 鉴权）。
        const full = backendUrl.replace(/\/+$/, "") + rel + (rel.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
        // 走 files 元数据 → App 端渲染成漂亮的文件卡片（图标+文件名+大小+下载），不再是纯文字链接。
        const files = [{ filename: fn, download_url: full, size: fsize }];
        await _frPostMsg(r.conv_id, `已从你的电脑发送文件：${fn}`, files, backendUrl, token);
        await _frPatch(r.id, { status: "done" }, token);
      } catch (e) {
        await _frPostMsg(r.conv_id, `找到了文件但上传失败（${e && e.message}），稍后再试。`, null, backendUrl, token);
        await _frPatch(r.id, { status: "error" }, token);
      }
    }
  } catch (_e) { /* 轮询失败静默，下一轮再试 */ } finally { _fileReqBusy = false; }
}

function _startFileReqPoller() {
  if (_fileReqTimer) return;
  _fileReqTimer = setInterval(() => { _pollFileRequests().catch(() => {}); }, 4000);
}

// V103.12: Git 图数据源——只读跑 `git -C <dir> log --all`，用 \x1f/\x1e 分隔符避免
// subject 含 | 的解析问题。返回 {hash, parents[], refs[], author, date, subject}。
ipcMain.handle("git:log", async (_e, { cwd, limit } = {}) => {
  try {
    const { execFile } = require("child_process");
    const dir = path.resolve(String(cwd || process.cwd()));
    const fmt = "%H%x1f%P%x1f%D%x1f%an%x1f%ad%x1f%s%x1e";
    const args = ["-C", dir, "log", "--all", "--date=short", "--pretty=format:" + fmt, "-n", String(Math.min(Math.max(parseInt(limit, 10) || 300, 1), 1000))];
    const out = await new Promise((resolve, reject) => {
      execFile("git", args, { maxBuffer: 16 * 1024 * 1024, timeout: 15000 }, (err, stdout, stderr) => {
        if (err) reject(new Error(((stderr || "") + "" || err.message || "git 执行失败").trim())); else resolve(stdout);
      });
    });
    const commits = [];
    for (const rec of String(out).split("\x1e")) {
      const line = rec.replace(/^\n+/, "");
      if (!line.trim()) continue;
      const f = line.split("\x1f");
      if (!f[0]) continue;
      commits.push({
        hash: f[0],
        parents: f[1] ? f[1].split(" ").filter(Boolean) : [],
        refs: f[2] ? f[2].split(",").map((s) => s.trim()).filter(Boolean) : [],
        author: f[3] || "",
        date: f[4] || "",
        subject: f[5] || "",
      });
    }
    return { ok: true, commits, cwd: dir };
  } catch (e) {
    return { ok: false, error: e.message };
  }
});
// V103.7: 原地写回（给 Monaco/编辑器存盘）。安全：只允许写**已存在**的文件（编辑场景，
// 不创建任意新文件）、非目录、内容 ≤1MB。路径解析与 local:read 一致。
ipcMain.handle("local:write", (_e, { file, text }) => {
  try {
    const fp = path.resolve(String(file || ""));
    const st = fs.statSync(fp);                 // 不存在会抛 → 拦在编辑已有文件
    if (st.isDirectory()) return { ok: false, error: "是目录" };
    const s = String(text == null ? "" : text);
    if (Buffer.byteLength(s, "utf-8") > 1024 * 1024) return { ok: false, error: "内容超过 1MB" };
    fs.writeFileSync(fp, s, "utf-8");
    return { ok: true, bytes: Buffer.byteLength(s, "utf-8") };
  } catch (e) { return { ok: false, error: e.message }; }
});

// ── agent 用量（fanbox server.js claudeUsage/codexUsage 的适配版） ──
const CLAUDE_PROJ = path.join(os.homedir(), ".claude", "projects");
const CODEX_SESS = path.join(os.homedir(), ".codex", "sessions");
const _cuCache = new Map(); // file -> {offset,lastMsgId,events}

function _parseClaudeFile(fp, size, mtimeMs) {
  let c = _cuCache.get(fp);
  if (!c) { c = { offset: 0, lastMsgId: "", events: [] }; _cuCache.set(fp, c); }
  if (size < c.offset) { c.offset = 0; c.lastMsgId = ""; c.events = []; }
  if (size === c.offset) return c.events;
  let chunk;
  try {
    const fd = fs.openSync(fp, "r");
    const buf = Buffer.alloc(size - c.offset);
    fs.readSync(fd, buf, 0, buf.length, c.offset);
    fs.closeSync(fd);
    chunk = buf.toString("utf8");
  } catch (_) { return c.events; }
  const lastNL = chunk.lastIndexOf("\n");
  if (lastNL === -1) return c.events;
  c.offset += Buffer.byteLength(chunk.slice(0, lastNL + 1), "utf8");
  for (const line of chunk.slice(0, lastNL).split("\n")) {
    if (!line.includes('"usage"') || !line.includes('"assistant"')) continue;
    let d; try { d = JSON.parse(line); } catch (_) { continue; }
    const m = d && d.message, u = m && m.usage;
    if (!u || d.type !== "assistant" || (m && m.model === "<synthetic>")) continue;
    if (m.id && m.id === c.lastMsgId) continue;
    if (m.id) c.lastMsgId = m.id;
    const t = Date.parse(d.timestamp || "") || mtimeMs;
    c.events.push({ t, in: u.input_tokens || 0, out: u.output_tokens || 0,
      cc: u.cache_creation_input_tokens || 0, cr: u.cache_read_input_tokens || 0 });
  }
  return c.events;
}

ipcMain.handle("local:search", (_e, { dir, q }) => {
  // 文件名搜索（适配自 fanbox /api/search）：从 dir 向下走，深度≤4，命中≤80 条。
  try {
    const base = path.resolve(String(dir || os.homedir()));
    const needle = String(q || "").toLowerCase();
    if (needle.length < 2) return { ok: true, items: [] };
    const hits = [];
    const SKIP = new Set(["node_modules", ".git", "__pycache__", ".next", "dist", "venv", ".venv"]);
    const walk = (d, depth) => {
      if (hits.length >= 80 || depth > 4) return;
      let names = [];
      try { names = fs.readdirSync(d, { withFileTypes: true }); } catch (_) { return; }
      for (const n of names) {
        if (hits.length >= 80) return;
        if (n.name.startsWith(".") || SKIP.has(n.name)) continue;
        const fp = path.join(d, n.name);
        if (n.name.toLowerCase().includes(needle)) {
          let st = null; try { st = fs.statSync(fp); } catch (_) { continue; }
          hits.push({ name: n.name, path: fp, dir: n.isDirectory(), size: st.size });
        }
        if (n.isDirectory()) walk(fp, depth + 1);
      }
    };
    walk(base, 0);
    return { ok: true, items: hits };
  } catch (e) { return { ok: false, error: e.message }; }
});

ipcMain.handle("local:grep", (_e, { dir, q }) => {
  // 内容搜索（适配自 fanbox /api/grep）：在文本文件里搜字符串，命中≤60 文件、每文件≤3 行。
  try {
    const base = path.resolve(String(dir || os.homedir()));
    const needle = String(q || "");
    if (needle.length < 2) return { ok: true, items: [] };
    const lower = needle.toLowerCase();
    const SKIP = new Set(["node_modules", ".git", "__pycache__", ".next", "dist", "venv", ".venv"]);
    const hits = [];
    const walk = (d, depth) => {
      if (hits.length >= 60 || depth > 4) return;
      let names = [];
      try { names = fs.readdirSync(d, { withFileTypes: true }); } catch (_) { return; }
      for (const n of names) {
        if (hits.length >= 60) return;
        if (n.name.startsWith(".") || SKIP.has(n.name)) continue;
        const fp = path.join(d, n.name);
        if (n.isDirectory()) { walk(fp, depth + 1); continue; }
        const ext = path.extname(n.name).toLowerCase();
        if (!TEXT_EXT.has(ext)) continue;
        let st = null;
        try { st = fs.statSync(fp); } catch (_) { continue; }
        if (st.size > 1024 * 1024) continue;
        let txt = "";
        try { txt = fs.readFileSync(fp, "utf8"); } catch (_) { continue; }
        if (txt.includes("\u0000")) continue;
        const lines = txt.split("\n");
        const matches = [];
        for (let i = 0; i < lines.length && matches.length < 3; i++) {
          if (lines[i].toLowerCase().includes(lower)) {
            matches.push({ line: i + 1, text: lines[i].trim().slice(0, 160) });
          }
        }
        if (matches.length) hits.push({ name: n.name, path: fp, dir: false, size: st.size, matches });
      }
    };
    walk(base, 0);
    return { ok: true, items: hits };
  } catch (e) { return { ok: false, error: e.message }; }
});

ipcMain.handle("local:recent", (_e, { dir }) => {
  // 最近修改（适配自 fanbox /api/recent）：72h 内改过的文件，按时间倒序 ≤50。
  try {
    const base = path.resolve(String(dir || os.homedir()));
    const cutoff = Date.now() - 72 * 3600000;
    const SKIP = new Set(["node_modules", ".git", "__pycache__", ".next", "dist", "venv", ".venv"]);
    const hits = [];
    const walk = (d, depth) => {
      if (hits.length >= 400 || depth > 4) return;
      let names = [];
      try { names = fs.readdirSync(d, { withFileTypes: true }); } catch (_) { return; }
      for (const n of names) {
        if (n.name.startsWith(".") || SKIP.has(n.name)) continue;
        const fp = path.join(d, n.name);
        if (n.isDirectory()) { walk(fp, depth + 1); continue; }
        let st = null;
        try { st = fs.statSync(fp); } catch (_) { continue; }
        if (st.mtimeMs >= cutoff) hits.push({ name: n.name, path: fp, dir: false, size: st.size, mtime: st.mtimeMs });
      }
    };
    walk(base, 0);
    hits.sort((a, b) => b.mtime - a.mtime);
    return { ok: true, items: hits.slice(0, 50) };
  } catch (e) { return { ok: false, error: e.message }; }
});

ipcMain.handle("local:agentUsage", () => {
  const out = { claude: null, codex: null };
  // Claude Code
  try {
    if (fs.existsSync(CLAUDE_PROJ)) {
      const cutoff = Date.now() - 8 * 86400000;
      const all = [];
      for (const d of fs.readdirSync(CLAUDE_PROJ)) {
        const dd = path.join(CLAUDE_PROJ, d);
        let names = [];
        try { names = fs.readdirSync(dd); } catch (_) { continue; }
        for (const n of names) {
          if (!n.endsWith(".jsonl")) continue;
          const fp = path.join(dd, n);
          try {
            const st = fs.statSync(fp);
            if (st.mtimeMs >= cutoff) all.push(..._parseClaudeFile(fp, st.size, st.mtimeMs));
          } catch (_) { /* 单文件坏不挡整体 */ }
        }
      }
      const now = Date.now();
      const dayStart = new Date(); dayStart.setHours(0, 0, 0, 0);
      const mk = () => ({ total: 0, input: 0, output: 0, msgs: 0 });
      const last5h = mk(), today = mk(), week = mk();
      for (const e of all) {
        const tot = e.in + e.out + e.cc + e.cr;
        for (const [b, frm] of [[last5h, now - 5 * 3600000], [today, dayStart.getTime()], [week, now - 7 * 86400000]]) {
          if (e.t >= frm) { b.total += tot; b.input += e.in; b.output += e.out; b.msgs++; }
        }
      }
      out.claude = { last5h, today, week };
    }
  } catch (_) { /* 保持 null */ }
  // Codex（尾部抓最后一条 token_count 快照）
  try {
    if (fs.existsSync(CODEX_SESS)) {
      const files = [];
      const walk = (dir, depth) => {
        let names = [];
        try { names = fs.readdirSync(dir, { withFileTypes: true }); } catch (_) { return; }
        for (const n of names) {
          const fp = path.join(dir, n.name);
          if (n.isDirectory() && depth < 3) walk(fp, depth + 1);
          else if (n.isFile() && n.name.endsWith(".jsonl")) {
            try { files.push({ fp, mtimeMs: fs.statSync(fp).mtimeMs }); } catch (_) { /* */ }
          }
        }
      };
      walk(CODEX_SESS, 0);
      files.sort((a, b) => b.mtimeMs - a.mtimeMs);
      outer:
      for (const f of files.slice(0, 10)) {
        let txt = "";
        try {
          const st = fs.statSync(f.fp);
          const fd = fs.openSync(f.fp, "r");
          const n = Math.min(65536, st.size);
          const buf = Buffer.alloc(n);
          fs.readSync(fd, buf, 0, n, Math.max(0, st.size - n));
          fs.closeSync(fd);
          txt = buf.toString("utf8");
        } catch (_) { continue; }
        for (const line of txt.split("\n").reverse()) {
          if (!line.includes('"token_count"') && !line.includes('"rate_limits"')) continue;
          try {
            const d = JSON.parse(line);
            const payload = d.payload && typeof d.payload === "object" ? d.payload : d;
            const info = payload.info && typeof payload.info === "object" ? payload.info : payload;
            const tc = info.token_count || info.total_token_usage || {};
            const rl = info.rate_limits || {};
            if (Object.keys(tc).length || Object.keys(rl).length) {
              out.codex = { tokens: tc, rate_limits: rl };
              break outer;
            }
          } catch (_) { /* */ }
        }
      }
    }
  } catch (_) { /* 保持 null */ }
  return out;
});

ipcMain.handle("hashmm:setOverlay", (_e, { dark }) => {
  try {
    if (mainWindow && !mainWindow.isDestroyed() && mainWindow.setTitleBarOverlay) {
      mainWindow.setTitleBarOverlay(dark
        ? { color: "#18181B", symbolColor: "#A1A1AA", height: 36 }
        : { color: "#fafafa", symbolColor: "#52525B", height: 36 });
    }
  } catch (_) { /* 平台不支持时忽略 */ }
  return true;
});

// ───────────────────────── 本地 RAG（V73，Marvis 式本地+云端分层） ─────────────────────────
// 本地知识库 + BM25 检索（纯 JS 任何 CPU 可跑）+ 直连 LLM = 不依赖远程后端的完整 RAG。
const { LocalIndex } = require("./localrag.js");
let _lr_index = null;
function _lr_path() { return path.join(app.getPath("userData"), "localrag.json"); }
function _lr_load() {
  if (_lr_index) return _lr_index;
  try {
    _lr_index = LocalIndex.fromJSON(JSON.parse(fs.readFileSync(_lr_path(), "utf-8")));
  } catch (_) { _lr_index = new LocalIndex(); }
  return _lr_index;
}
function _lr_save() {
  try { fs.writeFileSync(_lr_path(), JSON.stringify(_lr_index.toJSON())); } catch (_) { /* */ }
}

// ── V77 语义检索（ONNX，可选启用；任何故障 → BM25 降级，主功能不受影响） ──
const SEM = require("./semantic.js");
const _MODEL_URLS = [   // 国内镜像优先，HF 兜底（bge-small-zh-v1.5 INT8 量化）
  "https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main/onnx/model_quantized.onnx",
  "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/onnx/model_quantized.onnx",
];
const _VOCAB_URLS = [
  "https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main/vocab.txt",
  "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/main/vocab.txt",
];
let _sem = { session: null, tokenizer: null, index: null, enabled: false };
function _sem_dir() { const d = path.join(app.getPath("userData"), "models"); fs.mkdirSync(d, { recursive: true }); return d; }
function _sem_idx_path() { return path.join(app.getPath("userData"), "semantic.json"); }

function _download(urls, dest, onPct) {
  // 依次尝试镜像；流式写盘带进度
  const NU = require("./services/net-util");
  return new Promise((resolve) => {
    let redirects = 0;
    const tryOne = (i) => {
      if (i >= urls.length) return resolve({ ok: false, error: "全部下载源失败" });
      const u = NU.safeParseUrl(urls[i]);
      if (!u) return tryOne(i + 1);            // 非法 URL → 跳过该源，绝不崩（修 Invalid URL 崩溃）
      const lib = u.protocol === "https:" ? https : http;
      const req = lib.get(u, { headers: { "User-Agent": "HashMM" } }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          res.resume();                        // 排空响应体
          if (++redirects > 8) return tryOne(i + 1);   // 防重定向死循环
          // 关键修复：Location 可能是相对路径，用当前 URL 作基地址解析成绝对。
          const abs = NU.resolveRedirect(res.headers.location, u);
          if (!abs) return tryOne(i + 1);
          urls[i] = abs;
          return tryOne(i);                    // 跟随重定向（同源重试）
        }
        if (res.statusCode !== 200) { res.resume(); return tryOne(i + 1); }
        const total = parseInt(res.headers["content-length"] || "0", 10);
        let got = 0;
        const ws = fs.createWriteStream(dest + ".part");
        res.on("data", (c) => { got += c.length; if (total && onPct) onPct(Math.round(got * 100 / total)); });
        res.pipe(ws);
        ws.on("finish", () => { try { fs.renameSync(dest + ".part", dest); resolve({ ok: true }); } catch (e) { resolve({ ok: false, error: e.message }); } });
        ws.on("error", () => tryOne(i + 1));
      });
      req.on("error", () => tryOne(i + 1));
      req.setTimeout(120000, () => { req.destroy(); tryOne(i + 1); });
    };
    tryOne(0);
  });
}

async function _sem_load() {
  // 惰性加载：onnxruntime-node 装不上/模型缺失 → 返回 false（BM25 降级）
  if (_sem.session) return true;
  let ort;
  try { ort = require("onnxruntime-node"); }
  catch (_) { return false; }
  const mp = path.join(_sem_dir(), "bge-small-zh-q8.onnx");
  const vp = path.join(_sem_dir(), "vocab.txt");
  if (!fs.existsSync(mp) || !fs.existsSync(vp)) return false;
  try {
    _sem.session = await ort.InferenceSession.create(mp);
    _sem.tokenizer = SEM.WordPiece.fromVocabText(fs.readFileSync(vp, "utf8"));
    try { _sem.index = SEM.SemanticIndex.fromJSON(JSON.parse(fs.readFileSync(_sem_idx_path(), "utf8"))); }
    catch (_) { _sem.index = new SEM.SemanticIndex(512); }
    return true;
  } catch (_) { _sem.session = null; return false; }
}

async function _sem_embed(text) {
  const ort = require("onnxruntime-node");
  const runFn = async (enc) => {
    const n = enc.inputIds.length;
    const feeds = {
      input_ids: new ort.Tensor("int64", BigInt64Array.from(enc.inputIds.map(BigInt)), [1, n]),
      attention_mask: new ort.Tensor("int64", BigInt64Array.from(enc.attentionMask.map(BigInt)), [1, n]),
      token_type_ids: new ort.Tensor("int64", BigInt64Array.from(enc.tokenTypeIds.map(BigInt)), [1, n]),
    };
    const out = await _sem.session.run(feeds);
    const first = out[Object.keys(out)[0]];
    return { data: first.data, dims: first.dims };
  };
  return SEM.embed(text, _sem.tokenizer, runFn);
}

ipcMain.handle("semantic:deviceCheck", () => {
  let hasOrt = true;
  try { require("onnxruntime-node"); } catch (_) { hasOrt = false; }
  const d = SEM.deviceCheck(os);
  const mp = path.join(_sem_dir(), "bge-small-zh-q8.onnx");
  let vectors = 0; try { if (_sem.index && _sem.index.stat) vectors = _sem.index.stat().vectors || 0; } catch (_) {}
  return { ...d, ortInstalled: hasOrt, modelDownloaded: fs.existsSync(mp), enabled: _sem.enabled, vectors };
});

ipcMain.handle("semantic:downloadModel", async () => {
  const push = (stage, pct) => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("semantic:progress", { stage, pct }); };
  const r1 = await _download([..._VOCAB_URLS], path.join(_sem_dir(), "vocab.txt"), (p) => push("vocab", p));
  if (!r1.ok) return r1;
  const r2 = await _download([..._MODEL_URLS], path.join(_sem_dir(), "bge-small-zh-q8.onnx"), (p) => push("model", p));
  return r2;
});

ipcMain.handle("semantic:enable", async () => {
  const ok = await _sem_load();
  _sem.enabled = ok;
  return { ok, reason: ok ? "" : "onnxruntime 未安装或模型未下载（已自动保持 BM25）" };
});
ipcMain.handle("semantic:disable", () => { _sem.enabled = false; return { ok: true }; });

ipcMain.handle("semantic:reindex", async () => {
  // 给现有 BM25 索引的全部 chunk 生成向量（增量场景简化为全量重建，量小可接受）
  if (!_sem.enabled || !(await _sem_load())) return { ok: false, error: "语义模式未启用" };
  const lr = _lr_load();
  _sem.index = new SEM.SemanticIndex(512);
  let done = 0;
  for (const c of lr.chunks) {
    try { _sem.index.setVec(c.id, await _sem_embed(c.text.slice(0, 800))); } catch (_) { /* 单块失败跳过 */ }
    done++;
    if (done % 10 === 0 && mainWindow && !mainWindow.isDestroyed())
      mainWindow.webContents.send("semantic:progress", { stage: "index", pct: Math.round(done * 100 / lr.chunks.length) });
  }
  try { fs.writeFileSync(_sem_idx_path(), JSON.stringify(_sem.index.toJSON())); } catch (_) { /* */ }
  return { ok: true, stat: _sem.index.stat() };
});

// ── V98 本地语义服务编排（modules/semantic-serve.js，main 只做装配） ──
// 把上面的小模型能力以 POST /local/embed 喂给本地后端 sidecar：
// 开关存 config.semanticServe（默认关），backend:start 时注入 HASHMM_LOCAL_EMBED_URL。
//
// V99 防御式加载（铁律 3 血的教训）：可选模块缺失/构造异常绝不能拖崩主进程。
// 任何失败 → 退化为"全关"安全空壳，方法签名齐全，下游 IPC/backend:start/attach
// 照常调用、行为=未启用。app.asar 漏打、模块语法错、依赖缺失都被这层兜住。
const NULL_SEM_SERVE = {
  isOn: () => false,
  setOn: () => ({ ok: false, error: "本地语义模块未加载（已安全降级，不影响其他功能）" }),
  attach: () => {},
  envFor: async () => ({}),
  provider: async () => null,
};
let semServe = NULL_SEM_SERVE;
try {
  const { createSemanticServe } = require("./modules/semantic-serve");
  semServe = createSemanticServe({
    loadConfig, saveConfig,
    semLoad: _sem_load, semEmbed: _sem_embed,
    getShell: () => shellSrv,
    log: (s) => console.log(s),
  }) || NULL_SEM_SERVE;
} catch (e) {
  console.error("[semantic-serve] 模块加载失败，已降级为关闭：", e && e.message);
}
ipcMain.handle("semantic:getServe", () => ({ ok: true, on: semServe.isOn() }));
ipcMain.handle("semantic:setServe", (_e, on) => semServe.setOn(!!on));

// V103.90：目录遍历抽成可复用函数（摄取与增量同步共用）。返回 [{path, mtime, size}]。
function _lr_walkFolder(base) {
  const SKIP = new Set(["node_modules", ".git", "__pycache__", ".next", "dist", "venv", ".venv"]);
  const out = [];
  const walk = (d, depth) => {
    if (out.length >= 300 || depth > 5) return;
    let names = [];
    try { names = fs.readdirSync(d, { withFileTypes: true }); } catch (_) { return; }
    for (const n of names) {
      if (out.length >= 300) return;
      if (n.name.startsWith(".") || SKIP.has(n.name)) continue;
      const fp = path.join(d, n.name);
      if (n.isDirectory()) { walk(fp, depth + 1); continue; }
      if (TEXT_EXT.has(path.extname(n.name).toLowerCase())) {
        try { const st = fs.statSync(fp); if (st.size <= 1024 * 1024) out.push({ path: fp, mtime: Math.round(st.mtimeMs), size: st.size }); } catch (_) {}
      }
    }
  };
  walk(base, 0);
  return out;
}

// 读取单个文件文本（二进制/超大跳过）。成功返回字符串，否则 null。
function _lr_readText(fp) {
  try {
    const st = fs.statSync(fp);
    if (st.size > 1024 * 1024) return null;
    const buf = fs.readFileSync(fp);
    if (buf.includes(0)) return null;
    return buf.toString("utf8");
  } catch (_) { return null; }
}

ipcMain.handle("localrag:pickAndIngest", async () => {
  // 选目录 → 递归摄取文本文件（≤300 个、单文件≤1MB），进度推前端
  const r = await dialog.showOpenDialog(mainWindow, {
    title: "选择要加入本地知识库的文件夹",
    properties: ["openDirectory"],
  });
  if (r.canceled || !r.filePaths.length) return { ok: false, canceled: true };
  const base = r.filePaths[0];
  const idx = _lr_load();
  const files = _lr_walkFolder(base);
  let nFiles = 0, nChunks = 0, skipped = 0;
  for (let i = 0; i < files.length; i++) {
    const { path: fp, mtime, size } = files[i];
    const text = _lr_readText(fp);
    if (text == null) { skipped++; continue; }
    try {
      // V103.90：绝对路径作键 + 记录 folder/mtime/size（支持后续按文件夹管理与增量同步）
      const added = idx.addDocument(fp, text, { folder: base, mtime, size });
      if (added > 0) { nFiles++; nChunks += added; }
    } catch (_) { skipped++; }
    if ((i % 10 === 0 || i === files.length - 1) && mainWindow && !mainWindow.isDestroyed())
      mainWindow.webContents.send("localrag:progress", { files: nFiles, chunks: nChunks, total: files.length, done: i + 1, current: path.basename(fp), skipped });
  }
  _lr_save();
  return { ok: true, base, ingested: { files: nFiles, chunks: nChunks, skipped }, stat: idx.stat() };
});

// V103.90 已索引文件夹列表（供 UI 分组展示与管理）
ipcMain.handle("localrag:folderList", () => {
  try { return { ok: true, folders: _lr_load().folderList(), stat: _lr_load().stat() }; }
  catch (e) { return { ok: false, error: e.message }; }
});
// V103.90 某文件夹下的文件清单
ipcMain.handle("localrag:fileList", (_e, { folder } = {}) => {
  try {
    const all = _lr_load().fileList();
    const list = folder ? all.filter(f => (f.folder || "") === folder) : all;
    return { ok: true, files: list };
  } catch (e) { return { ok: false, error: e.message }; }
});
// V103.90 移除单个文件夹（不再只能整库清空）
ipcMain.handle("localrag:removeFolder", (_e, { folder } = {}) => {
  try {
    if (!folder) return { ok: false, error: "缺 folder" };
    const idx = _lr_load();
    const r = idx.removeFolder(folder);
    _lr_save();
    return { ok: true, removed: r, stat: idx.stat() };
  } catch (e) { return { ok: false, error: e.message }; }
});
// V103.90 移除单个文件
ipcMain.handle("localrag:removeFile", (_e, { file } = {}) => {
  try {
    if (!file) return { ok: false, error: "缺 file" };
    const idx = _lr_load();
    const removed = idx.removeFile(file);
    _lr_save();
    return { ok: true, removed, stat: idx.stat() };
  } catch (e) { return { ok: false, error: e.message }; }
});
// V103.90 增量同步：重扫文件夹 → 只重摄 新增/改动、删除磁盘已不存在的，不全量重建
ipcMain.handle("localrag:sync", async (_e, { folder } = {}) => {
  try {
    if (!folder) return { ok: false, error: "缺 folder" };
    const idx = _lr_load();
    const current = _lr_walkFolder(folder);
    const diff = idx.diffFolder(folder, current);
    const curByNorm = new Map(current.map(f => [f.path.replace(/\\/g, "/"), f]));
    let reAdded = 0, reChunks = 0;
    const toIngest = diff.added.concat(diff.changed);
    for (let i = 0; i < toIngest.length; i++) {
      const norm = toIngest[i];
      const meta = curByNorm.get(norm);
      const realPath = meta ? meta.path : norm;
      const text = _lr_readText(realPath);
      if (text == null) continue;
      const added = idx.addDocument(realPath, text, { folder, mtime: meta ? meta.mtime : 0, size: meta ? meta.size : 0 });
      if (added > 0) { reAdded++; reChunks += added; }
      if (mainWindow && !mainWindow.isDestroyed())
        mainWindow.webContents.send("localrag:progress", { files: reAdded, chunks: reChunks, total: toIngest.length, done: i + 1, current: path.basename(realPath), sync: true });
    }
    // 删除磁盘上已不存在的
    let removedFiles = 0;
    for (const gone of diff.removed) { if (idx.removeFile(gone) > 0) removedFiles++; }
    _lr_save();
    return { ok: true, summary: { added: diff.added.length, changed: diff.changed.length, removed: diff.removed.length, unchanged: diff.unchanged.length, reChunks }, stat: idx.stat() };
  } catch (e) { return { ok: false, error: e.message }; }
});

ipcMain.handle("localrag:search", async (_e, { q, topK }) => {
  try {
    const lr = _lr_load();
    const k = topK || 5;
    const bm = lr.search(String(q || ""), k * 2);
    // V77: 语义启用且就绪 → 双路 RRF 融合；任何故障静默回 BM25
    if (_sem.enabled && _sem.session && _sem.index && _sem.index.stat().vectors > 0) {
      try {
        const qv = await _sem_embed(String(q || ""));
        const sem = _sem.index.search(qv, k * 2);
        const bmIds = bm.map(r => lr.chunks.findIndex(c => c.text === r.text && c.file === r.file)).filter(i => i >= 0);
        const fused = SEM.hybridFuse(bmIds, sem.map(s2 => s2.id), k * 2);
        // V80 方案B：融合后轻量重排（词覆盖+词距+位置），取 top-k
        const { tokenize } = require("./localrag.js");
        const reranked = SEM.lightRerank(tokenize(String(q || "")),
          fused.map(f => ({ score: f.fused, file: lr.chunks[f.id].file, text: lr.chunks[f.id].text })));
        return { ok: true, mode: "hybrid+rerank", results: reranked.slice(0, k) };
      } catch (_) { /* 降级 */ }
    }
    // BM25 纯路径同样过轻量重排（候选取 2k 再排回 k）
    const { tokenize } = require("./localrag.js");
    const rr = SEM.lightRerank(tokenize(String(q || "")), bm);
    return { ok: true, mode: "bm25+rerank", results: rr.slice(0, k) };
  } catch (e) { return { ok: false, error: e.message }; }
});
ipcMain.handle("localrag:stat", () => {
  try { return { ok: true, stat: _lr_load().stat() }; }
  catch (e) { return { ok: false, error: e.message }; }
});
ipcMain.handle("localrag:clear", () => {
  _lr_index = new LocalIndex();
  try { fs.unlinkSync(_lr_path()); } catch (_) { /* */ }
  return { ok: true };
});

// ───────────────────────── 直连 LLM（离线对话一期，V71） ─────────────────────────
// 没有 HashMM 后端时，用户用自己的 API Key 直连 OpenAI 兼容服务（DeepSeek 等）。
// 走主进程请求：Key 不出本机、无 CORS 限制；流式增量推回渲染层。
let llmAbort = null;
ipcMain.handle("llm:chat", (_e, { baseUrl, apiKey, model, messages }) => {
  return new Promise((resolve) => {
    try {
      const base = normalizeUrl(baseUrl || "https://api.deepseek.com");
      const u = new URL(base.replace(/\/+$/, "") + "/chat/completions");
      const lib = u.protocol === "https:" ? https : http;
      const body = JSON.stringify({ model: model || "deepseek-chat",
        messages: messages || [], stream: true });
      const req = lib.request({
        hostname: u.hostname, port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: u.pathname, method: "POST",
        headers: { "Content-Type": "application/json",
                   "Authorization": "Bearer " + (apiKey || ""),
                   "Content-Length": Buffer.byteLength(body) },
      }, (res) => {
        if (res.statusCode && res.statusCode >= 400) {
          let err = "";
          res.on("data", (c) => { err += c; });
          res.on("end", () => resolve({ ok: false, error: `HTTP ${res.statusCode}: ${err.slice(0, 300)}` }));
          return;
        }
        let buf = "";
        res.on("data", (chunk) => {
          buf += chunk.toString("utf8");
          let nl;
          while ((nl = buf.indexOf("\n")) !== -1) {
            const line = buf.slice(0, nl).trim();
            buf = buf.slice(nl + 1);
            if (!line.startsWith("data:")) continue;
            const payload = line.slice(5).trim();
            if (payload === "[DONE]") continue;
            try {
              const d = JSON.parse(payload);
              const delta = d.choices && d.choices[0] && d.choices[0].delta;
              const text = delta && (delta.content || delta.reasoning_content) || "";
              if (text && mainWindow && !mainWindow.isDestroyed())
                mainWindow.webContents.send("llm:delta", {
                  text: delta.content || "",
                  reasoning: delta.reasoning_content || "",
                });
            } catch (_) { /* 半截 JSON 行，忽略 */ }
          }
        });
        res.on("end", () => resolve({ ok: true }));
        res.on("error", (e) => resolve({ ok: false, error: e.message }));
      });
      req.on("error", (e) => resolve({ ok: false, error: e.message }));
      req.setTimeout(120000, () => { req.destroy(); resolve({ ok: false, error: "请求超时(120s)" }); });
      llmAbort = () => { try { req.destroy(); } catch (_) { /* */ } resolve({ ok: false, error: "已停止" }); };
      req.write(body);
      req.end();
    } catch (e) { resolve({ ok: false, error: e.message }); }
  });
});
ipcMain.on("llm:abort", () => { if (llmAbort) { llmAbort(); llmAbort = null; } });

// ── V103.90 模型连通性预检：GET {base}/models 验证 连通 + key 有效 + 模型存在，并列出可用模型 ──
ipcMain.handle("llm:testConnection", async (_e, { baseUrl, apiKey, model } = {}) => {
  const PF = require("./llm-preflight.js");
  return new Promise((resolve) => {
    try {
      const base = normalizeUrl(baseUrl || "https://api.deepseek.com");
      const u = new URL(base.replace(/\/+$/, "") + "/models");
      const lib = u.protocol === "https:" ? https : http;
      const req = lib.request({
        hostname: u.hostname, port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: u.pathname, method: "GET",
        headers: { "Authorization": "Bearer " + (apiKey || "") },
      }, (res) => {
        let buf = "";
        res.on("data", (c) => { if (buf.length < 200000) buf += c.toString("utf8"); });
        res.on("end", () => {
          const status = res.statusCode || 0;
          let json = null; try { json = buf ? JSON.parse(buf) : null; } catch (_e2) { json = null; }
          const models = PF.parseModelsResponse(json);
          resolve(PF.buildVerdict({ reachable: true, status, models, wantedModel: model, parseFailed: status < 400 && !json }));
        });
        res.on("error", () => resolve(PF.buildVerdict({ reachable: false, netKind: "refused" })));
      });
      req.on("error", (e) => resolve(PF.buildVerdict({ reachable: false, netKind: "refused", netCode: (e && e.code) || "" })));
      req.setTimeout(15000, () => { req.destroy(); resolve(PF.buildVerdict({ reachable: false, netKind: "timeout" })); });
      req.end();
    } catch (e) { resolve({ ok: false, level: "error", message: "✗ " + e.message, hint: "", models: [], modelFound: false }); }
  });
});

// ── V103.90 方案3：本地 LLM 推理（数据全程不出端的企业级私有模式）──
// 本地知识库(BM25) + 本地嵌入(ONNX) + 本地生成(本模块) = 检索/嵌入/生成全离线。
// 防御式加载：本地模型是【可选】功能，万一该模块未随包/加载失败，也只是此功能不可用，
// 绝不能让主进程启动崩溃（双击没反应）。故用降级桩兜底，所有 IPC 仍可安全调用并返回错误。
let LLLM;
try {
  LLLM = require("./localllm.js");
} catch (_e) {
  try { console.error("localllm 模块加载失败，本地模型功能禁用: " + (_e && _e.message)); } catch (_) { /* */ }
  LLLM = {
    DEFAULT_LLM_FILE: "qwen2.5-7b-hashmm-q4_k_m.gguf",
    isRuntimeAvailable: async () => false,
    localViability: () => ({ viable: false, target: "cloud", reason: "本地推理模块不可用", ready: false, modelPath: "", tier: "low" }),
    generate: async () => { throw new Error("本地推理模块不可用"); },
    unload: async () => ({ ok: true, unloaded: false }),
  };
}

// best-effort 探测本机 GPU 显存（NVIDIA）；拿不到则按纯 CPU 路径判定。零额外依赖。
function _detectHwForLlm() {
  const totalMemMB = Math.round(os.totalmem() / (1024 * 1024));
  const cpuCount = os.cpus().length;
  let totalVramMB = 0, hasGpu = false;
  try {
    const { execFileSync } = require("child_process");
    const out = execFileSync("nvidia-smi",
      ["--query-gpu=memory.total", "--format=csv,noheader,nounits"],
      { timeout: 4000, encoding: "utf8" });
    const mb = parseInt(String(out).split(/\r?\n/)[0].trim(), 10);
    if (Number.isFinite(mb) && mb > 0) { totalVramMB = mb; hasGpu = true; }
  } catch (_) { /* 无 nvidia-smi / 无 N 卡：保持 CPU 路径 */ }
  return { totalMemMB, cpuCount, totalVramMB, hasGpu };
}

// 本地模型可用性 + 运行时是否就绪（renderer 据此启用/禁用「本地模型」开关并显示原因）。
ipcMain.handle("llm:localStatus", async () => {
  try {
    const runtimeAvailable = await LLLM.isRuntimeAvailable();
    const hw = _detectHwForLlm();
    const v = LLLM.localViability({ hw, modelsDir: _sem_dir(), fileName: LLLM.DEFAULT_LLM_FILE });
    // V103.90：补 hw 明细与模型文件大小，供安装向导逐步判定与文件校验
    let modelSize = 0; try { if (v.ready && v.modelPath) modelSize = fs.statSync(v.modelPath).size; } catch (_) {}
    return {
      ok: true, runtimeAvailable, ...v, hw, modelSize,
      hint: !runtimeAvailable
        ? "未安装本地推理运行时（node-llama-cpp）。装上后可用自训模型在本机作答，数据不出端。"
        : (!v.ready ? "运行时就绪，但未发现本地模型文件。把转换好的 GGUF 放到模型目录即可。"
                    : (v.viable ? "本地模型就绪，可开启「本地模型」全程离线作答。" : v.reason)),
      modelFile: LLLM.DEFAULT_LLM_FILE, modelsDir: _sem_dir(),
    };
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});

// 本地生成：复用与云端相同的 llm:delta 事件流式回吐，renderer UI 零改动。
let _localBusy = false;
ipcMain.handle("llm:localGenerate", async (_e, opts) => {
  if (_localBusy) return { ok: false, error: "本地模型正在生成上一条，请稍候" };
  _localBusy = true;
  try {
    const o = opts || {};
    const hw = _detectHwForLlm();
    const v = LLLM.localViability({ hw, modelsDir: _sem_dir(), fileName: o.modelFile || LLLM.DEFAULT_LLM_FILE });
    if (!v.viable) return { ok: false, error: v.reason || "本地模型不可用", fallback: "cloud" };
    const text = await LLLM.generate({
      modelPath: v.modelPath,
      system: o.system || "",
      context: o.context || "",
      user: o.user || o.prompt || "",
      options: o.options || {},
      onToken: (chunk) => {
        if (chunk && mainWindow && !mainWindow.isDestroyed())
          mainWindow.webContents.send("llm:delta", { text: chunk, reasoning: "" });
      },
    });
    return { ok: true, text };
  } catch (e) {
    return { ok: false, error: String(e && e.message || e), fallback: "cloud" };
  } finally { _localBusy = false; }
});

// 释放本地模型（回收显存/内存）。
ipcMain.handle("llm:localUnload", async () => {
  try { return await LLLM.unload(); }
  catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});

// ── V82 Computer Use 一期（shell 型：LLM 操控本机，危险操作确认门） ──
const CU = require("./computeruse.js");
const { execFile } = require("child_process");

// V99 Computer Use 二期（GUI 动作工程）：纯逻辑校验 + 平台执行 + 回放审计。
// 防御式加载——模块缺失绝不崩主进程（铁律 3，V98 事故教训）。
let CUActions = null, CUDriver = null, cuRecorder = null, cuGuard = null;
try {
  CUActions = require("./modules/cu-actions");
  CUDriver = require("./modules/cu-driver");
  cuRecorder = new CUActions.ActionRecorder(500);
  // V99 Harness：统一工具守卫链（shell 危险 / 文件写 / GUI 策略 一条链裁决）
  const { createGuardChain } = require("./modules/cu-guard");
  cuGuard = createGuardChain({
    assessCommand: CU.assessCommand,
    validateAction: CUActions.validateAction,
    applyPolicy: CUActions.applyPolicy,
    describeAction: CUActions.describeAction,
  });
} catch (e) { console.error("[cu-actions] 动作工程加载失败，GUI 控制降级不可用：", e && e.message); }

// 当前主屏真实像素（归一化坐标 → 真实坐标用）
function _primaryScreenSize() {
  try { const { screen } = require("electron"); const s = screen.getPrimaryDisplay().size; return { width: s.width, height: s.height }; }
  catch (_) { return { width: 1920, height: 1080 }; }
}
// 复用 run_shell 的 UTF-8 解码逻辑跑 driver 命令
function _cuRunShell(shellPath, shellArgs) {
  return new Promise((resolve) => {
    try {
      execFile(shellPath, shellArgs, { timeout: 20000, maxBuffer: 2 * 1024 * 1024, encoding: "buffer" },
        (err, _o, errBuf) => {
          if (err) { let m = err.message; try { if (errBuf && errBuf.length) m = new TextDecoder("utf8").decode(errBuf) || m; } catch (_) {} resolve({ ok: false, error: m }); }
          else resolve({ ok: true });
        });
    } catch (e) { resolve({ ok: false, error: e.message }); }
  });
}
function chatToolsOnce({ baseUrl, apiKey, model, messages, tools, max_tokens }) {
  // 非流式 + function calling（Computer Use 循环用：要解析 tool_calls）
  return new Promise((resolve) => {
    try {
      const base = normalizeUrl(baseUrl || "https://api.deepseek.com");
      const u = new URL(base.replace(/\/+$/, "") + "/chat/completions");
      const lib = u.protocol === "https:" ? https : http;
      const payload = { model: model || "deepseek-chat", messages: messages || [], stream: false };
      // V103.90 关键：写大文件时，整份内容在 tool_call 的 arguments(JSON 字符串)里。
      // 不设 max_tokens 会用 API 默认(常 4096)，大文件被截断 → arguments 是残缺 JSON →
      // 前端 JSON.parse 失败 → args={} → write_file 收到 undefined path 报错并打转。
      // 给足额度（默认 8192，可经 cuMaxTokens 配置）从根上修掉。
      payload.max_tokens = max_tokens && max_tokens > 0 ? max_tokens : 8192;
      if (tools && tools.length) { payload.tools = tools; payload.tool_choice = "auto"; }
      const body = JSON.stringify(payload);
      const req = lib.request({ hostname: u.hostname, port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: u.pathname, method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + (apiKey || ""),
                   "Content-Length": Buffer.byteLength(body) } }, (res) => {
        let buf = "";
        res.on("data", (c) => { buf += c; });
        res.on("end", () => {
          try {
            const d = JSON.parse(buf);
            if (d.error) return resolve({ ok: false, error: d.error.message || "API 错误" });
            resolve({ ok: true, message: d.choices[0].message });
          } catch (e) { resolve({ ok: false, error: "解析失败: " + buf.slice(0, 200) }); }
        });
      });
      req.on("error", (e) => resolve({ ok: false, error: e.message }));
      req.setTimeout(90000, () => { req.destroy(); resolve({ ok: false, error: "超时" }); });
      req.write(body); req.end();
    } catch (e) { resolve({ ok: false, error: e.message }); }
  });
}
ipcMain.handle("llm:chatTools", (_e, o) => chatToolsOnce(o));

ipcMain.handle("cu:tools", (_e, { vision, control } = {}) => {
  const base = [...CU.TOOLS, ...CU.SYSTEM_TOOLS];
  if (vision) base.push(...CU.VISION_TOOLS);
  // GUI 控制工具仅在显式开启且动作工程就绪时注入（默认不给）
  if (vision && control && CUActions && CUDriver) base.push(...CU.CONTROL_TOOLS);
  return base;
});
ipcMain.handle("cu:meta", () => CU.TOOL_META);
// V99: 动作回放（审计面板/排障用）
ipcMain.handle("cu:replay", (_e, { n } = {}) => ({
  ok: true, events: cuRecorder ? cuRecorder.recent(n || 50) : [], text: cuRecorder ? cuRecorder.toText() : "",
}));
ipcMain.handle("cu:replayClear", () => { if (cuRecorder) cuRecorder.clear(); return { ok: true }; });
// V99: 电脑操作安全级别（normal / readonly / strict）。映射到 policy 开关，cu-guard 据此裁决。
ipcMain.handle("cu:getSafety", () => {
  const c = loadConfig();
  const level = c.cuReadOnly ? "readonly" : c.cuConfirmAllWrites ? "strict" : "normal";
  return { ok: true, level };
});
ipcMain.handle("cu:setSafety", (_e, level) => {
  const patch = { cuReadOnly: level === "readonly", cuConfirmAllWrites: level === "strict" };
  saveConfig(patch);
  return { ok: true, level: ["readonly", "strict", "normal"].includes(level) ? level : "normal" };
});

// V103.90 Computer Use 默认文件保存目录（写文件时相对路径/裸文件名落到这里；确认框也以此为默认）。
ipcMain.handle("cu:getFileDir", () => {
  const c = loadConfig();
  return { ok: true, dir: cuDefaultDir(), custom: !!(c.cuFileDir && String(c.cuFileDir).trim()) };
});
ipcMain.handle("cu:setFileDir", (_e, dir) => {
  const d = dir == null ? "" : String(dir).trim();   // 传空串=清除自定义，回落系统下载目录
  saveConfig({ cuFileDir: d });
  return { ok: true, dir: cuDefaultDir() };
});
ipcMain.handle("cu:pickFileDir", async () => {
  try {
    const r = await dialog.showOpenDialog(mainWindow, {
      title: "选择 Computer Use 默认保存目录",
      defaultPath: cuDefaultDir(),
      properties: ["openDirectory", "createDirectory"],
    });
    if (r.canceled || !r.filePaths || !r.filePaths[0]) return { ok: false, canceled: true };
    saveConfig({ cuFileDir: r.filePaths[0] });
    return { ok: true, dir: r.filePaths[0] };
  } catch (e) { return { ok: false, error: (e && e.message) || String(e) }; }
});

// V95: 结构化系统信息（窗口列表 + 概况）。Windows 用 EncodedCommand（Base64）
// 传 PS 脚本——彻底规避命令行引号/转义地狱（模型现编命令的 6 连败就栽在这）。
async function getSystemInfo(what) {
  const info = {
    os: `${os.type()} ${os.release()}`,
    platform: process.platform,
    homedir: os.homedir(),
    hostname: os.hostname(),
    now: new Date().toLocaleString(),
    windows: [],
  };
  try {
    if (process.platform === "win32") {
      const ps = "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8; " +
        "Get-Process | Where-Object { $_.MainWindowTitle -ne '' } | " +
        "Select-Object ProcessName, MainWindowTitle | ConvertTo-Json -Compress";
      const encoded = Buffer.from(ps, "utf16le").toString("base64");
      const out = await new Promise((resolve) => {
        execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
          { timeout: 15000, maxBuffer: 2 * 1024 * 1024, encoding: "buffer" },
          (err, stdoutBuf) => resolve(err ? "" : stdoutBuf.toString("utf8")));
      });
      if (out.trim()) {
        let parsed = JSON.parse(out);
        if (!Array.isArray(parsed)) parsed = [parsed];
        info.windows = parsed.map(w => ({ app: w.ProcessName, title: w.MainWindowTitle }));
      }
    } else if (process.platform === "darwin") {
      const out = await new Promise((resolve) => {
        execFile("osascript", ["-e",
          'tell application "System Events" to get name of (processes where background only is false)'],
          { timeout: 10000 }, (err, stdout) => resolve(err ? "" : stdout));
      });
      info.windows = out.split(",").map(s => ({ app: s.trim(), title: "" })).filter(w => w.app);
    } else {
      const out = await new Promise((resolve) => {
        execFile("/bin/bash", ["-lc", "wmctrl -l 2>/dev/null || true"],
          { timeout: 10000 }, (err, stdout) => resolve(stdout || ""));
      });
      info.windows = out.split("\n").filter(Boolean).map(line => {
        const parts = line.split(/\s+/); return { app: "", title: parts.slice(3).join(" ") };
      }).filter(w => w.title);
    }
  } catch (e) {
    return { ok: true, output: JSON.stringify({ ...info, windows_error: e.message }, null, 2) };
  }
  const summary = what === "windows"
    ? { windows: info.windows }
    : info;
  return { ok: true, output: JSON.stringify(summary, null, 2) };
}

// 截屏（视觉型 Computer Use）：微信式区域框选 + 标注，裁剪后返回
let _shotWin = null, _shotImage = null, _shotResolve = null, _shotHidden = [];

// V103.5: 截屏前被隐藏的 HashMM 窗口，截屏结束（确认/取消/出错）后统一恢复显示。
function _restoreShotHidden() {
  const wins = _shotHidden; _shotHidden = [];
  for (const w of wins) { try { if (w && !w.isDestroyed()) w.show(); } catch (_e) {} }
}

// 抓主屏一帧并编码为 JPEG dataURL。getSources 是这条链里最贵的一步（整屏抓取+缩略，常数百 ms），
// 也是「直接截屏仍顿一下」的主因——下面的预热就是把它从点击的关键路径上挪走。
async function _grabScreenJPEG() {
  const { desktopCapturer, screen } = require("electron");
  const primary = screen.getPrimaryDisplay();
  const { width, height } = primary.size;
  const sf = primary.scaleFactor || 1;
  const cap = require("./services/screenshot-util").capCaptureSize(width, height, sf, 2048);
  const sources = await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: { width: cap.width, height: cap.height } });
  if (!sources.length) return null;
  const src = sources.find(s => String(s.display_id) === String(primary.id)) || sources[0];
  try { return "data:image/jpeg;base64," + src.thumbnail.toJPEG(92).toString("base64"); }
  catch (_e) { return src.thumbnail.toDataURL(); }
}

// V103.16: 截屏预热——前端在「打开截屏菜单」的瞬间就调一次，把当前屏先抓好缓存起来。
// 等用户真点「直接截屏」时（间隔通常数百 ms，足够 getSources 跑完），cu:capture 直接用这帧、
// 跳过最贵的 getSources，冻结层几乎瞬间出现，逼近微信「按下即冻结」的手感。
let _shotPrewarm = null;  // { image, ts }
ipcMain.handle("cu:precapture", async () => {
  try { const img = await _grabScreenJPEG(); if (img) { _shotPrewarm = { image: img, ts: Date.now() }; return { ok: true }; } }
  catch (_e) { /* 预热失败不影响正常截屏 */ }
  return { ok: false };
});
ipcMain.handle("cu:capture", async (evt, opts) => {
  opts = opts || {};
  // V103.5: hideSelf（默认开）——截屏前隐藏 HashMM 自己的所有窗口（主窗 + 电脑操作浮层等
  // 置顶窗），让 desktopCapturer 截到的是"背后的纯净桌面"，不把 HashMM 界面/浮层一起截进去。
  // 这正是你看到的"截屏上面截屏（HashMM 界面被截进冻结帧）"的根因——之前 V101 为治卡顿删了
  // 这步，导致"隐藏窗口截屏"形同虚设。现配合 JPEG 编码，隐藏 + 最小等待仍跟手。"直接截屏"传 "0" 关闭。
  const hideSelf = !(opts.hideSelf === false || opts.hideSelf === "0" || opts.hideSelf === 0);
  try {
    const { desktopCapturer, screen, BrowserWindow } = require("electron");
    if (hideSelf) {
      // 先显式隐藏主窗（最显眼），再隐藏其它可见窗口
      try { if (mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible()) { mainWindow.hide(); _shotHidden.push(mainWindow); } } catch (_e) {}
      for (const w of BrowserWindow.getAllWindows()) {
        if (w && w !== mainWindow && !w.isDestroyed() && w.isVisible()) {
          try { w.hide(); _shotHidden.push(w); } catch (_e) {}
        }
      }
      // 等合成器把"没有这些窗口"的桌面重新画出来再截。V103.13: 140ms→320ms——
      // 部分机器（远程桌面/虚拟机/低配）140ms 内合成器还没重画完，desktopCapturer 会抓到
      // 仍带 HashMM 窗口的旧帧（你看到的"软件还在截屏画面里"）。加长更稳；JPEG 编码仍保证整体跟手。
      if (_shotHidden.length) await new Promise(r => setTimeout(r, 320));
    }
    const primary = screen.getPrimaryDisplay();
    // V103.16: 直接截屏优先用「预热帧」(开菜单时已抓好)——跳过最贵的 getSources，
    // 冻结层几乎瞬间出现，逼近微信「按下即冻结」。隐藏窗口截屏不能用预热帧（需隐藏后重抓）。
    let _usedPrewarm = false;
    if (!hideSelf && _shotPrewarm && (Date.now() - _shotPrewarm.ts) < 1500) {
      _shotImage = _shotPrewarm.image; _usedPrewarm = true;
    }
    _shotPrewarm = null;   // 用过即弃，避免下次拿到旧帧
    if (!_usedPrewarm) {
      const _img = await _grabScreenJPEG();
      if (!_img) { _restoreShotHidden(); return { ok: false, error: "未获取到屏幕源" }; }
      _shotImage = _img;
    }

    // 弹出全屏透明标注窗口
    return await new Promise((resolve) => {
      _shotResolve = resolve;
      _shotWin = new BrowserWindow({
        x: primary.bounds.x, y: primary.bounds.y,
        width: primary.bounds.width, height: primary.bounds.height,   // V103.14: 用 bounds（含任务栏区域）而非 size/workArea，确保盖住任务栏，杜绝"两个任务栏/截屏上叠截屏"
        frame: false,
        enableLargerThanScreen: true,
        // V103.2: 仿微信/QQ 截屏——不用系统全屏（fullscreen 会触发全屏过渡动画/闪），
        // 改用「无边框 + 精确屏幕尺寸 + 置顶」的**不透明**窗口；transparent 窗口在 show
        // 瞬间会与桌面合成、露出真实桌面，这正是"双重闪"的根因。不透明 + 纯黑底兜底，
        // 冻结帧未上屏前是黑屏而非穿透桌面，配合 screenshot:ready（decode+双rAF）画好再 show。
        transparent: false,
        backgroundColor: "#000000",
        fullscreen: false,
        hasShadow: false,
        alwaysOnTop: true, skipTaskbar: true, resizable: false, movable: false,
        show: false,
        paintWhenInitiallyHidden: true,   // 隐藏期间也渲染，确保 show 时帧已就绪
        webPreferences: { nodeIntegration: true, contextIsolation: false },
      });
      _shotWin.setAlwaysOnTop(true, "screen-saver");
      // V103.2: 显示门控——窗口绝不在「页面渲染过至少一帧」之前 show。
      // 即使 screenshot:ready 没来、走兜底，也先等 ready-to-show，杜绝兜底路径抢跑造成的闪。
      let _shown = false, _pageReady = false, _wantShow = false;
      const tryShow = () => {
        if (_shown || !_shotWin || _shotWin.isDestroyed()) return;
        if (!_pageReady) { _wantShow = true; return; }   // 页面还没画过帧 → 先挂起，待 ready-to-show 再 show
        _shown = true;
        try { _shotWin.setBounds(primary.bounds); } catch (_e) {}   // show 前再强制覆盖整个显示器（含任务栏）
        _shotWin.show(); _shotWin.focus();
        try { _shotWin.moveTop(); } catch (_e) {}                    // 置于最顶，盖住可能置顶的任务栏
      };
      _shotWin.once("ready-to-show", () => { _pageReady = true; if (_wantShow) tryShow(); });
      ipcMain.once("screenshot:ready", tryShow);   // 冻结帧 decode+双rAF 画好后的最佳信号
      setTimeout(tryShow, 1200);                    // 兜底放宽到 1200ms，且同样受 ready-to-show 门控
      _shotWin.loadFile(path.join(__dirname, "screenshot.html"));
      _shotWin.on("closed", () => {
        ipcMain.removeListener("screenshot:ready", tryShow);
        _shotWin = null;
        if (_shotResolve) { _shotResolve({ ok: false, error: "已取消" }); _shotResolve = null; }
        _restoreShotHidden();   // V103.5: 截屏结束，恢复之前隐藏的 HashMM 窗口
      });
    });
  } catch (e) { _restoreShotHidden(); return { ok: false, error: e.message }; }
});
ipcMain.handle("screenshot:getImage", () => _shotImage);
ipcMain.handle("screenshot:result", (_e, dataUrl) => {
  if (_shotResolve) {
    _shotResolve(dataUrl ? { ok: true, dataUrl } : { ok: false, error: "已取消" });
    _shotResolve = null;
  }
  if (_shotWin && !_shotWin.isDestroyed()) { _shotWin.destroy(); _shotWin = null; }
  _restoreShotHidden();   // V103.5: 兜底恢复（正常情况下 closed 也会恢复，幂等无副作用）
  return true;
});
async function cuExecOnce({ name, args }) {
  args = args || {};
  // V99 Harness：统一工具守卫链裁决（shell 危险 / 文件写 / GUI 策略 / 坐标禁区）。
  // computer 工具需先校验得到 plan 供策略闸判定；校验失败直接回错。
  let _plan = null;
  if (name === "computer") {
    if (!CUActions || !CUDriver) return { ok: false, error: "GUI 控制不可用（动作工程未加载）" };
    const v = CUActions.validateAction({
      type: args.action, x: args.x, y: args.y, x2: args.x2, y2: args.y2,
      text: args.text, keys: args.keys,
      scroll_direction: args.scroll_direction, scroll_amount: args.scroll_amount, ms: args.ms,
    }, _primaryScreenSize());
    if (!v.ok) return { ok: false, error: v.error };
    _plan = v.plan;
  }
  if (cuGuard) {
    const cfg = loadConfig();
    const verdict = cuGuard.decide({ name, args, plan: _plan,
      policy: { cuReadOnly: !!cfg.cuReadOnly, cuConfirmAllWrites: !!cfg.cuConfirmAllWrites } });
    if (verdict.decision === "deny") {
      if (_plan) cuRecorder && cuRecorder.record(_plan, { ok: false, error: verdict.reason });
      return { ok: false, error: verdict.reason };
    }
    if (verdict.decision === "confirm") {
      // V103.90 写文件确认时多给「选择位置…」，用户可自己挑保存路径（不再默认 C 盘）。
      const isWrite = name === "write_file";
      const buttons = isWrite ? ["允许写入", "选择位置…", "拒绝"] : ["允许执行", "拒绝"];
      const denyIdx = buttons.length - 1;
      const rawPath = String((args && args.path) || "").trim();
      const suggestedName = (rawPath && path.basename(rawPath)) || "untitled.txt";
      const targetPreview = isWrite
        ? (path.isAbsolute(rawPath) ? rawPath : path.join(cuDefaultDir(), suggestedName))
        : "";
      const r = await dialog.showMessageBox(mainWindow, {
        type: "warning", buttons, defaultId: denyIdx, cancelId: denyIdx,
        title: verdict.title || "Computer Use 需要确认",
        message: _plan ? `Agent 想执行：${CUActions.describeAction(_plan)}` : `Agent 想执行 ${name}`,
        detail: (verdict.detail || verdict.reason || "") + (isWrite ? `\n\n将保存到：${targetPreview}\n（点「选择位置…」可改）` : ""),
      });
      if (isWrite && r.response === 1) {
        // 选择位置 → 保存对话框；用户挑好后覆盖 args.path
        const sr = await dialog.showSaveDialog(mainWindow, {
          title: "选择保存位置", defaultPath: targetPreview || path.join(cuDefaultDir(), suggestedName),
        });
        if (sr.canceled || !sr.filePath) {
          if (_plan) cuRecorder && cuRecorder.record(_plan, { ok: false, error: "用户取消保存" });
          return { ok: false, error: "用户取消了保存" };
        }
        args = Object.assign({}, args, { path: sr.filePath });
      } else if (r.response !== 0) {
        if (_plan) cuRecorder && cuRecorder.record(_plan, { ok: false, error: "用户拒绝" });
        return { ok: false, error: "用户拒绝了此操作" };
      }
    }
  }
  try {
    if (name === "run_shell") {
      // V95: 根治中文 Windows 乱码 —— PowerShell 输出编码强制 UTF-8，
      // 命令前置 chcp 65001；用 buffer 收集再按 UTF-8 解码（失败兜底 GBK）。
      const isWin = process.platform === "win32";
      const shell = isWin ? "powershell.exe" : "/bin/bash";
      let cmd = args.command;
      let shellArgs;
      if (isWin) {
        // 让本会话 PS 的输出/控制台编码都切到 UTF-8，再执行原命令
        const prelude = "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8; chcp 65001 > $null; ";
        shellArgs = ["-NoProfile", "-NonInteractive", "-Command", prelude + cmd];
      } else {
        shellArgs = ["-lc", cmd];
      }
      return await new Promise((resolve) => {
        execFile(shell, shellArgs,
          { cwd: args.cwd || os.homedir(), timeout: 60000, maxBuffer: 4 * 1024 * 1024, encoding: "buffer" },
          (err, stdoutBuf, stderrBuf) => {
            const dec = (buf) => {
              if (!buf || !buf.length) return "";
              try {
                const u = buf.toString("utf8");
                if (!u.includes("\uFFFD")) return u;          // 无替换符=确实是 UTF-8
              } catch (_e) { /* fall through */ }
              try { return new TextDecoder("gbk").decode(buf); } // 兜底按 GBK
              catch (_e2) { return buf.toString("utf8"); }
            };
            const out = dec(stdoutBuf) + dec(stderrBuf);
            resolve({ ok: !err, output: out.slice(0, 30000), error: err ? err.message : "" });
          });
      });
    }
    if (name === "get_system_info") {
      return await getSystemInfo(args.what || "all");
    }
    if (name === "read_file") {
      const txt = fs.readFileSync(args.path, "utf8");
      return { ok: true, output: txt.slice(0, 20000) };
    }
    if (name === "write_file") {
      // V103.90 健壮化：校验 path；相对路径/裸文件名落到默认目录；自动建父目录。
      let p = (args.path != null) ? String(args.path).trim() : "";
      if (!p) {
        return { ok: false, error: "write_file 缺少 path 参数。请提供文件路径：可以是绝对路径（如 C:\\Users\\你\\file.py），也可以只给文件名（会存到默认目录）。注意要把完整 content 一起传过来。" };
      }
      if (!path.isAbsolute(p)) p = path.join(cuDefaultDir(), p);
      try { fs.mkdirSync(path.dirname(p), { recursive: true }); } catch (_e) { /* */ }
      fs.writeFileSync(p, String(args.content || ""), "utf8");
      return { ok: true, output: `已写入 ${p}` };
    }
    if (name === "list_dir") {
      const items = fs.readdirSync(args.path, { withFileTypes: true })
        .slice(0, 200).map(d => (d.isDirectory() ? "[D] " : "    ") + d.name);
      return { ok: true, output: items.join("\n") };
    }
    if (name === "capture_screen") {
      // V94: 静默全屏抓图（CU 自动循环不能弹交互选区）。返回 dataURL 供视觉注入。
      const { desktopCapturer, screen } = require("electron");
      const disp = screen.getPrimaryDisplay();
      const srcs = await desktopCapturer.getSources({
        types: ["screen"],
        thumbnailSize: { width: Math.min(disp.size.width, 1920), height: Math.min(disp.size.height, 1080) },
      });
      const src = srcs.find(s => String(s.display_id) === String(disp.id)) || srcs[0];
      if (!src) return { ok: false, error: "未找到屏幕源" };
      // V103.3: 视觉截图同样改 JPEG——agent 每步都截屏，PNG 编码拖慢整个 computer-use；
      // JPEG(q90) 对视觉识别足够清晰且快数倍。失败退回 PNG。
      let _visImg;
      try { _visImg = "data:image/jpeg;base64," + src.thumbnail.toJPEG(90).toString("base64"); }
      catch (_e) { _visImg = src.thumbnail.toDataURL(); }
      return { ok: true, output: "已截取当前屏幕（见图）", vision: true, image: _visImg };
    }
    if (name === "computer") {
      // V99：校验与守卫裁决已在入口完成（_plan 已就绪），此处只执行 + 回放。
      const plan = _plan;
      if (plan.type === "cursor_position") {
        cuRecorder && cuRecorder.record(plan, { ok: true });
        return { ok: true, output: "光标位置查询（归一化坐标见后续截屏）" };
      }
      const exec = await CUDriver.executePlan(plan, _cuRunShell, process.platform);
      cuRecorder && cuRecorder.record(plan, exec);
      return exec.ok
        ? { ok: true, output: `已执行：${CUActions.describeAction(plan)}` }
        : { ok: false, error: exec.error };
    }
    return { ok: false, error: "未知工具: " + name };
  } catch (e) { return { ok: false, error: e.message }; }
}
ipcMain.handle("cu:exec", (_e, p) => cuExecOnce(p));

// V103: 真·Agent 循环（harness/loop/computer use 内核）。前端给目标，主进程跑多步：
//   chatToolsOnce(模型) ↔ cuExecOnce(守卫执行) ↔ 结果回填，复用 computeruse 安全闸；
//   每一步以 "cu:loopEvent" 推给前端 cockpit 实时显示。危险/写操作的最终确认仍由
//   cuExecOnce 内的 dialog 守卫闸把关，故循环层 confirm 直接放行（不重复弹窗）。
const { AgentLoop } = (() => { try { return require("./agent-loop"); } catch (_e) { return {}; } })();
let _activeLoop = null;
ipcMain.handle("cu:runLoop", async (_e, o) => {
  o = o || {};
  if (!AgentLoop) return { ok: false, error: "Agent 循环模块缺失（agent-loop.js 未随包）" };
  const cfg = loadConfig();
  const loop = new AgentLoop({
    callModel: (messages, tools) => chatToolsOnce({
      baseUrl: o.baseUrl || cfg.cuBaseUrl || cfg.baseUrl,
      apiKey:  o.apiKey  || cfg.cuApiKey  || cfg.apiKey,
      model:   o.model   || cfg.cuModel   || cfg.model,
      max_tokens: o.maxTokens || cfg.cuMaxTokens || cfg.maxTokens || 8192,
      messages, tools,
    }),
    execTool: ({ name, args }) => cuExecOnce({ name, args }),
    onEvent: (ev) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        try { mainWindow.webContents.send("cu:loopEvent", ev); } catch (_) { /* */ }
      }
    },
    maxSteps: o.maxSteps || 16,
  });
  _activeLoop = loop;
  const tools = [].concat(CU.TOOLS, CU.SYSTEM_TOOLS);
  if (o.vision) tools.push.apply(tools, CU.VISION_TOOLS);
  if (o.control && CU.CONTROL_TOOLS) tools.push.apply(tools, CU.CONTROL_TOOLS);
  try {
    return await loop.run({ goal: o.goal, system: o.system, tools, history: o.history });
  } finally { _activeLoop = null; }
});
ipcMain.handle("cu:stopLoop", () => { if (_activeLoop) _activeLoop.abort(); return { ok: true }; });

// ── 远程传输（远程桌面）── V103.15 起，V103.16 加 WebRTC P2P
// 三层、逐层退化，对照大厂（参考 marvis）做的「无服务器」版：
//  · 信令：宿主自带的零依赖 WebSocket 服务端（services/remote-server.js，Node 内置 http+crypto 手写
//    RFC6455，无需 npm/rebuild）。局域网：viewer 连同源宿主，offer/answer/ICE 经 WS 中继。
//    跨网络（零服务器）：手动粘贴 邀请码/应答码（经投屏窗 IPC 交换）。
//  · 媒体：WebRTC 视频轨——Chromium 硬件编码 VP8/VP9/H.264，ICE+公共 STUN 穿 NAT，DTLS-SRTP 加密（=TLS）。
//    抓屏+RTCPeerConnection 只能在渲染进程，故另起一个隐藏「投屏窗」(remote-host.html)。收不到 P2P 视频
//    时自动回退 MJPEG（WS 推 JPEG 帧）。
//  · 输入：viewer 的输入经 WS 直达主进程 cu-driver（validateAction→executePlan，与 computer 工具同一条
//    安全链，含敏感区确认/坐标 clamp）。
// 【实测边界】信令中继 + 配对 + MJPEG + 输入注入链已在 127.0.0.1 loopback 端到端单测通过
//  （test_remote-server.js 12 项 + test_remote-signaling.js 9 项）。WebRTC 真实媒体协商（getDisplayMedia/
//  RTCPeerConnection/STUN 穿透）依赖 Chromium 运行时，沙箱无法跑，需在你的真机上联调确认。
let _remoteSrv = null;
let _remotePrivacyOn = false;   // V103.51 隐私防护：被控端黑屏/锁输入开关
// 注入一次输入——与 computer 工具完全同一条链：validateAction（吃 0..1000 归一坐标）→ executePlan。
// 提到模块级：局域网 RemoteServer 和账号模式（投屏页经 IPC 转发的输入）共用同一条安全注入链。
async function _remoteInjectInput(ev) {
  try {
    // V103.51 隐私防护：开启后拒绝一切远端输入注入（配合 host 端黑屏，守护本机隐私）。
    if (_remotePrivacyOn) return;

    // V104：系统指令（重启/关机）与剪贴板同步——不经 computer 链，单独处理。
    // 锁屏/显示桌面/任务管理器由 App 侧改用系统组合键（win+l / win+d / ctrl+shift+escape），仍走 computer 链。
    if (ev && ev.action === "system") {
      try {
        const cmd = String(ev.cmd || "");
        const plat = process.platform;
        let line = null;
        if (cmd === "reboot") line = plat === "win32" ? "shutdown /r /t 0" : plat === "darwin" ? "osascript -e 'tell app \"System Events\" to restart'" : "systemctl reboot";
        else if (cmd === "shutdown") line = plat === "win32" ? "shutdown /s /t 0" : plat === "darwin" ? "osascript -e 'tell app \"System Events\" to shut down'" : "systemctl poweroff";
        else if (cmd === "lock") line = plat === "win32" ? "rundll32.exe user32.dll,LockWorkStation" : plat === "darwin" ? "pmset displaysleepnow" : "loginctl lock-session";
        if (line) { const { exec } = require("child_process"); exec(line, () => {}); }
      } catch (_se) { /* 系统指令失败不影响会话 */ }
      return;
    }
    if (ev && ev.action === "clipboard_set") {
      try { require("electron").clipboard.writeText(String(ev.text || "")); } catch (_ce) { /* */ }
      return;
    }

    if (!CUActions || !CUDriver) return;
    // V103.90：先过控制协议校验，拒绝畸形/越界注入（安全+鲁棒；兼容旧 {action,x,y} 形状）。
    try {
      const _RP = require("./remote-protocol.js");
      const _v = _RP.validateInput(ev || {});
      if (!_v.ok) return;   // 非法输入直接丢弃，不进驱动
    } catch (_pe) { /* 协议模块缺失则退回原校验，不阻断 */ }
    const v = CUActions.validateAction({
      type: ev.action, x: ev.x, y: ev.y, x2: ev.x2, y2: ev.y2,
      text: ev.text, keys: ev.keys,
      scroll_direction: ev.scroll_direction, scroll_amount: ev.scroll_amount, ms: ev.ms,
    }, _primaryScreenSize());
    if (!v.ok || !v.plan) return;
    if (v.plan.type === "cursor_position") return;   // 纯查询，无需执行
    await CUDriver.executePlan(v.plan, _cuRunShell, process.platform);
  } catch (_e) { /* 单次输入失败不影响整个会话 */ }
}
// 账号模式：投屏渲染页把 viewer 经后端中继来的输入转给主进程，这里注入。
ipcMain.on("remote-host-input", (_e, ev) => { _remoteInjectInput(ev || {}); });

// V104 接力：手机端把对话交接到本桌面端 —— 唤起窗口、通知、并转发给渲染层尝试打开该会话。
ipcMain.on("remote-handoff", (_e, data) => {
  try {
    if (mainWindow && !mainWindow.isDestroyed()) {
      try { if (mainWindow.isMinimized()) mainWindow.restore(); } catch (_re) {}
      try { mainWindow.show(); mainWindow.focus(); } catch (_fe) {}
      try { mainWindow.webContents.send("hashmm-open-conversation", data || {}); } catch (_se) {}
    }
    const title = (data && data.title) || "对话";
    try { new Notification({ title: "已接力到本机", body: "手机已把对话交接过来：" + title }).show(); } catch (_ne) {}
  } catch (_e) { /* 接力失败不影响主流程 */ }
});

// ── V103.52 会话内文件传输（被控端落盘）+ 多屏 ──────────────────
// 文件字节经信令转发到这里（小块 base64），用 remote-filetransfer 的 ChunkAssembler 重组、
// 校验后落盘到「下载」目录。回复（accept/progress/done）经 host 渲染进程的信令发回 viewer。
const _ftRecv = new Map();   // ftid -> { asm, meta, sender(payload) }
function _replyToHost(payload) {
  // 把回复发给 host 渲染窗（它再经信令转发给对应 viewer）。两种模式的窗口都试。
  for (const w of [_remoteHostWin, _remoteAcctWin]) {
    try { if (w && !w.isDestroyed()) w.webContents.send("remote-host-reply", payload); } catch (_e) {}
  }
}
ipcMain.on("remote-host-file", (_e, m) => {
  try {
    const ft = require("./services/remote-filetransfer.js");
    if (!m || !m.ftid) return;
    if (m.type === "fileOffer") {
      // V103.90 幂等：若已有同 ftid 的部分接收（重连后对端重发 offer），复用现有 assembler，不抹掉已收块
      if (_ftRecv.has(m.ftid)) { _replyToHost({ type: "fileAccept", vid: m.vid, ftid: m.ftid }); return; }
      const meta = { id: m.ftid, name: String(m.name || "file"), size: Number(m.size) || 0, totalChunks: Number(m.totalChunks) || 0, mime: m.mime };
      const policy = ft.checkPolicy(meta, { currentConcurrent: _ftRecv.size });
      if (!policy.allowed) { _replyToHost({ type: "fileReject", vid: m.vid, ftid: m.ftid, reason: policy.reason }); return; }
      _ftRecv.set(m.ftid, { asm: new ft.ChunkAssembler(meta), meta, vid: m.vid, lastPct: 0 });
      _replyToHost({ type: "fileAccept", vid: m.vid, ftid: m.ftid });
    } else if (m.type === "fileResumeQuery") {
      // V103.90 断点续传：对端重连后问"我还缺哪些块"。有部分接收→回缺块清单；查无此传输→missing:null 让对端从头重发。
      const rec = _ftRecv.get(m.ftid);
      if (!rec) { _replyToHost({ type: "fileResumeState", vid: m.vid, ftid: m.ftid, missing: null }); return; }
      let missing = [];
      try { const ex = require("./filetransfer-extras.js"); missing = ex.missingChunks([...rec.asm.received.keys()], rec.asm.totalChunks); }
      catch (_e) { missing = null; }
      rec.vid = m.vid;   // 重连后 viewer 的 vid 可能变，刷新回包目标
      _replyToHost({ type: "fileResumeState", vid: m.vid, ftid: m.ftid, missing, received: rec.asm.received.size, total: rec.asm.totalChunks });
    } else if (m.type === "fileChunk") {
      const rec = _ftRecv.get(m.ftid); if (!rec) return;
      let buf; try { buf = Buffer.from(String(m.data || ""), "base64"); } catch (_e) { return; }
      const r = rec.asm.addChunk(Number(m.seq), buf);
      if (r && typeof r.progress === "number" && r.progress - rec.lastPct >= 0.03) {
        rec.lastPct = r.progress;
        _replyToHost({ type: "fileProgress", vid: rec.vid, ftid: m.ftid, pct: r.progress });
      }
    } else if (m.type === "fileDone") {
      const rec = _ftRecv.get(m.ftid); if (!rec) return;
      _ftRecv.delete(m.ftid);
      try {
        if (typeof m.checksum === "number") rec.asm.expectedChecksum = m.checksum >>> 0;
        const full = rec.asm.assemble();
        if (full) {
          const dir = (app && app.getPath) ? app.getPath("downloads") : require("os").tmpdir();
          const safe = String(rec.meta.name).replace(/[\\/:*?"<>|]/g, "_");
          let dest = path.join(dir, safe);
          let n = 1;
          while (fs.existsSync(dest)) { const e = path.extname(safe), b = path.basename(safe, e); dest = path.join(dir, `${b} (${n++})${e}`); }
          fs.writeFileSync(dest, full);
          _replyToHost({ type: "fileProgress", vid: rec.vid, ftid: m.ftid, pct: 1 });
          try { if (_remoteHostWin && !_remoteHostWin.isDestroyed()) _remoteHostWin.webContents.send("toast", `已接收文件：${safe}`); } catch (_e) {}
        }
      } catch (err) {
        _replyToHost({ type: "fileReject", vid: rec.vid, ftid: m.ftid, reason: String((err && err.message) || err) });
      }
    } else if (m.type === "fileCancel") {
      _ftRecv.delete(m.ftid);
    }
  } catch (_e) { /* 文件传输失败不影响会话 */ }
});
// 多屏：查询本机显示器列表 → 经 host 发回 viewer
ipcMain.on("remote-host-monitors-query", (_e, args) => {
  try {
    const { screen } = require("electron");
    const displays = screen.getAllDisplays();
    const primaryId = screen.getPrimaryDisplay().id;
    const monitors = displays.map((d, i) => ({
      id: d.id, index: i, primary: d.id === primaryId,
      width: d.size.width, height: d.size.height, scaleFactor: d.scaleFactor,
      label: `显示器 ${i + 1}${d.id === primaryId ? "（主）" : ""} · ${d.size.width}×${d.size.height}`,
    }));
    _replyToHost({ type: "monitorList", vid: args && args.vid, monitors, current: _remoteCurrentMonitor });
  } catch (_e) { /* */ }
});
// 多屏：切换被控端采集的显示器（记录选择；实际换源在投屏渲染进程按 sourceId 重捕获）
let _remoteCurrentMonitor = null;
ipcMain.on("remote-host-select-monitor", (_e, m) => {
  try {
    _remoteCurrentMonitor = (m && m.monitor != null) ? m.monitor : _remoteCurrentMonitor;
    for (const w of [_remoteHostWin, _remoteAcctWin]) {
      try { if (w && !w.isDestroyed()) w.webContents.send("remote-switch-monitor", { id: m.monitor, index: m.index }); } catch (_e2) {}
    }
  } catch (_e) { /* */ }
});
// 列出本机局域网 IPv4，供查看端在另一台设备上输入连接。
function _lanAddrs() {
  const out = [];
  try {
    const ni = require("os").networkInterfaces();
    for (const name of Object.keys(ni)) {
      for (const a of (ni[name] || [])) {
        if (a && a.family === "IPv4" && !a.internal) out.push(a.address);
      }
    }
  } catch (_e) { /* */ }
  return out;
}
function _getRemoteServer() {
  if (_remoteSrv) return _remoteSrv;
  let RemoteServer, PairingManager;
  try {
    ({ RemoteServer } = require("./services/remote-server"));
    ({ PairingManager } = require("./services/remote-pairing"));
  } catch (e) {
    console.error("[remote] 远程模块加载失败：", e && e.message);
    return null;
  }
  // 抓一帧屏幕（JPEG Buffer）。比截屏标注更激进地压小+降质（1280 封顶、q60），换流畅度与带宽。
  const captureFrame = async () => {
    try {
      const { desktopCapturer, screen } = require("electron");
      const disp = screen.getPrimaryDisplay();
      const { width, height } = disp.size;
      const sf = disp.scaleFactor || 1;
      let cap = { width: Math.min(width, 1280), height: Math.min(height, 720) };
      try { cap = require("./services/screenshot-util").capCaptureSize(width, height, sf, 1280); } catch (_e) { /* 用兜底 cap */ }
      const srcs = await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: { width: cap.width, height: cap.height } });
      const src = srcs.find(s => String(s.display_id) === String(disp.id)) || srcs[0];
      if (!src) return null;
      try { return src.thumbnail.toJPEG(60); } catch (_e) { return null; }
    } catch (_e) { return null; }
  };
  // 注入一次输入——复用模块级 _remoteInjectInput（与账号模式共用同一条安全链）。
  const injectInput = (ev) => _remoteInjectInput(ev);
  const frameMeta = () => {
    const s = _primaryScreenSize();
    return { sw: s.width, sh: s.height, fw: Math.min(s.width, 1280), fh: Math.min(s.height, 720) };
  };
  let hostName = "HashMM";
  try { hostName = require("os").hostname() || "HashMM"; } catch (_e) { /* */ }
  // 查看端网页（控制方浏览器打开 http://<本机IP>:<端口>/ 即得，免安装）。
  let viewerHtml = null;
  try { viewerHtml = fs.readFileSync(path.join(__dirname, "remote-viewer.html"), "utf8"); } catch (_e) { /* 老包可能没有，GET / 会给占位页 */ }
  // ICE：默认免费公共 STUN；用户在「远程」页填了自己的/免费 TURN 会存进 config.remoteIce。
  let iceServers;
  try { const c = loadConfig(); if (Array.isArray(c.remoteIce) && c.remoteIce.length) iceServers = c.remoteIce; } catch (_e) { /* */ }
  // 固定 hostToken（投屏渲染窗用它注册为 host；远程 viewer 不知此 token，无法冒充投屏端）。
  const hostToken = require("crypto").randomBytes(16).toString("hex");
  _remoteSrv = new RemoteServer({
    pairing: new PairingManager(),
    captureFrame, injectInput, frameMeta,
    fps: 8,
    name: hostName,
    viewerHtml,
    hostToken,
    iceServers,
  });
  _remoteSrv._hostToken = hostToken;   // 留个引用，起投屏窗时拼进 URL
  return _remoteSrv;
}

// ── 投屏渲染进程（隐藏窗）：getDisplayMedia 抓屏 + WebRTC 推流只能在渲染进程做 ──
let _remoteHostWin = null;
let _displayHandlerSet = false;
function _ensureDisplayMediaHandler() {
  if (_displayHandlerSet) return;
  try {
    const { session, desktopCapturer, screen } = require("electron");
    // 让投屏窗的 getDisplayMedia 无需弹选择器：自动授权主屏。
    session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
      desktopCapturer.getSources({ types: ["screen"] }).then((sources) => {
        const primary = screen.getPrimaryDisplay();
        const src = sources.find(s => String(s.display_id) === String(primary.id)) || sources[0];
        callback(src ? { video: src } : {});
      }).catch(() => callback({}));
    }, { useSystemPicker: false });
    _displayHandlerSet = true;
  } catch (e) { console.error("[remote] setDisplayMediaRequestHandler 失败（WebRTC 投屏将不可用，MJPEG 仍可）：", e && e.message); }
}
function _startRemoteHostWin(port, token) {
  try {
    const { BrowserWindow } = require("electron");
    if (_remoteHostWin && !_remoteHostWin.isDestroyed()) return;
    _ensureDisplayMediaHandler();
    _remoteHostWin = new BrowserWindow({
      width: 320, height: 200, show: false, skipTaskbar: true,
      webPreferences: { nodeIntegration: true, contextIsolation: false, backgroundThrottling: false },
    });
    const url = "file://" + path.join(__dirname, "remote-host.html") + `?port=${encodeURIComponent(port)}&token=${encodeURIComponent(token)}`;
    _remoteHostWin.loadURL(url);
    _remoteHostWin.on("closed", () => { _remoteHostWin = null; });
  } catch (e) { console.error("[remote] 启动投屏渲染窗失败（WebRTC 不可用，MJPEG 仍可）：", e && e.message); }
}
function _stopRemoteHostWin() {
  try { if (_remoteHostWin && !_remoteHostWin.isDestroyed()) _remoteHostWin.destroy(); } catch (_e) {}
  _remoteHostWin = null;
}

// ── 账号模式（跨网络·同账号直连）：信令走公网账号后端的 /api/remote/ws ──
let _remoteAcctWin = null;     // 账号模式投屏窗（被控端·隐藏）
let _remoteViewerWins = [];    // 账号模式查看端窗（控制端·可见）
let _lastAcctToken = "";       // V105 最近一次的 Supabase access_token（前端刷新后会推过来），用于保活在线 + 看门狗重启托管
// 由当前已连后端地址派生信令 WS 地址：http(s)://host:port → ws(s)://host:port/api/remote/ws
function _signalUrlFromBackend() {
  let base = null;
  try { base = (currentBackend && currentBackend.url) || (shellSrv && shellSrv.getTarget && shellSrv.getTarget()); } catch (_e) {}
  if (!base) { try { const d = defaultBackend(); base = d && d.url; } catch (_e) {} }
  if (!base) return "";
  try {
    const u = new URL(base);
    const proto = u.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${u.host}/api/remote/ws`;
  } catch (_e) { return ""; }
}
// 当前已连后端的 HTTP base（http(s)://host[:port]）——帧中继推帧用，和查看端 App 用的是同一后端。
function _backendHttpBase() {
  let base = null;
  try { base = (currentBackend && currentBackend.url) || (shellSrv && shellSrv.getTarget && shellSrv.getTarget()); } catch (_e) {}
  if (!base) { try { const d = defaultBackend(); base = d && d.url; } catch (_e) {} }
  if (!base) return "";
  try { const u = new URL(base); return `${u.protocol}//${u.host}`; } catch (_e) { return ""; }
}
function _startAccountHostWin(token) {
  try {
    const { BrowserWindow } = require("electron");
    token = token || _lastAcctToken;
    if (!token) return { ok: false, error: "未登录账号，无法远程（请先登录同一账号）" };
    _lastAcctToken = token;
    if (_remoteAcctWin && !_remoteAcctWin.isDestroyed()) return { ok: true, mode: "supabase" };
    _ensureDisplayMediaHandler();
    let hostName = "被控端"; try { hostName = require("os").hostname() || "被控端"; } catch (_e) {}
    _remoteAcctWin = new BrowserWindow({
      width: 320, height: 200, show: false, skipTaskbar: true,
      webPreferences: { nodeIntegration: true, contextIsolation: false, backgroundThrottling: false },
    });
    // Supabase 信令 P2P：同账号 + 联网即可被控，无需后端公网可达/隧道。
    const url = "file://" + path.join(__dirname, "remote-host.html") +
      `?mode=supabase&token=${encodeURIComponent(token || "")}&name=${encodeURIComponent(hostName)}&apiBase=${encodeURIComponent(_backendHttpBase())}`;
    _remoteAcctWin.loadURL(url);
    _remoteAcctWin.on("closed", () => { _remoteAcctWin = null; });
    return { ok: true, mode: "supabase" };
  } catch (e) { return { ok: false, error: String(e) }; }
}
function _stopAccountHostWin() {
  try { if (_remoteAcctWin && !_remoteAcctWin.isDestroyed()) _remoteAcctWin.destroy(); } catch (_e) {}
  _remoteAcctWin = null;
}
ipcMain.handle("remote:startAccountHost", (_e, token) => {
  _ensureDisplayMediaHandler();
  return _startAccountHostWin(token);
});
ipcMain.handle("remote:stopAccountHost", () => { _stopAccountHostWin(); return { ok: true }; });
ipcMain.handle("remote:accountHostStatus", () => ({ ok: true, on: !!(_remoteAcctWin && !_remoteAcctWin.isDestroyed()), signal: _signalUrlFromBackend() }));
// V105 前端在 Supabase token 刷新后把新令牌推过来：存起来 + 热推给被控窗，让在线状态永不因令牌过期而掉线。
ipcMain.on("remote:updateAccountToken", (_e, token) => {
  try {
    if (!token || typeof token !== "string" || token.length < 20) return;
    _lastAcctToken = token;
    // 热推给所有被控投屏窗（账号窗 + LAN/兜底窗），任一都可能在做 relay/push，避免漏掉导致 401。
    for (const w of [_remoteAcctWin, _remoteHostWin]) {
      try { if (w && !w.isDestroyed()) w.webContents.send("remote-host-token", token); } catch (_e2) {}
    }
  } catch (_e) {}
});
// 打开一个可见的查看端窗（控制端），账号模式直连同账号设备。
ipcMain.handle("remote:openAccountViewer", (_e, opts) => {
  try {
    opts = opts || {};
    const { BrowserWindow } = require("electron");
    const signal = _signalUrlFromBackend();
    if (!signal) return { ok: false, error: "未连接账号后端" };
    let myName = "查看端"; try { myName = (require("os").hostname() || "查看端") + " 控制端"; } catch (_e) {}
    const win = new BrowserWindow({
      width: 1100, height: 720, show: true, title: "HashMM 远程控制",
      // 与主窗口同形态：隐藏系统标题栏（去掉被系统主题染蓝的标题栏）+ 浅色覆盖层控件 + 隐藏菜单。
      titleBarStyle: "hidden",
      titleBarOverlay: { color: "#ffffff", symbolColor: "#52525B", height: 38 },
      autoHideMenuBar: true,
      backgroundColor: "#f8f9fa",
      webPreferences: { nodeIntegration: false, contextIsolation: true },
    });
    try { win.setMenuBarVisibility(false); } catch (_e) {}
    let url = "file://" + path.join(__dirname, "remote-viewer.html") +
      `?mode=account&signal=${encodeURIComponent(signal)}&token=${encodeURIComponent(opts.token || "")}&name=${encodeURIComponent(myName)}`;
    if (opts.target) url += `&target=${encodeURIComponent(opts.target)}`;
    win.loadURL(url);
    win.on("closed", () => { _remoteViewerWins = _remoteViewerWins.filter(w => w !== win); });
    _remoteViewerWins.push(win);
    return { ok: true };
  } catch (e) { return { ok: false, error: String(e) }; }
});
ipcMain.handle("remote:start", async (_e, opts) => {
  opts = opts || {};
  const srv = _getRemoteServer();
  if (!srv) return { ok: false, error: "远程模块未加载（services/remote-server.js 未随包）" };
  const r = await srv.start(opts.port || 17690, opts.host || "0.0.0.0");
  if (!r.ok) return r;
  const code = srv.issueCode();   // 起服务即签发首个配对码
  _startRemoteHostWin(r.port, srv._hostToken);   // 拉起投屏渲染窗（WebRTC 投屏）；失败仍有 MJPEG
  return { ok: true, port: r.port, code: code && code.code, expiresAt: code && code.expiresAt, addrs: _lanAddrs() };
});
ipcMain.handle("remote:stop", () => { _stopRemoteHostWin(); _stopAccountHostWin(); if (_remoteSrv) _remoteSrv.stop(); return { ok: true }; });
ipcMain.handle("remote:status", () => {
  if (!_remoteSrv) return { ok: true, running: false, clients: 0, paired: 0, addrs: _lanAddrs() };
  return Object.assign({ ok: true }, _remoteSrv.status(), { addrs: _lanAddrs() });
});
ipcMain.handle("remote:issueCode", () => {
  const srv = _getRemoteServer();
  if (!srv) return { ok: false, error: "远程模块未加载" };
  const code = srv.issueCode();
  return { ok: true, code: code && code.code, expiresAt: code && code.expiresAt };
});
ipcMain.handle("remote:currentCode", () => {
  if (!_remoteSrv) return { ok: true, code: null };
  const cur = _remoteSrv.currentCode();
  return { ok: true, code: cur && cur.code, expiresAt: cur && cur.expiresAt, remainingMs: cur && cur.remainingMs };
});
// 设置 ICE 服务器（用户填了免费/自有 TURN）。存配置 + 即时生效。
ipcMain.handle("remote:setIce", (_e, iceServers) => {
  try {
    const arr = Array.isArray(iceServers) ? iceServers : [];
    saveConfig({ remoteIce: arr });
    if (_remoteSrv) _remoteSrv.setIceServers(arr.length ? arr : [{ urls: "stun:stun.l.google.com:19302" }, { urls: ["turn:openrelay.metered.ca:80", "turn:openrelay.metered.ca:443", "turns:openrelay.metered.ca:443?transport=tcp"], username: "openrelayproject", credential: "openrelayproject" }]);
    return { ok: true };
  } catch (e) { return { ok: false, error: String(e) }; }
});
ipcMain.handle("remote:getIce", () => {
  try { const c = loadConfig(); return { ok: true, iceServers: Array.isArray(c.remoteIce) ? c.remoteIce : [] }; }
  catch (_e) { return { ok: true, iceServers: [] }; }
});

// ── V103.51 对标 UU 远程的扩展能力 ──────────────────────────────────────
// 远程开机（WOL）：发魔术包叫醒同网段的目标机器。纯逻辑在 services/remote-wol.js。
ipcMain.handle("remote:wake", async (_e, args) => {
  try {
    const { wake } = require("./services/remote-wol.js");
    const r = await wake(String((args && args.mac) || ""), {
      port: args && args.port, address: args && args.address, count: args && args.count,
    });
    return { ok: true, ...r };
  } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});
// 开机目标簿：存到 config.wolTargets（[{id,name,mac,address?}]）。
ipcMain.handle("remote:listWolTargets", () => {
  try { const c = loadConfig(); return { ok: true, targets: Array.isArray(c.wolTargets) ? c.wolTargets : [] }; }
  catch (_e) { return { ok: true, targets: [] }; }
});
ipcMain.handle("remote:saveWolTarget", (_e, t) => {
  try {
    const c = loadConfig();
    const list = Array.isArray(c.wolTargets) ? c.wolTargets : [];
    const id = (t && t.id) || `wol_${Date.now().toString(36)}`;
    const next = list.filter(x => x.id !== id);
    next.push({ id, name: String((t && t.name) || "我的电脑"), mac: String((t && t.mac) || ""), address: (t && t.address) || "" });
    saveConfig({ wolTargets: next });
    return { ok: true, id, targets: next };
  } catch (e) { return { ok: false, error: String(e) }; }
});
ipcMain.handle("remote:removeWolTarget", (_e, id) => {
  try {
    const c = loadConfig();
    const next = (Array.isArray(c.wolTargets) ? c.wolTargets : []).filter(x => x.id !== id);
    saveConfig({ wolTargets: next });
    return { ok: true, targets: next };
  } catch (e) { return { ok: false, error: String(e) }; }
});
// 多屏协作：枚举本机显示器（被控端把列表发给查看端选屏）。
ipcMain.handle("remote:listMonitors", () => {
  try {
    const { screen } = require("electron");
    const displays = screen.getAllDisplays();
    const primaryId = screen.getPrimaryDisplay().id;
    return {
      ok: true,
      monitors: displays.map((d, i) => ({
        id: d.id, index: i, primary: d.id === primaryId,
        width: d.size.width, height: d.size.height, scaleFactor: d.scaleFactor,
        label: `显示器 ${i + 1}${d.id === primaryId ? "（主）" : ""} · ${d.size.width}×${d.size.height}`,
      })),
    };
  } catch (e) { return { ok: false, error: String(e) }; }
});
// 画质：把 fps / jpeg 质量下发给投屏服务与投屏窗（真彩=高 jpeg 质量）。
ipcMain.handle("remote:setQuality", (_e, q) => {
  try {
    const fps = Math.max(1, Math.min(30, Number(q && q.fps) || 8));
    const jpeg = Math.max(30, Math.min(95, Number(q && q.jpeg) || 70));
    saveConfig({ remoteQuality: { fps, jpeg } });
    if (_remoteSrv && typeof _remoteSrv.setQuality === "function") _remoteSrv.setQuality({ fps, jpeg });
    for (const w of [_remoteHostWin, _remoteAcctWin]) {
      try { if (w && !w.isDestroyed()) w.webContents.send("remote-set-quality", { fps, jpeg }); } catch (_e) {}
    }
    return { ok: true, fps, jpeg };
  } catch (e) { return { ok: false, error: String(e) }; }
});
// 隐私防护：被控端黑屏遮挡 + 锁本地输入（开启访客时一键守护屏幕隐私）。
ipcMain.handle("remote:setPrivacy", (_e, on) => {
  try {
    _remotePrivacyOn = !!on;
    for (const w of [_remoteHostWin, _remoteAcctWin]) {
      try { if (w && !w.isDestroyed()) w.webContents.send("remote-set-privacy", _remotePrivacyOn); } catch (_e) {}
    }
    return { ok: true, privacy: _remotePrivacyOn };
  } catch (e) { return { ok: false, error: String(e) }; }
});
// 返回查看端网页内容——跨网络手动模式时，宿主把它存成文件发给控制方（对方本地打开即用，无需连上宿主）。
ipcMain.handle("remote:viewerHtml", () => {
  try { return { ok: true, html: fs.readFileSync(path.join(__dirname, "remote-viewer.html"), "utf8") }; }
  catch (e) { return { ok: false, error: String(e) }; }
});
// ── 跨网络手动信令（零服务器）：经投屏窗生成邀请码 / 应用应答码 ──
ipcMain.handle("remote:manualOffer", async () => {
  if (!_remoteHostWin || _remoteHostWin.isDestroyed()) return { ok: false, error: "请先开启远程（投屏端未就绪）" };
  return await new Promise((resolve) => {
    const to = setTimeout(() => { ipcMain.removeListener("manual-offer", on); resolve({ ok: false, error: "生成邀请码超时（屏幕捕获可能被拒）" }); }, 12000);
    const on = (_evt, payload) => { clearTimeout(to); ipcMain.removeListener("manual-offer", on); resolve(payload && payload.ok ? { ok: true, code: payload.sdp } : { ok: false, error: (payload && payload.error) || "生成失败" }); };
    ipcMain.once("manual-offer", on);
    try { _remoteHostWin.webContents.send("manual-create"); } catch (e) { clearTimeout(to); ipcMain.removeListener("manual-offer", on); resolve({ ok: false, error: String(e) }); }
  });
});
ipcMain.handle("remote:manualAnswer", async (_e, code) => {
  if (!_remoteHostWin || _remoteHostWin.isDestroyed()) return { ok: false, error: "投屏端未就绪" };
  return await new Promise((resolve) => {
    const to = setTimeout(() => { ipcMain.removeListener("manual-applied", on); resolve({ ok: false, error: "应用应答码超时" }); }, 8000);
    const on = (_evt, payload) => { clearTimeout(to); ipcMain.removeListener("manual-applied", on); resolve(payload && payload.ok ? { ok: true } : { ok: false, error: (payload && payload.error) || "应答码无效" }); };
    ipcMain.once("manual-applied", on);
    try { _remoteHostWin.webContents.send("manual-answer", code); } catch (e) { clearTimeout(to); ipcMain.removeListener("manual-applied", on); resolve({ ok: false, error: String(e) }); }
  });
});

ipcMain.handle("hashmm:getConfig", () => loadConfig());

// ── V103.90 配置导入导出（文件读写在主进程；序列化/校验/脱敏在渲染层用 config-portability）──
ipcMain.handle("config:exportToFile", async (_e, { json, suggestName } = {}) => {
  try {
    const r = await dialog.showSaveDialog(mainWindow, {
      title: "导出 HashMM 配置",
      defaultPath: path.join(defaultDownloadDir(), suggestName || "hashmm-config.json"),
      filters: [{ name: "JSON 配置", extensions: ["json"] }],
    });
    if (r.canceled || !r.filePath) return { ok: false, canceled: true };
    fs.writeFileSync(r.filePath, String(json || "{}"), "utf-8");
    return { ok: true, path: r.filePath };
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});
ipcMain.handle("config:importFromFile", async () => {
  try {
    const r = await dialog.showOpenDialog(mainWindow, {
      title: "导入 HashMM 配置", properties: ["openFile"],
      filters: [{ name: "JSON 配置", extensions: ["json"] }],
    });
    if (r.canceled || !r.filePaths.length) return { ok: false, canceled: true };
    const content = fs.readFileSync(r.filePaths[0], "utf-8");
    if (content.length > 1024 * 1024) return { ok: false, error: "配置文件过大" };
    return { ok: true, content };
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});
ipcMain.handle("config:applyMainPatch", (_e, { patch } = {}) => {
  try { if (patch && typeof patch === "object") saveConfig(patch); return { ok: true }; }
  catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});

// ── V103.90 对话历史持久化（存 userData/hashmm-conversations.json）──
function _convPath() { return path.join(app.getPath("userData"), "hashmm-conversations.json"); }
ipcMain.handle("conv:load", () => {
  try { return { ok: true, content: fs.existsSync(_convPath()) ? fs.readFileSync(_convPath(), "utf-8") : "" }; }
  catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});
ipcMain.handle("conv:save", (_e, { json } = {}) => {
  try {
    const s = String(json || "");
    if (s.length > 20 * 1024 * 1024) return { ok: false, error: "对话历史过大" };  // 20MB 上限
    fs.writeFileSync(_convPath(), s, "utf-8");
    return { ok: true };
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});

// ── V103.90 对话导出（Markdown/文本直接写文件；PDF 用隐藏窗口 printToPDF）──
ipcMain.handle("export:saveText", async (_e, { content, suggestName } = {}) => {
  try {
    const r = await dialog.showSaveDialog(mainWindow, {
      title: "导出对话",
      defaultPath: path.join(defaultDownloadDir(), suggestName || "对话.md"),
      filters: [{ name: "Markdown", extensions: ["md"] }, { name: "文本", extensions: ["txt"] }],
    });
    if (r.canceled || !r.filePath) return { ok: false, canceled: true };
    fs.writeFileSync(r.filePath, String(content || ""), "utf-8");
    return { ok: true, path: r.filePath };
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
});
ipcMain.handle("export:savePdf", async (_e, { html, suggestName } = {}) => {
  let win = null;
  try {
    const r = await dialog.showSaveDialog(mainWindow, {
      title: "导出对话为 PDF",
      defaultPath: path.join(defaultDownloadDir(), suggestName || "对话.pdf"),
      filters: [{ name: "PDF", extensions: ["pdf"] }],
    });
    if (r.canceled || !r.filePath) return { ok: false, canceled: true };
    win = new BrowserWindow({ show: false, webPreferences: { javascript: false, sandbox: true } });
    await win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(String(html || "<html><body></body></html>")));
    const pdf = await win.webContents.printToPDF({ printBackground: true, margins: { marginType: "default" } });
    fs.writeFileSync(r.filePath, pdf);
    return { ok: true, path: r.filePath };
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
  finally { try { if (win && !win.isDestroyed()) win.destroy(); } catch (_) {} }
});

// ── V96: 文件保存（微信式下载位置管理）──
// saveMode: "ask" 每次询问 | "fixed" 固定文件夹（saveDir）。默认 fixed→系统下载夹。
function defaultDownloadDir() {
  try { return app.getPath("downloads"); } catch (_) { return app.getPath("userData"); }
}

// ── V97: 安装类型识别（微信式：分清全新安装 / 更新 / 正常启动）──
// 原理：userData/install-state.json 记录上次运行的版本与首装时间。
//   文件不存在        → fresh（全新安装）
//   记录版本 != 当前   → update（覆盖安装/自动更新后首启）
//   相同              → normal
// userData 在卸载时默认保留，所以"卸载重装"也能识别为 update（除非用户手动清了数据）。
let _installInfo = { type: "normal", previousVersion: "", currentVersion: "", firstInstallAt: 0 };
function detectInstallType() {
  const cur = app.getVersion();
  const stateFile = path.join(app.getPath("userData"), "install-state.json");
  let prev = null;
  try { prev = JSON.parse(fs.readFileSync(stateFile, "utf8")); } catch (_) { /* 不存在 */ }
  const now = Date.now();
  if (!prev || !prev.version) {
    _installInfo = { type: "fresh", previousVersion: "", currentVersion: cur, firstInstallAt: now };
  } else if (prev.version !== cur) {
    _installInfo = { type: "update", previousVersion: prev.version, currentVersion: cur,
                     firstInstallAt: prev.firstInstallAt || now };
  } else {
    _installInfo = { type: "normal", previousVersion: prev.version, currentVersion: cur,
                     firstInstallAt: prev.firstInstallAt || now };
  }
  try {
    fs.writeFileSync(stateFile, JSON.stringify({
      version: cur, firstInstallAt: _installInfo.firstInstallAt,
      lastRunAt: now, runCount: ((prev && prev.runCount) || 0) + 1,
    }, null, 2));
  } catch (e) { console.warn("[install] 写状态失败:", e.message); }
  console.log(`[install] 类型=${_installInfo.type} 当前=${cur} 上次=${_installInfo.previousVersion || "（无）"}`);
}
ipcMain.handle("hashmm:installInfo", () => _installInfo);
ipcMain.handle("files:getSaveConfig", () => {
  const c = loadConfig();
  return { saveMode: c.saveMode || "fixed", saveDir: c.saveDir || defaultDownloadDir() };
});
ipcMain.handle("files:chooseSaveDir", async () => {
  const r = await dialog.showOpenDialog(mainWindow, {
    title: "选择文件保存位置", properties: ["openDirectory", "createDirectory"],
    defaultPath: loadConfig().saveDir || defaultDownloadDir(),
  });
  if (r.canceled || !r.filePaths[0]) return { ok: false };
  saveConfig({ saveDir: r.filePaths[0] });
  return { ok: true, saveDir: r.filePaths[0] };
});
ipcMain.handle("files:setSaveMode", (_e, mode) => {
  saveConfig({ saveMode: mode === "ask" ? "ask" : "fixed" }); return true;
});
// 保存一份文本/二进制内容到磁盘。content 支持纯文本或 dataURL(base64)。
ipcMain.handle("files:save", async (_e, { filename, content, isDataUrl }) => {
  try {
    const cfg = loadConfig();
    const safeName = String(filename || "download").replace(/[\\/:*?"<>|]/g, "_");
    let target;
    if ((cfg.saveMode || "fixed") === "ask") {
      const r = await dialog.showSaveDialog(mainWindow, {
        title: "保存文件", defaultPath: path.join(cfg.saveDir || defaultDownloadDir(), safeName),
      });
      if (r.canceled || !r.filePath) return { ok: false, canceled: true };
      target = r.filePath;
    } else {
      const dir = cfg.saveDir || defaultDownloadDir();
      fs.mkdirSync(dir, { recursive: true });
      target = path.join(dir, safeName);
      // 重名加序号，不覆盖（微信式）
      if (fs.existsSync(target)) {
        const ext = path.extname(safeName), base = path.basename(safeName, ext);
        let i = 1;
        while (fs.existsSync(path.join(dir, `${base} (${i})${ext}`))) i++;
        target = path.join(dir, `${base} (${i})${ext}`);
      }
    }
    if (isDataUrl) {
      const b64 = String(content).replace(/^data:[^;]+;base64,/, "");
      fs.writeFileSync(target, Buffer.from(b64, "base64"));
    } else {
      fs.writeFileSync(target, String(content), "utf8");
    }
    return { ok: true, path: target };
  } catch (e) { return { ok: false, error: e.message }; }
});
ipcMain.handle("files:revealInFolder", (_e, p) => { try { shell.showItemInFolder(p); return true; } catch (_) { return false; } });
ipcMain.handle("files:openPath", (_e, p) => { try { shell.openPath(p); return true; } catch (_) { return false; } });

// V91: 关于页手动检查更新（10s 超时；未配置更新源时人话提示）
ipcMain.handle("hashmm:checkUpdate", async () => {
  if (!updateFeedReady) {
    return { ok: false, message: "未配置更新源（连接后端后自动启用）" };
  }
  // V101: 检查逻辑委托 UpdateService.checkOnce（promisify 事件 + 超时，已单测）
  let autoUpdater;
  try { autoUpdater = require("electron-updater").autoUpdater; }
  catch (_) { return { ok: false, message: "更新组件未安装（开发模式属正常）" }; }
  try {
    const { UpdateService } = require("./services/update-service");
    const svc = (require("./services/registry").getRegistry().tryGet("update")) || new UpdateService({ logger: __log() });
    const r = await svc.checkOnce(autoUpdater, 10000);
    if (r.available) return { ok: true, message: `发现新版本 v${r.version || "?"}，正在后台下载…` };
    if (r.error) return { ok: false, message: "检查失败：" + r.error };
    return { ok: true, message: "已是最新版本" };
  } catch (e) { return { ok: false, message: "检查失败：" + ((e && e.message) || e) }; }
});

// ── V87: 本地后端 sidecar（无需 autodl 的本机模式）──
ipcMain.handle("backend:detect", (_e, custom) => backendMgr.detectPython(custom));
ipcMain.handle("backend:setup", async (_e, o) => {
  const det = backendMgr.detectPython(o && o.python);
  if (!det.ok) return det;
  return backendMgr.setup({ pythonCmd: det.cmd, home: backendHome(), rtDir: runtimeDir(),
                            srcDir: backendSrcDir(), mirror: (o && o.mirror) || "" });
});
// V103.14: 本地后端启动助手——统一注入用户在 UI 选的功能档位（config.featurePreset），
// backend:start 与 feature:setPreset 都走它，保证档位一致生效。o.env 在 backendmgr 内于
// HASHMM_PRESET 默认值之后展开，故这里塞进 env 即可覆盖默认。
async function startLocalBackend(port) {
  const cfg = loadConfig();
  const env0 = await semServe.envFor();
  const env = cfg.featurePreset ? { ...env0, HASHMM_PRESET: cfg.featurePreset } : env0;
  return backendMgr.start({
    home: backendHome(), srcDir: backendSrcDir(), rtDir: runtimeDir(),
    port: port || cfg.localPort || LOCAL_BACKEND_PORT,
    env,
  });
}
ipcMain.handle("backend:start", async (_e, o) => startLocalBackend(o && o.port));
// V103.14: 功能档位——用户在 UI 切「基础/推荐/全开」，持久化到 config 并重启本地后端生效。
ipcMain.handle("feature:getPreset", () => ({ preset: loadConfig().featurePreset || "recommended" }));
ipcMain.handle("feature:setPreset", async (_e, preset) => {
  if (!["basic", "recommended", "max"].includes(preset)) return { ok: false, error: "无效档位" };
  saveConfig({ featurePreset: preset });
  try { backendMgr.stop(); } catch (_e) {}
  await new Promise(r => setTimeout(r, 700));        // 等端口释放再重启
  try {
    const r = await startLocalBackend();
    return { ok: r && r.ok !== false, preset, error: r && r.error };
  } catch (e) { return { ok: false, error: e.message }; }
});
ipcMain.handle("backend:stop", () => backendMgr.stop());
ipcMain.handle("backend:resetEnv", () => backendMgr.resetEnv(backendHome()));
ipcMain.handle("backend:status", () => backendMgr.status(backendHome()));
ipcMain.handle("backend:logs", () => backendMgr.logs());

// V102: 能力包（按需下载的 Python 运行时 / OCR）状态与安装。
//   pack:status(id)  -> { installed, dir }
//   pack:install(id) -> { ok, dir | error }，过程通过 "pack:progress" 推进度
// 装好 python-runtime 后，runtimeDir() 自动指向它，下次「启动本地后端」即零环境直跑。
ipcMain.handle("pack:status", (_e, id) => {
  const m = capPacks();
  return { installed: m.isInstalled(id), dir: m.installedPath(id) };
});
ipcMain.handle("pack:install", async (_e, id) => {
  const m = capPacks();
  return m.install(id, {
    onProgress: (pct) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        try { mainWindow.webContents.send("pack:progress", { id, pct }); } catch (_) { /* */ }
      }
    },
  });
});

// V101: 后端数据目录（解析文档/知识库的落盘位置）查询与选择。默认安装位置，可改。
ipcMain.handle("backend:getDataDir", () => {
  const dir = backendHome();
  const def = path.join(installDir(), "local-backend");
  return { ok: true, dataDir: dir, isDefault: dir === def, defaultDir: def };
});
ipcMain.handle("backend:chooseDataDir", async () => {
  try {
    const r = await dialog.showOpenDialog(mainWindow || undefined, {
      title: "选择后端数据保存位置", properties: ["openDirectory", "createDirectory"],
      defaultPath: backendHome(),
    });
    if (r.canceled || !r.filePaths[0]) return { ok: false, canceled: true };
    // 校验目录合规（与安装目录同样的隔离约束：不写系统目录）
    const chosen = r.filePaths[0];
    const G = require("./isolation/env-guard");
    const roots = G.allowedRoots({ installDir: installDir(), userData: app.getPath("userData"), tmpDir: require("os").tmpdir() });
    // 允许安装目录/userData/临时之外的用户自选目录（用户主动选的盘符目录），但挡掉系统关键目录
    const low = chosen.toLowerCase().replace(/[\\/]+$/, "");
    if (low === "c:\\windows" || low.startsWith("c:\\windows\\") || /^[a-z]:$/.test(low)) {
      return { ok: false, error: "不能选择系统目录或盘符根目录" };
    }
    saveConfig({ backendDataDir: chosen });
    return { ok: true, dataDir: chosen, needRestart: true };  // 重启后端后生效
  } catch (e) { return { ok: false, error: String(e) }; }
});
ipcMain.handle("backend:resetDataDir", () => {
  saveConfig({ backendDataDir: "" });   // 清配置 → 回默认安装位置
  return { ok: true, dataDir: backendHome(), needRestart: true };
});
ipcMain.handle("backend:setAuto", (_e, on) => { saveConfig({ localAuto: !!on }); return true; });
ipcMain.handle("backend:getLocalCfg", () => {
  const c = loadConfig();
  const bundled = !!backendMgr.bundledPython(runtimeDir());
  return { localAuto: !!c.localAuto, port: c.localPort || LOCAL_BACKEND_PORT,
           srcDir: backendSrcDir(), home: backendHome(), bundled,
           envReady: backendMgr.envReady(backendHome(), { rtDir: runtimeDir() }) };
});
ipcMain.handle("shell:info", () => shellSrv
  ? { origin: shellSrv.origin, port: shellSrv.port, target: shellSrv.getTarget() } : null);

ipcMain.handle("hashmm:connect", async (_evt, { url, token }) => {
  const clean = normalizeUrl(url);
  const r = await checkHealth(clean);
  if (!r.ok) {
    // V103.90：返回结构化失败信息（kind/code/ms + http 状态），由渲染层给精准的人话提示
    return { success: false, kind: r.kind || "unknown", code: r.code || "", ms: r.ms, status: r.status || 0,
             error: `后端不可达或 /api/health 未通过（${r.ms}ms）` };
  }
  rememberBackend(clean, token);
  if (global.__loadRemote) global.__loadRemote(clean, token || "");
  // V103.90：把健康详情（就绪/知识库/模型/GPU）一并回传，连接成功后展示"后端是什么"
  return { success: true, ms: r.ms, url: clean, detail: r.detail || null };
});
ipcMain.handle("hashmm:probe", (_evt, url) => checkHealth(url, 5000));
ipcMain.handle("hashmm:appVersion", () => app.getVersion());
ipcMain.handle("hashmm:goLocal", () => {
  // V70: 离线工作台已删——goLocal = 回连接页（与主 UI 同款设计的单卡页）
  currentBackend = null;
  if (mainWindow && !mainWindow.isDestroyed())
    mainWindow.loadFile(path.join(__dirname, "app.html"));
  return true;
});

ipcMain.handle("hashmm:reset", () => {
  currentBackend = null;
  if (mainWindow) mainWindow.webContents.send("nav", "settings");
  return true;
});
ipcMain.handle("hashmm:openTerminal", () => {
  if (mainWindow) mainWindow.webContents.send("nav", "terminal");
  return true;
});
ipcMain.handle("hashmm:forgetRecent", (_evt, url) => {
  const cfg = loadConfig();
  saveConfig({ recent: (cfg.recent || []).filter((r) => r.url !== url) });
  return true;
});

// ───────────────────────── 启动（单实例） ─────────────────────────

// V100: 卸载模式（--uninstall）跳过单实例锁——否则 app 正在运行时点卸载，
// 第二个实例会被锁挡掉直接退出，卸载器永远弹不出来。
const __uninstallMode = process.argv.includes("--uninstall");
const gotLock = __uninstallMode ? true : app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    // V103.90 双击 exe / 再次启动：把窗口显示到前台。关键修复——最小化到托盘是 hide()，
    // 此时 isVisible()=false 但 isMinimized()=false，原来只 focus() 不会显示隐藏窗口（看着像打不开）。
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
    } else {
      try { boot(); } catch (_e) { /* 窗口已销毁但进程还在 → 重建主窗口 */ }
    }
  });

  // 内置默认后端：打包前在 desktop/ 放 default-backend.json（{"url":"...","token":"..."}）。
  function defaultBackend() {
    try {
      const p = path.join(__dirname, "default-backend.json");
      if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, "utf-8"));
    } catch (_) { /* */ }
    return null;
  }
  ipcMain.handle("hashmm:getDefaultBackend", () => defaultBackend());

  // V100: MCP 工具层接入（对标 Marvis MCP Agent 层）。懒加载单例宿主，前端 Agent 通过
  // hashmm:mcp:listTools / hashmm:mcp:callTool 调到独立进程里的 MCP Server 工具。
  // try/catch 包裹：MCP 出问题绝不阻断 App 正常启动。
  try {
    require("./mcp").registerMcpIpc(ipcMain);
  } catch (e) { __logCrash("mcp-ipc", e); }

  // V100/V101: 存储服务 + 服务化引导。storage 实例在此创建（需 dialog/shell/installDir），
  // 其余服务注册与 IPC 全部收口到 services/bootstrap（main.js 更薄）。
  try {
    const { Storage, registerStorageIpc } = require("./storage");
    const installDir = path.dirname(app.getPath("exe")); // 安装目录 = 主 exe 所在目录
    const storage = new Storage({ installDir, dialog, shell, logger: __log() });
    storage.ensureDirs();
    try {
      const { getRegistry } = require("./services/registry");
      const { bootstrapServices } = require("./services/bootstrap");
      bootstrapServices({
        ipcMain, registry: getRegistry(), storage, registerStorageIpc, backendMgr,
        logger: __log(), installDir, userData: app.getPath("userData"),
        resourcesPath: process.resourcesPath, devSrcDir: path.join(__dirname, ".."),
        tmpDir: require("os").tmpdir(), isPackaged: app.isPackaged,
      });
    } catch (e) { __logCrash("services-init", e); }
  } catch (e) { __logCrash("storage-ipc", e); }

  // v1.4（Marvis 架构同款）：桌面端加载的就是后端的 Web UI 本体——preload 桥注入后，
  // 同一套 React 前端在桌面里自动长出「文件/终端/用量」入口（web 浏览器打开则没有）。
  // 一套代码，web 与桌面完全一致。连不上后端 → app.html 离线本机模式兜底。
  // ───────────── V81: 自动更新（MarvisUpdate 对应物） + 连接心跳 + 崩溃韧性 ─────────────
  // 更新源 = 当前连接的后端 /desktop-updates（generic provider 动态指向）。
  // 全程静默容错：没装 electron-updater / 后端没开更新服务 / 网络失败 → 不打扰用户。
  function setupAutoUpdate(backendUrl) {
    updateFeedReady = true;
    let autoUpdater;
    try { autoUpdater = require("electron-updater").autoUpdater; }
    catch (_) { return; }                                   // 依赖缺失：跳过
    try {
      // V101: feedURL/自动下载/退出装 的配置委托 UpdateService.configure（已单测）
      const { UpdateService } = require("./services/update-service");
      const svc = require("./services/registry").getRegistry().tryGet("update") || new UpdateService({ logger: __log() });
      svc.configure(autoUpdater, { feedUrl: backendUrl });
      autoUpdater.on("update-downloaded", (info) => {
        try {
          const { dialog } = require("electron");
          dialog.showMessageBox(mainWindow, {
            type: "info", buttons: ["立即重启更新", "稍后（退出时自动安装）"], defaultId: 1,
            title: "HashMM 更新", message: `新版本 ${info.version} 已下载`,
            detail: "现在重启立即生效；选稍后则在退出应用时自动安装。",
          }).then((r) => { if (r.response === 0) autoUpdater.quitAndInstall(); });
        } catch (_) { /* */ }
      });
      autoUpdater.on("error", () => { /* 静默：更新失败不影响使用 */ });
      setTimeout(() => { try { autoUpdater.checkForUpdates(); } catch (_) { /* */ } }, 8000);
      // 之后每 4 小时再查一次
      setInterval(() => { try { autoUpdater.checkForUpdates(); } catch (_) { /* */ } }, 4 * 3600 * 1000);
    } catch (_) { /* */ }
  }

  // 连接心跳：30s 探活。后端忙（解析大文件→心跳超时）不算掉线，只有连接被拒（真下线）
  // 连续 2 次才注入掉线条，恢复即移除（修"离线/解析时误报后端断开"）。
  let _hbTimer = null, _hbState = { downStreak: 0, busyStreak: 0 };
  function startHeartbeat() {
    if (_hbTimer) clearInterval(_hbTimer);
    _hbState = { downStreak: 0, busyStreak: 0 };
    const { nextHeartbeatState } = require("./services/health-util");
    _hbTimer = setInterval(async () => {
      if (!currentBackend || !mainWindow || mainWindow.isDestroyed()) return;
      const r = await checkHealth(currentBackend.url);
      const s = nextHeartbeatState(_hbState, r, { downThreshold: 2 });
      _hbState = { downStreak: s.downStreak, busyStreak: s.busyStreak };
      if (s.clearOffline) {
        mainWindow.webContents.executeJavaScript(
          '(function(){var b=document.getElementById("hashmm-offline-bar");if(b)b.remove();})();'
        ).catch(() => {});
      } else if (s.showOffline && s.downStreak === 2) {  // 仅真下线连续 2 次，注入一次
        mainWindow.webContents.executeJavaScript(
          '(function(){' +
          '  if (document.getElementById("hashmm-offline-bar")) return;' +
          '  var b = document.createElement("div");' +
          '  b.id = "hashmm-offline-bar";' +
          '  b.textContent = "后端连接中断，正在自动重连…（恢复后此条自动消失）";' +
          '  b.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483645;' +
          'padding:6px 12px;background:#FEF3C7;color:#92400E;font:500 12px sans-serif;' +
          'text-align:center;border-bottom:1px solid #FDE68A";' +
          '  document.documentElement.appendChild(b);' +
          '})();'
        ).catch(() => {});
      }
    }, 30000);
  }

  // V87: 本地壳层 —— 内置 webui 静态托管 + /api 同源反代。
  // 有它：前端版本随安装包走（根治"服务器没重新 build → 桌面看不到新功能/截屏按钮"），
  //       且远程/本地后端用同一套 UI；没它（开发态没 build 前端）→ 回退旧的远程直载。
  async function ensureShell() {
    if (shellSrv) return shellSrv;
    const dir = webuiDir();
    if (!fs.existsSync(path.join(dir, "index.html"))) {
      console.warn("[shell] 未找到内置 webui（" + dir + "），使用远程直载模式");
      return null;
    }
    try {
      // V93: 游客可浏览架构 —— 窗口不再随登录态变形（V91 小窗联动撤销），
      // 登录改为 Web 层的 Marvis 式居中遮罩弹窗。
      const preferred = loadConfig().shellPort || 17615;   // V94: 固定端口=稳定 origin=登录持久
      shellSrv = await createShellServer({ webuiDir: dir, port: preferred, log: (s) => console.log(s) });
      if (shellSrv && shellSrv.port !== preferred) saveConfig({ shellPort: shellSrv.port });
      else if (!loadConfig().shellPort) saveConfig({ shellPort: preferred });
      semServe.attach();   // V98: 按持久化开关注册 /local/embed provider
    } catch (e) {
      console.error("[shell] 启动失败：", e.message);
      shellSrv = null;
    }
    return shellSrv;
  }

  function loadRemote(url, token) {
    currentBackend = { url, token: token || "" };
    if (shellSrv) {
      shellSrv.setTarget(url, token || "");      // 数据面切到目标后端（远程或本地 sidecar）
      mainWindow.loadURL(shellSrv.origin + "/"); // UI 永远从内置壳层来
    } else {
      // 旧模式兜底：直载远程 UI（前端版本由服务器决定）
      if (token) {
        const filter = { urls: [`${url}/*`] };
        session.defaultSession.webRequest.onBeforeSendHeaders(filter, (details, cb) => {
          details.requestHeaders["X-HashMM-Session-Token"] = token;
          cb({ requestHeaders: details.requestHeaders });
        });
      }
      mainWindow.loadURL(url + "/");
    }
    startHeartbeat();          // V81: 连接健康心跳
    setupAutoUpdate(url);      // V81: 更新源跟随当前后端
  }

  async function boot() {
    detectInstallType();          // V97: 区分全新安装 / 更新 / 正常启动
    createShellWindow();
    buildMenu();
    loadRemoteRef = loadRemote;          // V93: 托盘菜单需要触达闭包内的 loadRemote
    createTray();
    try { globalShortcut.register("Alt+H", toggleMainWindow); } catch (_) { /* 被占用则跳过 */ }
    await ensureShell();                       // V87: 先把本地壳层拉起来（UI 资产内置）
    const cfg = loadConfig();
    const dft = defaultBackend();
    const candidates = [];
    if (cfg && cfg.url) candidates.push({ url: cfg.url, token: cfg.token || "" });
    if (dft && dft.url && (!cfg || cfg.url !== dft.url))
      candidates.push({ url: normalizeUrl(dft.url), token: dft.token || "" });
    for (const c of candidates) {
      const r = await checkHealth(c.url);
      if (r.ok) { rememberBackend(normalizeUrl(c.url), c.token); loadRemote(normalizeUrl(c.url), c.token); return; }
    }
    // V87: 远程都不可达 → 若用户开了"自动启动本地后端"且环境已初始化，本机直接顶上
    if (cfg.localAuto && backendMgr.envReady(backendHome(), { rtDir: runtimeDir() })) {
      const r = await backendMgr.start({
        home: backendHome(), srcDir: backendSrcDir(), rtDir: runtimeDir(),
        port: cfg.localPort || LOCAL_BACKEND_PORT,
      });
      if (r.ok) { loadRemote(r.url, ""); return; }
    }
    mainWindow.loadFile(path.join(__dirname, "app.html"));   // 连接 / 本地能力页
  }
  global.__loadRemote = loadRemote;

  // 全窗口统一治理：弹窗也注入隐形拖拽条（与主界面一致的无边框形态）
  app.on("web-contents-created", (_e, contents) => {
    if (contents.getType() === "webview") {
      contents.setWindowOpenHandler(({ url }) => {
        if (/^https?:\/\//i.test(url)) shell.openExternal(url);
        return { action: "deny" };
      });
    }
    contents.on("did-finish-load", () => {
      try {
        contents.executeJavaScript(
          '(function(){' +
          '  if (document.getElementById("hashmm-shell-drag")) return;' +
          '  var d = document.createElement("div");' +
          '  d.id = "hashmm-shell-drag";' +
          '  d.style.cssText = "position:fixed;top:0;left:0;right:146px;height:12px;' +
          'z-index:2147483646;-webkit-app-region:drag;background:transparent";' +
          '  document.documentElement.appendChild(d);' +
          '})();'
        ).catch(() => {});
      } catch (_) { /* */ }
    });
  });

app.whenReady().then(() => {
  if (__earlyCrash) { __showCrashDialog(__earlyCrash); return; }  // V99: 顶层 require 阶段崩溃，GUI 就绪后补弹中文框
  // V100: 全自绘卸载器 —— HashMM.exe --uninstall（控制面板/开始菜单卸载入口）触发，
  // 弹无边框自绘卸载窗口（installer/uninstall.html，与安装器同款风格），不进 App。
  try {
    const uninstaller = require("./installer/uninstaller");
    if (uninstaller.shouldRunUninstaller()) {
      uninstaller.registerIpc();
      uninstaller.createUninstallWindow();
      return;
    }
  } catch (e) { __logCrash("uninstaller-mode", e); /* 卸载器异常不应阻断正常启动 */ }
  // V100: 全自绘安装器 —— 若作为"未安装的 portable 实例"启动，则不进 App，
  // 而是弹无边框自绘安装窗口（installer/ui.html）。已安装副本正常进 App。
  try {
    const installer = require("./installer/installer");
    if (installer.shouldRunInstaller()) {
      installer.registerIpc();
      installer.createInstallerWindow();
      return;
    }
  } catch (e) { __logCrash("installer-mode", e); /* 安装器异常不应阻断正常启动 */ }
  boot();
  try { _startFileReqPoller(); } catch (_e) { /* 文件投送轮询失败不影响主程序 */ }
});
  app.on("before-quit", () => {
    isQuitting = true;                   // V93: 真退出，不被"最小化到托盘"拦截
    try { globalShortcut.unregisterAll(); } catch (_) { /* */ }
    if (tray) { try { tray.destroy(); } catch (_) { /* */ } tray = null; }
    for (const [, p] of terminals) { try { p.kill(); } catch (_) { /* */ } }
    terminals.clear();
    for (const [, w] of watchers) { try { w.close(); } catch (_) { /* */ } }
    watchers.clear();
    try { backendMgr.stop(); } catch (_) { /* V87: 本地后端随应用退出 */ }
    if (shellSrv) { try { shellSrv.close(); } catch (_) { /* */ } shellSrv = null; }
    // V103.90 兜底：清理后若进程因某个未关闭的 handle 卡住没退出，1.5s 后强制退出，
    // 避免出现"已退出但后台仍残留 HashMM 进程、双击打不开"的情况。
    setTimeout(() => { try { app.exit(0); } catch (_) { try { process.exit(0); } catch (_e) { /* */ } } }, 1500);
  });
  app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) boot(); });
}
