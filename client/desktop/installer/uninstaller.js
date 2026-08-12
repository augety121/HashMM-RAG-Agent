/**
 * desktop/installer/uninstaller.js — 全自绘卸载器主进程（V100）。
 *
 * 与安装器同款无边框自绘窗口（uninstall.html），由 `HashMM.exe --uninstall` 触发
 * （portable 安装时写入注册表 UninstallString，控制面板/开始菜单卸载即走这里）。
 * 卸载执行：HashMM.exe 删不掉正在运行的自身 → 写一个自删 .bat（install-engine
 * 的 selfDeleteScript：等本进程退出后删注册表/快捷方式/整个安装目录/自身），detached
 * 启动它，然后本进程退出。用户数据在 %APPDATA%，不在安装目录，默认保留。
 *
 * 沙箱不实跑（require electron）；node --check 静态通过即可。
 */
"use strict";
const { BrowserWindow, ipcMain, app, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawn } = require("child_process");
const E = require("./install-engine");

let win = null;

/** 是否卸载模式：命令行带 --uninstall。 */
function shouldRunUninstaller() {
  return process.argv.includes("--uninstall");
}

function createUninstallWindow() {
  win = new BrowserWindow({
    width: 480, height: 420,
    frame: false, transparent: true, resizable: false,
    maximizable: false, fullscreenable: false, hasShadow: true,
    backgroundColor: "#00000000", title: "卸载 HashMM",
    webPreferences: {
      preload: path.join(__dirname, "preload-uninstall.js"),
      contextIsolation: true, nodeIntegration: false,
    },
  });
  win.removeMenu();
  win.loadFile(path.join(__dirname, "uninstall.html"));
  return win;
}

function registerIpc() {
  ipcMain.on("hm-uninst:minimize", () => { if (win) win.minimize(); });
  ipcMain.on("hm-uninst:close", () => { if (win) win.close(); });

  ipcMain.handle("hm-uninst:info", () => {
    // 安装目录 = 当前 exe 所在目录（portable 装好后 exe 就在安装目录里）
    const installDir = path.dirname(process.execPath);
    return { installDir };
  });

  // 执行卸载：写自删脚本 → detached 启动 → 回报"进行中" → 退出本进程
  ipcMain.handle("hm-uninst:run", async () => {
    try {
      const installDir = path.dirname(process.execPath);
      const t = E.uninstallTargets({
        installDir,
        appData: process.env.APPDATA || app.getPath("appData"),
        homeDir: os.homedir(),
      });
      const bat = E.selfDeleteScript({
        installDir: t.installDir, registryKey: t.registryKey,
        shortcuts: t.shortcuts, exeName: path.basename(process.execPath),
        // 用户数据夹（默认在安装目录内）卸载时原地保留：工作区 + 后端解析数据
          preserveDirs: ["HashMM Files", "HashMM Data", "local-backend"],
      });
      const batPath = path.join(os.tmpdir(), "hashmm-uninstall-" + Date.now() + ".bat");
      fs.writeFileSync(batPath, bat, "utf8");
      // detached 启动自删脚本（它会等本进程退出后再删目录/自身）
      const child = spawn("cmd.exe", ["/c", batPath], {
        detached: true, stdio: "ignore", windowsHide: true,
      });
      child.unref();
      return { ok: true };
    } catch (err) {
      return { ok: false, error: (err && err.message) || String(err) };
    }
  });

  // 完成 → 退出（自删脚本随即删掉安装目录）
  ipcMain.on("hm-uninst:done", () => { setTimeout(() => app.quit(), 200); });
}

module.exports = { shouldRunUninstaller, createUninstallWindow, registerIpc };
