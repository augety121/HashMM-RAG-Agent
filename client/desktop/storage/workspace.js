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
function defaultDataRoot(installDir) {
  return path.win32.join(String(installDir || ""), DATA_FOLDER);
}

/** 生效数据根：用户配了自定义路径就用它，否则用默认（安装目录内）。 */
function resolveDataRoot({ installDir, configuredPath }) {
  const c = configuredPath && String(configuredPath).trim();
  if (c) return c.replace(/[\\/]+$/, "");
  return defaultDataRoot(installDir);
}

function subDir(dataRoot, key) {
  return path.win32.join(String(dataRoot || ""), SUBDIRS[key] || key);
}
function downloadsDir(dataRoot) { return subDir(dataRoot, "downloads"); }
function documentsDir(dataRoot) { return subDir(dataRoot, "documents"); }
function notesDir(dataRoot) { return subDir(dataRoot, "notes"); }

/** 所有应被创建的目录（数据根 + 三个子目录）。 */
function allDirs(dataRoot) {
  return [dataRoot, downloadsDir(dataRoot), documentsDir(dataRoot), notesDir(dataRoot)];
}

function configPath(installDir) {
  return path.win32.join(String(installDir || ""), CONFIG_FILE);
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
function validateDataDir(dir) {
  const d = String(dir || "").trim();
  if (!d) return { ok: false, reason: "路径为空" };
  if (!/^[a-zA-Z]:[\\/]/.test(d)) return { ok: false, reason: "请使用带盘符的绝对路径" };
  const low = d.toLowerCase().replace(/[\\/]+$/, "");
  if (/^[a-z]:$/.test(low)) return { ok: false, reason: "不能直接用盘符根目录" };
  if (low === "c:\\windows" || low.startsWith("c:\\windows\\")) return { ok: false, reason: "不能放在 Windows 目录内" };
  if (low === "c:\\program files" || low === "c:\\program files (x86)") return { ok: false, reason: "不能放在 Program Files 根" };
  return { ok: true };
}

module.exports = {
  PRODUCT, DATA_FOLDER, CONFIG_FILE, SUBDIRS,
  defaultDataRoot, resolveDataRoot, subDir, downloadsDir, documentsDir, notesDir,
  allDirs, configPath, sanitizeFilename, uniqueFilePath, validateDataDir,
};
