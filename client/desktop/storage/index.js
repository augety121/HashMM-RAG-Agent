/**
 * desktop/storage/index.js — 主进程存储服务（V100）。
 *
 * 把 workspace.js 的纯逻辑接到真实 fs/IPC：
 *   - 配置：把用户选的数据根持久化到 <installDir>\.hashmm-storage.json
 *   - ensureDirs：按需建出 HashMM Files\{Downloads,Documents,Notes}
 *   - saveFile(name, content, kind)：写到 Documents/Notes，自动净化名 + 防覆盖
 *   - getDownloadTarget(filename)：返回 Downloads 下不冲突的目标路径（给下载用）
 *   - IPC：hashmm:storage:getRoot / setRoot(带系统文件夹选择器) / saveFile / reveal
 *
 * 默认数据根在安装目录内（便携式/微信式）；用户可改到别处。全程 try/catch。
 */
"use strict";
const fs = require("fs");
const path = require("path");
const W = require("./workspace");

class Storage {
  /** @param {object} opts { installDir, dialog, shell, logger } */
  constructor(opts = {}) {
    this.installDir = opts.installDir || process.cwd();
    this.dialog = opts.dialog || null;   // electron dialog（选文件夹）
    this.shell = opts.shell || null;     // electron shell（在资源管理器打开）
    this.log = opts.logger || { info() {}, warn() {}, error() {} };
    this._configured = this._loadConfig();
  }

  _configFile() { return W.configPath(this.installDir); }

  _loadConfig() {
    try {
      const p = this._configFile();
      if (fs.existsSync(p)) { const j = JSON.parse(fs.readFileSync(p, "utf8")); return j.dataRoot || ""; }
    } catch (e) { this.log.warn("storage 配置读取失败", { err: String(e) }); }
    return "";
  }

  _saveConfig() {
    try { fs.writeFileSync(this._configFile(), JSON.stringify({ dataRoot: this._configured || "" }, null, 2), "utf8"); }
    catch (e) { this.log.warn("storage 配置写入失败", { err: String(e) }); }
  }

  /** 当前生效数据根（自定义或安装目录内默认）。 */
  dataRoot() { return W.resolveDataRoot({ installDir: this.installDir, configuredPath: this._configured }); }

  isDefault() { return !this._configured; }

  /** 按需建出数据目录结构。返回数据根。 */
  ensureDirs() {
    const root = this.dataRoot();
    for (const d of W.allDirs(root)) { try { fs.mkdirSync(d, { recursive: true }); } catch (e) { this.log.warn("建目录失败", { d, err: String(e) }); } }
    return root;
  }

  /** 改数据根（校验 + 持久化 + 建目录）。空字符串=恢复默认。 */
  setDataRoot(dir) {
    if (dir) {
      const v = W.validateDataDir(dir);
      if (!v.ok) return { ok: false, error: v.reason };
      this._configured = String(dir).replace(/[\\/]+$/, "");
    } else {
      this._configured = "";
    }
    this._saveConfig();
    this.ensureDirs();
    return { ok: true, dataRoot: this.dataRoot(), isDefault: this.isDefault() };
  }

  /**
   * 保存用户文件。kind: "notes"(md 笔记) | "documents"(默认)。
   * 自动净化文件名 + 防覆盖去重。content 为字符串或 Buffer。
   */
  saveFile(name, content, kind) {
    this.ensureDirs();
    const root = this.dataRoot();
    const dir = kind === "notes" ? W.notesDir(root) : W.documentsDir(root);
    const target = W.uniqueFilePath(dir, name || "untitled", (p) => fs.existsSync(p), path);
    fs.writeFileSync(target, content == null ? "" : content);
    this.log.info("saveFile", { target });
    return { ok: true, path: target };
  }

  /** 下载目标路径（Downloads 下不冲突的全路径），交给下载器写入。 */
  getDownloadTarget(filename) {
    this.ensureDirs();
    const dir = W.downloadsDir(this.dataRoot());
    return W.uniqueFilePath(dir, filename || "download", (p) => fs.existsSync(p), path);
  }

  /** 在系统资源管理器里打开数据根。 */
  reveal() {
    const root = this.ensureDirs();
    try { if (this.shell) this.shell.openPath(root); return { ok: true, path: root }; }
    catch (e) { return { ok: false, error: String(e) }; }
  }

  /** 弹系统文件夹选择器选新数据根。需 electron dialog。 */
  async pickDataRoot() {
    if (!this.dialog) return { ok: false, error: "无对话框能力" };
    const r = await this.dialog.showOpenDialog({ properties: ["openDirectory", "createDirectory"], title: "选择 HashMM 数据保存位置" });
    if (r.canceled || !r.filePaths || !r.filePaths[0]) return { ok: false, canceled: true };
    return this.setDataRoot(r.filePaths[0]);
  }
}

/** 注册存储 IPC。返回 Storage 单例。 */
function registerStorageIpc(ipcMain, storage) {
  if (!ipcMain || typeof ipcMain.handle !== "function") return storage;
  ipcMain.handle("hashmm:storage:getRoot", () => ({ ok: true, dataRoot: storage.dataRoot(), isDefault: storage.isDefault() }));
  ipcMain.handle("hashmm:storage:setRoot", async (_e, dir) => {
    try { return dir === "__pick__" ? await storage.pickDataRoot() : storage.setDataRoot(dir); }
    catch (e) { return { ok: false, error: String(e) }; }
  });
  ipcMain.handle("hashmm:storage:saveFile", (_e, name, content, kind) => {
    try { return storage.saveFile(name, content, kind); } catch (e) { return { ok: false, error: String(e) }; }
  });
  ipcMain.handle("hashmm:storage:reveal", () => { try { return storage.reveal(); } catch (e) { return { ok: false, error: String(e) }; } });
  return storage;
}

module.exports = { Storage, registerStorageIpc };
