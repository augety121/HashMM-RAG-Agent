/**
 * desktop/installer/installer.js — 全自绘安装器主进程（V100）。
 *
 * 创建一个**无边框 Electron 窗口**加载 ui.html（纯 HTML/CSS 自绘界面，无 NSIS 壳），
 * 通过 IPC 驱动真实安装：选目录 → 拷贝 app 文件 → 建快捷方式 → 写卸载信息 →
 * 落安装标记 → 启动已安装副本。纯逻辑在 install-engine.js（已单测），这里只做 IO。
 *
 * 触发方式（见 main.js startup 钩子）：当本进程作为"未安装的 portable 实例"运行时
 * （electron-builder portable 目标会注入 PORTABLE_EXECUTABLE_FILE 环境变量，且安装
 * 标记不存在），主进程不进 App，而是 createInstallerWindow()。已安装副本正常进 App。
 *
 * 注：本文件 require('electron')，沙箱不实跑；node --check 静态通过即可。真机由
 * Electron 加载。所有"决策"已在 install-engine 单测覆盖。
 */
"use strict";
const { BrowserWindow, ipcMain, dialog, shell, app } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawn } = require("child_process");
const E = require("./install-engine");

// ★ original-fs：Electron 默认的 fs 被打了 asar 补丁——会把 app.asar 当"目录"遍历，
// 导致拷贝时 mkdir 'resources\app.asar' 撞 EEXIST/EISDIR（用户实测报错）。original-fs
// 是未打补丁的原生 fs，把 app.asar 当**普通文件**正常拷。沙箱无 electron 时回退 fs。
let rfs;
try { rfs = require("original-fs"); } catch (_) { rfs = require("fs"); }

let win = null;

/** 判断是否应进入安装流程：portable 实例 且 目标位置尚无安装标记。 */
function shouldRunInstaller() {
  // ① 已装副本：运行的 exe 自己目录里有安装标记 → 绝不当安装器跑。
  //    （关键修复：shell/spawn 启动已装副本时可能继承到 portable 的 PORTABLE_EXECUTABLE_FILE，
  //     仅凭该环境变量会误判。以"自己目录有无标记"为准最可靠。）
  try {
    if (fs.existsSync(E.markerPath(path.dirname(process.execPath)))) return false;
  } catch (_) { /* */ }
  // ② 非 portable（直接运行的已装 exe 或 NSIS 装好的）→ 不弹安装器。
  const portableFile = process.env.PORTABLE_EXECUTABLE_FILE;
  if (!portableFile) return false;
  // ③ portable 且非已装副本 → 显示安装器窗口（UI 会据"上次安装记录"显示全新安装 or 已安装态）。
  return true;
}

function createInstallerWindow() {
  win = new BrowserWindow({
    width: 480, height: 480,
    frame: false,            // 无边框 —— 全自绘
    transparent: true,       // 透明 —— 配合 HTML 圆角卡片做"浮窗"观感
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    hasShadow: true,
    backgroundColor: "#00000000",
    title: "HashMM 安装",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.removeMenu();
  win.loadFile(path.join(__dirname, "ui.html"));
  return win;
}

// ── IPC：窗口控制（自绘标题栏的最小化/关闭按钮） ──
function registerIpc() {
  ipcMain.on("hm-inst:minimize", () => { if (win) win.minimize(); });
  ipcMain.on("hm-inst:close", () => { if (win) win.close(); });

  // 默认安装目录 + 可用空间 + 已安装检测，喂给 UI 初始化
  ipcMain.handle("hm-inst:defaults", () => {
    const def = E.defaultInstallDir(process.env.LOCALAPPDATA || app.getPath("appData"));
    let freeText = "";
    try { freeText = E.humanSize(os.freemem()); } catch (_) { /* */ }
    // 读上次安装记录：若记录的安装目录里仍有标记 → 检测到"已安装"，UI 显示对应态并预填路径
    let existing = null;
    try {
      const rec = JSON.parse(rfs.readFileSync(E.lastInstallRecordPath(app.getPath("userData")), "utf8"));
      if (rec && rec.installDir && fs.existsSync(E.markerPath(rec.installDir))) {
        existing = {
          installDir: rec.installDir, version: rec.version || "",
          exe: path.join(rec.installDir, rec.exeName || "HashMM.exe"),
        };
      }
    } catch (_) { /* 无记录=全新安装 */ }
    return { installDir: existing ? existing.installDir : def, sizeText: "约 540 MB", freeText, existing };
  });

  // 浏览选目录（系统对话框），返回归一化后的安装目录
  ipcMain.handle("hm-inst:browse", async (_e, current) => {
    const r = await dialog.showOpenDialog(win, {
      title: "选择安装位置", properties: ["openDirectory", "createDirectory"],
      defaultPath: current || undefined,
    });
    if (r.canceled || !r.filePaths[0]) return null;
    return E.normalizeInstallDir(r.filePaths[0]);
  });

  // 执行安装（流式回报进度），返回实际已装 exe 路径供 UI 启动
  ipcMain.handle("hm-inst:install", async (_e, rawDir) => {
    const installDir = E.normalizeInstallDir(rawDir);
    const v = E.validateInstallDir(installDir);
    if (!v.ok) return { ok: false, error: v.reason };
    try {
      const installedExe = await doInstall(installDir);
      return { ok: true, installDir, installedExe };
    } catch (err) {
      return { ok: false, error: (err && err.message) || String(err) };
    }
  });

  // 查看《用户协议与隐私政策》——打开独立可读窗口
  ipcMain.handle("hm-inst:eula", () => { openEulaWindow(); return { ok: true }; });

  // 启动已安装副本并退出安装器
  ipcMain.on("hm-inst:launch", (_e, exePath) => {
    try {
      // 关键修复：用清掉 PORTABLE_* 的环境 spawn，detached——否则已装副本继承到
      // PORTABLE_EXECUTABLE_FILE 会以为自己是 portable 又弹安装器（用户报的"点开始使用又出安装界面"）。
      const env = Object.assign({}, process.env);
      delete env.PORTABLE_EXECUTABLE_FILE;
      delete env.PORTABLE_EXECUTABLE_DIR;
      delete env.PORTABLE_EXECUTABLE_APP_FILENAME;
      const child = spawn(exePath, [], { detached: true, stdio: "ignore", env, cwd: path.dirname(exePath) });
      child.unref();
    } catch (_) { try { shell.openPath(exePath); } catch (__) { /* */ } }
    setTimeout(() => app.quit(), 300);
  });
}

// 《用户协议与隐私政策》窗口（读 build/hm-eula.txt，渲染成可滚动只读页）
let eulaWin = null;
function openEulaWindow() {
  if (eulaWin && !eulaWin.isDestroyed()) { eulaWin.focus(); return; }
  eulaWin = new BrowserWindow({
    width: 560, height: 600, title: "用户协议与隐私政策",
    parent: win || undefined, modal: false, resizable: true, minimizable: false, maximizable: false,
    backgroundColor: "#ffffff",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  eulaWin.removeMenu();
  eulaWin.loadFile(path.join(__dirname, "eula.html"));
  eulaWin.on("closed", () => { eulaWin = null; });
}

function emit(stage, pct) {
  if (win && !win.isDestroyed()) win.webContents.send("hm-inst:progress", { stage, pct });
}

/** 递归拷贝：用引擎的 copyTree，传入 original-fs（app.asar 当文件不当目录）。 */
function copyTree(from, to, exclude) {
  return E.copyTree(rfs, from, to, exclude, path);
}

function runPowerShell(cmd) {
  return new Promise((resolve) => {
    const p = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", cmd], { windowsHide: true });
    p.on("close", () => resolve()); p.on("error", () => resolve());
  });
}

async function doInstall(installDir) {
  // 源：portable 解压出的 app 根（PORTABLE_EXECUTABLE_DIR 的父级即 app 资源）。
  const sourceExe = process.env.PORTABLE_EXECUTABLE_FILE || process.execPath;
  const sourceDir = path.dirname(process.execPath); // 解压后的 app 目录
  const plan = E.copyPlan(sourceDir, installDir);

  emit("正在准备文件", 8);
  // 清掉上次失败可能残留的 resources（含被旧版误建成"目录"的 app.asar），
  // 保证可重复安装、不再 EEXIST。只动我们自己的 resources 子目录，不碰用户其他文件。
  try { rfs.rmSync(path.join(installDir, "resources"), { recursive: true, force: true }); } catch (_) { /* */ }
  rfs.mkdirSync(installDir, { recursive: true });

  emit("正在解压程序文件", 25);
  copyTree(plan.from, plan.to, plan.exclude);
  emit("正在解压程序文件", 70);

  // 修复：已装 exe 名取自 process.execPath（解压出的真 app 二进制 HashMM.exe），
  // 不从 portable 启动器名推导（那会得到错误的 HashMM-安装-x.exe）。
  const exeName = E.installedExeName(process.execPath);
  const installedExe = path.join(installDir, exeName);
  const iconPath = installedExe;

  emit("正在创建快捷方式", 80);
  const startDir = path.join(process.env.APPDATA || os.homedir(), "Microsoft", "Windows", "Start Menu", "Programs");
  const startMenu = path.join(startDir, E.PRODUCT + ".lnk");
  const desktop = path.join(os.homedir(), "Desktop", E.PRODUCT + ".lnk");
  for (const lnk of [startMenu, desktop]) {
    await runPowerShell(E.shortcutPsCommand({
      lnkPath: lnk, targetPath: installedExe, workingDir: installDir,
      iconPath, description: E.PRODUCT,
    }));
  }
  // 卸载快捷方式（用户要的"可见卸载程序"）：开始菜单里一个"卸载 HashMM.lnk" → HashMM.exe --uninstall
  await runPowerShell(E.shortcutPsCommand({
    lnkPath: path.join(startDir, "卸载 " + E.PRODUCT + ".lnk"),
    targetPath: installedExe, workingDir: installDir, iconPath,
    description: "卸载 " + E.PRODUCT, arguments: "--uninstall",
  }));

  emit("正在写入卸载信息", 90);
  const reg = E.uninstallRegistry({
    installDir, version: app.getVersion(), uninstallExe: installedExe, iconPath,
  });
  const regCmds = Object.entries(reg.values).map(([k, val]) => {
    const type = typeof val === "number" ? "DWord" : "String";
    return `New-ItemProperty -Path 'HKCU:\\${reg.key}' -Name '${k}' -PropertyType ${type} -Value '${val}' -Force | Out-Null`;
  });
  await runPowerShell(`New-Item -Path 'HKCU:\\${reg.key}' -Force | Out-Null; ` + regCmds.join("; "));

  emit("即将完成", 96);
  rfs.writeFileSync(E.markerPath(installDir), JSON.stringify(E.markerContent(app.getVersion(), sourceExe), null, 2));
  // 写上次安装记录到 userData，供再次运行安装器时检测"已安装"（修"重装让我重选位置"）
  try {
    rfs.writeFileSync(E.lastInstallRecordPath(app.getPath("userData")),
      JSON.stringify(E.lastInstallRecord(installDir, app.getVersion(), exeName), null, 2));
  } catch (_) { /* */ }
  emit("完成", 100);

  return installedExe;
}

module.exports = { shouldRunInstaller, createInstallerWindow, registerIpc, copyTree };
