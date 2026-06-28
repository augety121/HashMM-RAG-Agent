/**
 * desktop/services/backend-service.js — 后端服务（V101，从 main.js 抽出）。
 *
 * 把本地后端 sidecar 的管理收口成服务：路径解析（home/src 目录）是纯逻辑可测；
 * 生命周期委托已有的 backendmgr 模块。注册进定位器，对外提供 home()/srcDir()/status()。
 *
 * 注：home 目录在 userData 内（隔离：用户数据不出 app 自己的目录，见 env-guard）。
 */
"use strict";
const path = require("path");

/** 本地后端工作目录：userData/local-backend（旧默认，作回退）。 */
function backendHome(userDataPath) {
  return path.join(String(userDataPath || ""), "local-backend");
}

/**
 * 解析后端数据目录（解析的文档/知识库/向量都落这里）。优先级：
 *   1. 用户在设置里选的 configuredDir（非空）
 *   2. **安装目录下 <installDir>\local-backend**（默认——"软件的东西都在安装位置"）
 *   3. userData\local-backend（兜底，installDir 缺失时）
 * 纯函数可测。
 */
function resolveBackendHome({ configuredDir, installDir, userDataDir }) {
  const c = String(configuredDir || "").trim();
  if (c) return c;
  if (installDir) return path.join(String(installDir), "local-backend");
  return backendHome(userDataDir);
}

/** 后端源码目录：resourcesPath/backend（打包后）或开发目录回退。 */
function backendSrcDir(resourcesPath, devFallback) {
  if (resourcesPath) return path.join(resourcesPath, "backend");
  return devFallback || "";
}

class BackendService {
  /** @param {object} opts { mgr(backendmgr), userDataPath, resourcesPath, devSrcDir, logger } */
  constructor(opts = {}) {
    this.name = "backend";
    this.mgr = opts.mgr || null;
    this.userDataPath = opts.userDataPath || "";
    this.resourcesPath = opts.resourcesPath || "";
    this.devSrcDir = opts.devSrcDir || "";
    this.log = opts.logger || { info() {} };
  }

  home() { return backendHome(this.userDataPath); }
  srcDir() { return backendSrcDir(this.resourcesPath, this.devSrcDir); }

  /** 当前后端状态（委托 backendmgr）。无 mgr 时返回 not-running。 */
  status() {
    if (!this.mgr || typeof this.mgr.status !== "function") return { running: false };
    try { return this.mgr.status(this.home()); } catch (_) { return { running: false }; }
  }
}

module.exports = { BackendService, backendHome, backendSrcDir, resolveBackendHome };
