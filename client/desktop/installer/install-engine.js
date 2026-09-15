/**
 * desktop/installer/install-engine.js — 全自绘安装器的"引擎"纯逻辑（V100）。
 *
 * 背景：用户要"像微信那样的全自绘原生安装界面"，不要 NSIS 向导壳。微信 4.x 是
 * 独立 Qt 程序；HashMM 本就是 Electron，于是用**无边框 Electron 窗口 + 纯 HTML/CSS**
 * 自绘安装界面（见 installer/ui.html），由 Electron 主进程驱动（installer.js）。
 *
 * 本文件只放**不依赖 electron/不碰真实磁盘**的纯函数：安装目录归一化与校验、
 * 拷贝计划、快捷方式的 PowerShell 命令拼装、卸载注册表项、安装标记路径。
 * 这样这些关键决策可在沙箱/CI 用 node 单测覆盖，主进程只负责真正执行 IO。
 */
"use strict";
const path = require("path");

const PRODUCT = "HashMM";
const MARKER = ".hashmm-install.json"; // 安装完成标记（判断是否已安装）

/** 把用户选的基目录归一为安装目录：确保以产品名结尾（C:\X → C:\X\HashMM）。 */
function normalizeInstallDir(base, product = PRODUCT) {
  const b = String(base || "").trim().replace(/[\\/]+$/, "");
  if (!b) return "";
  const last = b.split(/[\\/]/).pop();
  if (last && last.toLowerCase() === product.toLowerCase()) return b;
  // 用反斜杠拼（Windows 安装器目标）。path.win32 保证分隔符一致。
  return path.win32.join(b, product);
}

/** 默认安装目录（每用户，免管理员）：%LOCALAPPDATA%\Programs\HashMM。 */
function defaultInstallDir(localAppData, product = PRODUCT) {
  const root = String(localAppData || "").replace(/[\\/]+$/, "");
  return path.win32.join(root, "Programs", product);
}

const _SYSTEM_DIRS = [
  "c:\\windows", "c:\\program files", "c:\\program files (x86)",
  "c:\\", "c:\\users", "c:\\programdata",
];

/**
 * 校验安装目录是否安全可写。拒绝：空、盘符根、系统关键目录、过短。
 * @returns {{ok:boolean, reason?:string}}
 */
function validateInstallDir(dir) {
  const d = String(dir || "").trim();
  if (!d) return { ok: false, reason: "安装路径为空" };
  if (!/^[a-zA-Z]:[\\/]/.test(d)) return { ok: false, reason: "请使用带盘符的绝对路径（如 C:\\…）" };
  const low = d.toLowerCase().replace(/[\\/]+$/, "");
  if (/^[a-z]:$/.test(low)) return { ok: false, reason: "不能直接装在盘符根目录" };
  for (const sys of _SYSTEM_DIRS) {
    if (low === sys) return { ok: false, reason: "不能装在系统目录" };
  }
  // 不允许装进 Windows 目录树（防误操作毁系统）
  if (low.startsWith("c:\\windows\\")) return { ok: false, reason: "不能装在 Windows 目录内" };
  return { ok: true };
}

/** 安装标记文件的完整路径。 */
function markerPath(installDir) {
  return path.win32.join(String(installDir || ""), MARKER);
}

/** 安装标记内容（含版本/时间/源），写到 markerPath。 */
function markerContent(version, sourceExe) {
  return {
    product: PRODUCT,
    version: String(version || ""),
    installedAt: new Date().toISOString(),
    source: String(sourceExe || ""),
  };
}

/**
 * 生成创建快捷方式的 PowerShell 命令（WScript.Shell COM）。
 * 真机由主进程 spawn('powershell', ['-NoProfile','-Command', cmd]) 执行。
 * 路径里的单引号转义为两个单引号（PowerShell 单引号字符串规则）。
 */
function shortcutPsCommand({ lnkPath, targetPath, workingDir, iconPath, description, arguments: args }) {
  const q = (s) => "'" + String(s || "").replace(/'/g, "''") + "'";
  const lines = [
    "$ws = New-Object -ComObject WScript.Shell",
    "$s = $ws.CreateShortcut(" + q(lnkPath) + ")",
    "$s.TargetPath = " + q(targetPath),
    "$s.WorkingDirectory = " + q(workingDir || path.win32.dirname(targetPath)),
  ];
  if (args) lines.push("$s.Arguments = " + q(args));   // 卸载快捷方式用：--uninstall
  if (iconPath) lines.push("$s.IconLocation = " + q(iconPath));
  if (description) lines.push("$s.Description = " + q(description));
  lines.push("$s.Save()");
  return lines.join("; ");
}

/**
 * 已安装 exe 的文件名。portable 运行时 process.execPath 是解压出的真 app 二进制（HashMM.exe），
 * 取其 basename 即正确的已装 exe 名——**不要从 portable 启动器名 HashMM-安装-x.exe 推导**（会错）。
 */
function installedExeName(execPath) {
  const base = path.win32.basename(String(execPath || ""));
  return /\.exe$/i.test(base) ? base : "HashMM.exe";
}

/** 上次安装记录文件路径（写在 userData，portable 与已装副本共享同一 userData，故可跨次读取）。 */
function lastInstallRecordPath(userDataDir) {
  return path.win32.join(String(userDataDir || ""), ".hashmm-last-install.json");
}

/** 上次安装记录内容（记下装到哪、版本、exe 名，供再次运行安装器时检测"已安装"）。 */
function lastInstallRecord(installDir, version, exeName) {
  return {
    installDir: String(installDir || ""),
    version: String(version || ""),
    exeName: exeName || "HashMM.exe",
    at: new Date().toISOString(),
  };
}

/**
 * Add/Remove Programs（控制面板卸载列表）注册表项。
 * 每用户写 HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\HashMM。
 * @returns {{root:string, key:string, values:object}}
 */
function uninstallRegistry({ installDir, version, uninstallExe, iconPath }) {
  return {
    root: "HKCU",
    key: "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + PRODUCT,
    values: {
      DisplayName: PRODUCT,
      DisplayVersion: String(version || ""),
      DisplayIcon: String(iconPath || ""),
      Publisher: PRODUCT,
      InstallLocation: String(installDir || ""),
      UninstallString: '"' + String(uninstallExe || "") + '" --uninstall',
      NoModify: 1,
      NoRepair: 1,
      EstimatedSize: 553000, // KB，约 540MB（控制面板"大小"列展示用）
    },
  };
}

/**
 * 拷贝计划：把源（portable 解压出的 app 根）拷到安装目录。
 * 返回要跳过的项（运行期/无关文件）与目标目录，真正递归拷由主进程做。
 */
function copyPlan(sourceDir, installDir) {
  return {
    from: String(sourceDir || ""),
    to: String(installDir || ""),
    // 这些目录/文件不进安装目录（运行期数据、缓存、安装器自身临时物）
    exclude: ["data", "logs", ".cache", MARKER],
    createDirs: [installDir],
  };
}

/** 估算可读的空间字符串（字节 → MB/GB），给 UI 显示。 */
function humanSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(1) + " GB";
  if (n >= 1024 * 1024) return Math.round(n / (1024 * 1024)) + " MB";
  if (n >= 1024) return Math.round(n / 1024) + " KB";
  return n + " B";
}

/**
 * 递归拷贝目录树。**fs 实现作参数**：真机由 installer.js 传入 `original-fs`（关键——
 * Electron 默认 fs 把 app.asar 当目录会炸；original-fs 当文件正常拷），沙箱测试传
 * node 的 `fs`。幂等：mkdir recursive 容已存在，copyFileSync 默认覆盖 → 可重复安装。
 * @param fsImpl 提供 mkdirSync/readdirSync/statSync/copyFileSync 的对象
 * @param pathImpl 提供 join 的对象（默认 node path）
 */
function copyTree(fsImpl, from, to, exclude, pathImpl) {
  const P = pathImpl || require("path");
  const ex = exclude || [];
  fsImpl.mkdirSync(to, { recursive: true });
  for (const name of fsImpl.readdirSync(from)) {
    if (ex.includes(name)) continue;
    const s = P.join(from, name), d = P.join(to, name);
    if (fsImpl.statSync(s).isDirectory()) copyTree(fsImpl, s, d, [], P);
    else fsImpl.copyFileSync(s, d);
  }
}

/**
 * 卸载要清理的东西（与安装写入的对称）：安装目录、开始菜单/桌面快捷方式、卸载注册表键。
 * 注：用户数据在 %APPDATA%（userData），**不在安装目录**，故删整个安装目录是安全的，
 * 用户的知识库/对话/登录默认保留（"卸载保留数据"承诺）。
 */
function uninstallTargets({ installDir, appData, homeDir }) {
  const reg = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + PRODUCT;
  const startMenu = path.win32.join(appData || "", "Microsoft", "Windows", "Start Menu", "Programs", PRODUCT + ".lnk");
  const desktop = path.win32.join(homeDir || "", "Desktop", PRODUCT + ".lnk");
  return { installDir: String(installDir || ""), registryKey: reg, shortcuts: [startMenu, desktop] };
}

/**
 * 自删批处理：HashMM.exe 自己删不掉正在运行的自身，故写一个 .bat 等进程退出后再删。
 * 顺序：等 HashMM.exe 退出 → 删注册表键 → 删快捷方式 → 删安装目录 → 删自身。
 * preserveDir：要保留的**安装目录内子目录名**（如 "HashMM Files" 用户数据夹）。给了则
 *   不整目录 rd，而是逐项删除安装目录下除该子目录外的所有项（保数据在原地，"卸载保留数据"）。
 * 全部 best-effort（>nul 2>&1），失败也不阻塞。返回 .bat 文本。
 */
function selfDeleteScript({ installDir, registryKey, shortcuts, exeName, preserveDir, preserveDirs }) {
  const exe = exeName || "HashMM.exe";
  // 支持单个(preserveDir 向后兼容)或多个(preserveDirs)保留数据夹
  let keep = [];
  if (Array.isArray(preserveDirs)) keep = preserveDirs.filter(Boolean);
  else if (preserveDir) keep = [preserveDir];
  const lines = [
    "@echo off",
    "chcp 65001 >nul",
    ":wait",
    `tasklist /fi "imagename eq ${exe}" 2>nul | find /i "${exe}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)`,
    `reg delete "${registryKey}" /f >nul 2>&1`,
  ];
  for (const lnk of shortcuts || []) lines.push(`del /f /q "${lnk}" >nul 2>&1`);
  if (keep.length) {
    // 保留数据夹：删安装目录下除这些夹外的所有子目录 + 根文件（链式 if 跳过每个保留夹）
    const cond = keep.map((d) => `if /i not "%%~nxD"=="${d}" `).join("");
    lines.push(`for /d %%D in ("${installDir}\\*") do ${cond}rd /s /q "%%D" >nul 2>&1`);
    lines.push(`del /f /q "${installDir}\\*.*" >nul 2>&1`);
  } else {
    lines.push(`rd /s /q "${installDir}" >nul 2>&1`);
  }
  lines.push(`del /f /q "%~f0" >nul 2>&1`);
  return lines.join("\r\n") + "\r\n";
}

module.exports = {
  PRODUCT, MARKER,
  normalizeInstallDir, defaultInstallDir, validateInstallDir,
  markerPath, markerContent,
  shortcutPsCommand, uninstallRegistry, copyPlan, humanSize, copyTree,
  uninstallTargets, selfDeleteScript,
  installedExeName, lastInstallRecordPath, lastInstallRecord,
};
