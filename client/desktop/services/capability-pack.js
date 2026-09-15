/** desktop/services/capability-pack.js — 可选能力包的「按需下载」管理器（V102）。
 *
 * 背景：V334 的 installer-native 重新内置已校验 Python，保证干净电脑可离线启动；
 * 本模块保留给运行时修复/升级和 OCR 等真正可选的大包，不再是首次启动前置条件。
 *
 * 设计哲学与 backendmgr.js 一致：纯 Node、零 electron 依赖；fetch / fs / 解压 / sha256
 * 全部可注入 —— 所以能在沙箱里用假实现完整冒烟（见 test_capability-pack.js，已实测通过）。
 * 所有方法不抛错，失败以 {ok:false, error} 返回。
 *
 * 能力包清单（manifest）由你的 HashMM 后端下发/托管（publish.url 同源）。每个包：
 *   { id, version, url, sha256, sizeBytes }
 * 下载流程：fetch(url) -> 写临时 zip -> sha256 校验 -> 解压到 <userData>/packs/<id>/
 *           -> 写 .pack-info.json 标记。校验不过 / 解压失败都不留半成品。
 */
"use strict";
const path = require("path");
const crypto = require("crypto");

/** 内置默认清单。占位 URL 改为**可配置**：
 *  - 发布/自托管时设环境变量 HASHMM_PACKS_BASE=https://你的发布服务器（推荐），或由后端
 *    /desktop-updates/packs.json 下发覆盖（见 setManifest）。
 *  - 未配置时默认指向本机后端端口 17680（而非旧的死链 6006——那是开发期 AutoDL 端口，
 *    端用户机器上没有该服务，必然 fetch failed）。 */
const PACKS_BASE = String(process.env.HASHMM_PACKS_BASE || "http://127.0.0.1:17680").replace(/\/+$/, "");
const DEFAULT_MANIFEST = {
  "python-runtime": {
    id: "python-runtime",
    version: "3.12.8-deps1",
    url: `${PACKS_BASE}/desktop-updates/packs/python-runtime-3.12.8.zip`,
    sha256: "",          // 发布时填真值；空串表示「跳过校验」（仅本地调试用）
    sizeBytes: 0,
  },
  ocr: {
    id: "ocr",
    version: "onnx-winx64-dbnet-crnn-1",
    url: `${PACKS_BASE}/desktop-updates/packs/ocr-winx64.zip`,
    sha256: "",
    sizeBytes: 0,
  },
};

/** sha256 十六进制（小写）。纯函数，可单测。 */
function sha256Hex(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex").toLowerCase();
}

/** 校验 buffer 是否匹配期望 sha256。expected 为空串 -> 视为通过（调试）。纯函数。 */
function verifySha256(buf, expected) {
  if (!expected) return { ok: true, skipped: true };
  const got = sha256Hex(buf);
  if (got === String(expected).toLowerCase()) return { ok: true, got };
  return { ok: false, got, expected: String(expected).toLowerCase() };
}

/** Remote capability packs must be HTTPS and pinned by SHA-256.  The only
 * exception is loopback development, where the same user controls both ends. */
function validatePackSource(entry) {
  let u;
  try { u = new URL(String((entry && entry.url) || "")); }
  catch (_e) { return { ok: false, error: "能力包 URL 无效" }; }
  const host = String(u.hostname || "").toLowerCase();
  const loopback = host === "127.0.0.1" || host === "localhost" || host === "::1";
  if (!loopback && u.protocol !== "https:") {
    return { ok: false, error: "远程能力包必须使用 HTTPS" };
  }
  const sha = String((entry && entry.sha256) || "").toLowerCase();
  if (!loopback && !/^[0-9a-f]{64}$/.test(sha)) {
    return { ok: false, error: "远程能力包缺少有效 SHA-256，已拒绝下载" };
  }
  return { ok: true, loopback, checksumSkipped: !sha };
}

/** 解析/合并清单：外部 json 覆盖默认，缺字段回退默认。纯函数。 */
function parseManifest(json) {
  const out = {};
  for (const id of Object.keys(DEFAULT_MANIFEST)) out[id] = { ...DEFAULT_MANIFEST[id] };
  if (json && typeof json === "object") {
    for (const id of Object.keys(json)) {
      const e = json[id] || {};
      out[id] = { ...(out[id] || { id }), ...e, id };
    }
  }
  return out;
}

class CapabilityPackManager {
  /**
   * @param {object} deps
   *   userData   string   用户数据根目录（packs/ 落在这里）
   *   manifest   object   能力包清单（默认 DEFAULT_MANIFEST，可由 setManifest 更新）
   *   fetchBuf   async (url, {onProgress}) => Buffer        下载并返回字节（可注入）
   *   fs         {existsSync, mkdirSync, writeFileSync, readFileSync, rmSync}（可注入）
   *   extract    async (zipPath, destDir) => void           解压（默认 Windows tar.exe，可注入）
   *   tmpDir     string   临时目录（默认 userData/.pack-tmp）
   *   log        (msg)=>void
   */
  constructor(deps = {}) {
    this.userData = deps.userData || ".";
    this.manifest = deps.manifest || parseManifest(null);
    this._fetchBuf = deps.fetchBuf || defaultFetchBuf;
    this._fs = deps.fs || require("fs");
    this._extract = deps.extract || defaultExtract;
    this._tmpDir = deps.tmpDir || path.join(this.userData, ".pack-tmp");
    this._log = deps.log || (() => {});
  }

  setManifest(json) { this.manifest = parseManifest(json); }

  packsRoot() { return path.join(this.userData, "packs"); }
  packDir(id) { return path.join(this.packsRoot(), id); }
  _markerPath(id) { return path.join(this.packDir(id), ".pack-info.json"); }

  /** 是否已安装且版本匹配。 */
  isInstalled(id) {
    const entry = this.manifest[id];
    if (!entry) return false;
    try {
      const raw = this._fs.readFileSync(this._markerPath(id), "utf-8");
      const info = JSON.parse(raw);
      return info && info.id === id && info.version === entry.version && info.complete === true;
    } catch (_e) {
      return false;
    }
  }

  /** 已装则返回安装目录，否则空串。 */
  installedPath(id) { return this.isInstalled(id) ? this.packDir(id) : ""; }

  /**
   * 下载并安装一个能力包。幂等：已装且版本匹配 -> 直接返回 {ok:true, cached:true}。
   * @param {string} id
   * @param {object} opts { onProgress(pct0_100), force }
   * @returns {Promise<{ok:boolean, cached?:boolean, dir?:string, error?:string}>}
   */
  async install(id, opts = {}) {
    const entry = this.manifest[id];
    if (!entry) return { ok: false, error: `未知能力包: ${id}` };
    if (!opts.force && this.isInstalled(id)) {
      return { ok: true, cached: true, dir: this.packDir(id) };
    }
    const onProgress = typeof opts.onProgress === "function" ? opts.onProgress : () => {};

    const policy = validatePackSource(entry);
    if (!policy.ok) return { ok: false, error: policy.error };

    try {
      // 1) 下载
      this._log(`[pack] 下载 ${id} <- ${entry.url}`);
      const buf = await this._fetchBuf(entry.url, { onProgress });
      if (!buf || !buf.length) return { ok: false, error: `${id} 下载为空` };

      // 2) sha256 校验
      const v = verifySha256(buf, entry.sha256);
      if (!v.ok) return { ok: false, error: `${id} 校验失败: 期望 ${v.expected} 实得 ${v.got}` };
      if (v.skipped) this._log(`[pack] ${id} 未配置 sha256，跳过校验（调试模式）`);

      // 3) 写临时 zip
      this._ensureDir(this._tmpDir);
      const zipPath = path.join(this._tmpDir, `${id}.zip`);
      this._fs.writeFileSync(zipPath, buf);

      // 4) 解压到目标（先清旧目录，保证干净覆盖）
      const dest = this.packDir(id);
      this._rmrf(dest);
      this._ensureDir(dest);
      await this._extract(zipPath, dest);

      // 5) 写标记 + 清临时
      this._fs.writeFileSync(this._markerPath(id), JSON.stringify({
        id, version: entry.version, complete: true,
        installedAt: new Date().toISOString(), bytes: buf.length,
      }, null, 2));
      this._rmrf(zipPath);
      onProgress(100);
      this._log(`[pack] ${id} 安装完成 -> ${dest}`);
      return { ok: true, dir: dest };
    } catch (e) {
      return { ok: false, error: `${id} 安装失败: ${e && e.message ? e.message : e}` };
    }
  }

  _ensureDir(d) { try { this._fs.mkdirSync(d, { recursive: true }); } catch (_e) {} }
  _rmrf(p) {
    try {
      if (this._fs.rmSync) this._fs.rmSync(p, { recursive: true, force: true });
      else if (this._fs.existsSync && this._fs.existsSync(p) && this._fs.rmdirSync)
        this._fs.rmdirSync(p, { recursive: true });
    } catch (_e) {}
  }
}

/** 默认下载实现：Node 18+ 全局 fetch -> Buffer，带进度（Content-Length 已知时）。 */
async function defaultFetchBuf(url, { onProgress } = {}) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const total = Number(res.headers.get("content-length") || 0);
  if (!res.body || !total) {
    const ab = await res.arrayBuffer();
    return Buffer.from(ab);
  }
  const reader = res.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    if (onProgress) onProgress(Math.min(99, Math.round((got / total) * 100)));
  }
  return Buffer.concat(chunks.map((c) => Buffer.from(c)));
}

/** 默认解压实现：Windows 内置 tar.exe（与 bootstrap.c 一致）。非 Windows 抛错。 */
async function defaultExtract(zipPath, destDir) {
  const cp = require("child_process");
  const sysRoot = process.env.SystemRoot || "C:\\Windows";
  const tar = path.join(sysRoot, "System32", "tar.exe");
  await new Promise((resolve, reject) => {
    const p = cp.spawn(tar, ["-xf", zipPath, "-C", destDir], { windowsHide: true });
    p.on("error", reject);
    p.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`tar exit ${code}`))));
  });
}

module.exports = {
  CapabilityPackManager,
  sha256Hex,
  verifySha256,
  parseManifest,
  validatePackSource,
  DEFAULT_MANIFEST,
};
