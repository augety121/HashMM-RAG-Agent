/**
 * desktop/services/update-service.js — 更新服务（V101，从 main.js 抽出）。
 *
 * 把更新逻辑收口成服务：版本比较是**纯逻辑**（可单测），electron-updater 的 feedURL 配置
 * 与"检查一次"封装在此。main.js 增量委托，行为不变。
 */
"use strict";

/**
 * 语义化版本比较：返回 -1 / 0 / 1（a<b / a==b / a>b）。
 * 容忍前缀 v、预发布后缀（-beta 等按"小于正式版"处理的简化规则：有预发布 < 无预发布）。
 */
function compareVersions(a, b) {
  const parse = (s) => {
    const m = String(s || "0").trim().replace(/^v/i, "").split("-");
    const nums = m[0].split(".").map((x) => parseInt(x, 10) || 0);
    while (nums.length < 3) nums.push(0);
    return { nums, pre: m[1] || "" };
  };
  const pa = parse(a), pb = parse(b);
  for (let i = 0; i < 3; i++) {
    if (pa.nums[i] !== pb.nums[i]) return pa.nums[i] < pb.nums[i] ? -1 : 1;
  }
  // 主版本相等：有预发布的 < 无预发布的
  if (pa.pre && !pb.pre) return -1;
  if (!pa.pre && pb.pre) return 1;
  if (pa.pre && pb.pre) return pa.pre < pb.pre ? -1 : pa.pre > pb.pre ? 1 : 0;
  return 0;
}

/** remote 是否比 current 新。 */
function isNewer(remote, current) { return compareVersions(remote, current) > 0; }

class UpdateService {
  /** @param {object} opts { logger } */
  constructor(opts = {}) {
    this.name = "update";
    this.log = opts.logger || { info() {}, warn() {} };
  }

  /** 配置 electron-updater 的 autoUpdater（feedURL/自动下载/退出时装）。 */
  configure(autoUpdater, { feedUrl, autoDownload = true, autoInstallOnQuit = true } = {}) {
    if (!autoUpdater) return false;
    try {
      autoUpdater.autoDownload = autoDownload;
      autoUpdater.autoInstallOnAppQuit = autoInstallOnQuit;
      if (feedUrl) autoUpdater.setFeedURL({ provider: "generic", url: String(feedUrl).replace(/\/+$/, "") + "/desktop-updates" });
      return true;
    } catch (e) { this.log.warn("配置 autoUpdater 失败", { err: String(e) }); return false; }
  }

  /**
   * 检查一次更新（promisify 掉事件）。超时/出错都 resolve 成结构化结果，不抛。
   * @returns {Promise<{available:boolean, version?:string, error?:string}>}
   */
  checkOnce(autoUpdater, timeoutMs = 15000) {
    if (!autoUpdater) return Promise.resolve({ available: false, error: "无 autoUpdater" });
    return new Promise((resolve) => {
      let done = false;
      const finish = (r) => { if (done) return; done = true; cleanup(); resolve(r); };
      const onAvail = (info) => finish({ available: true, version: info && info.version });
      const onNone = () => finish({ available: false });
      const onErr = (err) => finish({ available: false, error: (err && err.message) || String(err) });
      const cleanup = () => {
        try {
          autoUpdater.removeListener("update-available", onAvail);
          autoUpdater.removeListener("update-not-available", onNone);
          autoUpdater.removeListener("error", onErr);
        } catch (_) { /* */ }
      };
      autoUpdater.once("update-available", onAvail);
      autoUpdater.once("update-not-available", onNone);
      autoUpdater.once("error", onErr);
      try { autoUpdater.checkForUpdates(); } catch (e) { finish({ available: false, error: String(e) }); }
      setTimeout(() => finish({ available: false, error: "检查更新超时" }), timeoutMs);
    });
  }
}

module.exports = { UpdateService, compareVersions, isNewer };
