/**
 * desktop/storage/workspace.js — 用户数据/工作区路径管理（V100，纯逻辑可测）。
 *
 * 模型对标微信：默认把用户文件/下载/笔记放进**安装目录下的「HashMM Files」**夹
 * （软件目录内，绿色/便携式风格），用户也可在软件里改成别的位置（持久化到配置）。
 *   <installDir>\HashMM Files\
 *       Downloads\   下载
 *       Documents\   用户保存的文件
 *       Notes\       md 笔记
 * 卸载时这个数据夹默认保留（见 install-engine.selfDeleteScript 的 preserveDir）。
 *
 * 路径解析/文件名净化/防覆盖去重都是纯函数，可单测；真正建目录/写文件在 index.js。
 */
"use strict";
const path = require("path");

const PRODUCT = "HashMM";
const DATA_FOLDER = "HashMM Files";        // 用户可见的数据夹名（微信式）
const CONFIG_FILE = ".hashmm-storage.json"; // 记录用户自定义数据根

const SUBDIRS = { downloads: "Downloads", documents: "Documents", notes: "Notes" };

/** 默认数据根 = 安装目录下的「HashMM Files」。 */
function defaultDataRoot(installDir, pathImpl) {
  // V312 修真实缺陷：此前硬编码 path.win32 —— Linux/macOS 上拼出
  // "\\tmp\\x\\HashMM Files" 这种【相对】反斜杠路径，mkdir 全落在进程 CWD 下
  // （本仓库根曾被测试写出 90 个 `\tmp\hminst-*` 字面量垃圾，就是这么来的；
  // 真实用户在 Linux 桌面端上会把数据写进启动目录而不是安装目录）。
  const P = pathImpl || path;
  return P.join(String(installDir || ""), DATA_FOLDER);
}

/** 生效数据根：用户配了自定义路径就用它，否则用默认（安装目录内）。 */
function resolveDataRoot({ installDir, configuredPath, pathImpl }) {
  const c = configuredPath && String(configuredPath).trim();
  if (c) return c.replace(/[\\/]+$/, "");
  return defaultDataRoot(installDir, pathImpl);
}

function subDir(dataRoot, key, pathImpl) {
  const P = pathImpl || path;
  return P.join(String(dataRoot || ""), SUBDIRS[key] || key);
}
function downloadsDir(dataRoot, pathImpl) { return subDir(dataRoot, "downloads", pathImpl); }
function documentsDir(dataRoot, pathImpl) { return subDir(dataRoot, "documents", pathImpl); }
function notesDir(dataRoot, pathImpl) { return subDir(dataRoot, "notes", pathImpl); }

/** 所有应被创建的目录（数据根 + 三个子目录）。 */
function allDirs(dataRoot, pathImpl) {
  return [dataRoot, downloadsDir(dataRoot, pathImpl),
          documentsDir(dataRoot, pathImpl), notesDir(dataRoot, pathImpl)];
}

function configPath(installDir, pathImpl) {
  const P = pathImpl || path;
  return P.join(String(installDir || ""), CONFIG_FILE);
}

const _WIN_RESERVED = new Set([
  "con", "prn", "aux", "nul",
  ...Array.from({ length: 9 }, (_, i) => "com" + (i + 1)),
  ...Array.from({ length: 9 }, (_, i) => "lpt" + (i + 1)),
]);

/**
 * 净化文件名：去掉 Windows 非法字符 \ / : * ? " < > | 与控制字符；去首尾空格/点；
 * 处理保留名（CON/PRN…→ 加下划线前缀）；空 → "untitled"；过长截断到 200。
 */
function sanitizeFilename(name, fallback = "untitled") {
  let s = String(name == null ? "" : name);
  s = s.replace(/[\\/:*?"<>|]/g, "_").replace(/[\x00-\x1f]/g, "");
  s = s.replace(/^[\s.]+|[\s.]+$/g, ""); // 去首尾空格与点
  if (!s) return fallback;
  const dot = s.lastIndexOf(".");
  const base = dot > 0 ? s.slice(0, dot) : s;
  const ext = dot > 0 ? s.slice(dot) : "";
  if (_WIN_RESERVED.has(base.toLowerCase())) s = "_" + s;
  if (s.length > 200) s = s.slice(0, 200 - ext.length) + ext;
  return s;
}

/**
 * 防覆盖去重：dir 下若已存在 name，则 name → "name (1).ext" → "name (2).ext"…
 * existsFn(fullPath)->bool 由调用方注入（真机 fs.existsSync，测试可造）。
 */
function uniqueFilePath(dir, name, existsFn, pathImpl) {
  const P = pathImpl || path.win32;
  const safe = sanitizeFilename(name);
  const dot = safe.lastIndexOf(".");
  const base = dot > 0 ? safe.slice(0, dot) : safe;
  const ext = dot > 0 ? safe.slice(dot) : "";
  let candidate = safe;
  let i = 0;
  while (existsFn(P.join(dir, candidate))) {
    i += 1;
    candidate = `${base} (${i})${ext}`;
    if (i > 9999) break; // 兜底防死循环
  }
  return P.join(dir, candidate);
}

/** 校验用户选的数据目录是否可用（带盘符、非盘根、非系统目录）。 */
function validateDataDir(dir, platform) {
  const plat = platform || process.platform;
  const d = String(dir || "").trim();
  if (!d) return { ok: false, reason: "路径为空" };
  if (plat === "win32") {
    if (!/^[a-zA-Z]:[\\/]/.test(d)) return { ok: false, reason: "请使用带盘符的绝对路径" };
    const low = d.toLowerCase().replace(/[\\/]+$/, "");
    if (/^[a-z]:$/.test(low)) return { ok: false, reason: "不能直接用盘符根目录" };
    if (low === "c:\\windows" || low.startsWith("c:\\windows\\")) return { ok: false, reason: "不能放在 Windows 目录内" };
    if (low === "c:\\program files" || low === "c:\\program files (x86)") return { ok: false, reason: "不能放在 Program Files 根" };
    return { ok: true };
  }
  // V312 修真实缺陷：旧版只有 Windows 规则——Linux/macOS 上所有合法绝对路径
  // （如 /data/hashmm）都被"请使用带盘符的绝对路径"拒掉；而 "C:\HashMMData" 这种
  // Windows 形态反而放行 → 在 POSIX 上它是【相对路径】→ mkdir 出字面量垃圾目录
  // （本仓库 desktop/ 下曾出现过 `C:\HashMMData` 目录，就是这么来的）。
  if (/^[a-zA-Z]:[\\/]/.test(d)) return { ok: false, reason: "Windows 盘符路径在本系统无效，请用 / 开头的绝对路径" };
  if (!d.startsWith("/")) return { ok: false, reason: "请使用 / 开头的绝对路径" };
  const norm = d.replace(/\/+$/, "") || "/";
  if (norm === "/") return { ok: false, reason: "不能直接用根目录" };
  const forbidden = ["/etc", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev", "/lib", "/lib64"];
  if (forbidden.some((p) => norm === p || norm.startsWith(p + "/")))
    return { ok: false, reason: "不能放在系统目录内" };
  return { ok: true };
}

module.exports = {
  PRODUCT, DATA_FOLDER, CONFIG_FILE, SUBDIRS,
  defaultDataRoot, resolveDataRoot, subDir, downloadsDir, documentsDir, notesDir,
  allDirs, configPath, sanitizeFilename, uniqueFilePath, validateDataDir,
};
