/**
 * desktop/services/bootstrap.js — 服务引导（V101）。
 *
 * 把所有 service 的注册 + IPC 收口到一处，main.js 只调一行 bootstrapServices(deps)，更薄。
 * 全部依赖注入（ipcMain/registry/storage/backendMgr/路径/logger），便于单测——传假 ipcMain
 * 即可验证"所有服务都注册、所有 handler 都挂上"。任何子项失败不连坐（各自 try/catch）。
 */
"use strict";

/**
 * @param {object} deps
 * @param {object} deps.ipcMain          electron ipcMain（或测试假对象，需有 handle 方法）
 * @param {object} deps.registry         ServiceRegistry 实例
 * @param {object} deps.storage          Storage 实例
 * @param {function} deps.registerStorageIpc storage IPC 注册函数
 * @param {object} deps.backendMgr       backendmgr 模块
 * @param {object} deps.logger           logger
 * @param {string} deps.installDir
 * @param {string} deps.userData
 * @param {string} deps.resourcesPath
 * @param {string} deps.devSrcDir
 * @param {string} deps.tmpDir
 * @param {boolean} deps.isPackaged
 * @returns {{registry:object, modelService:object}}
 */
function bootstrapServices(deps) {
  const d = deps || {};
  const reg = d.registry;
  const ipc = d.ipcMain;
  const logger = d.logger || { info() {}, warn() {} };
  const handle = (name, fn) => { if (ipc && typeof ipc.handle === "function") ipc.handle(name, fn); };

  // 基础：storage + logger 收口进定位器 + storage IPC
  try {
    if (d.storage) reg.set("storage", d.storage);
    reg.set("logger", logger);
    if (d.storage && typeof d.registerStorageIpc === "function") d.registerStorageIpc(ipc, d.storage);
  } catch (e) { logger.warn && logger.warn("bootstrap storage 失败", { err: String(e) }); }

  // 模型服务 + IPC
  let modelSvc = null;
  try {
    const { ModelService } = require("./model-service");
    modelSvc = new ModelService({ modelsBasePath: d.resourcesPath || d.installDir, logger });
    reg.set("model", modelSvc);
    handle("hashmm:model:capability", () => ({ ok: true, capability: modelSvc.getCapability() }));
    handle("hashmm:model:available", () => { try { return { ok: true, models: modelSvc.available() }; } catch (e) { return { ok: false, error: String(e) }; } });
    handle("hashmm:model:recommend", (_e, name) => { try { return { ok: true, decision: modelSvc.recommend(name) }; } catch (e) { return { ok: false, error: String(e) }; } });
    handle("hashmm:model:ocr", async (_e, input) => { try { return await modelSvc.runOcr(input); } catch (e) { return { ok: false, error: String(e) }; } });
  } catch (e) { logger.warn && logger.warn("bootstrap model 失败", { err: String(e) }); }

  // 更新 / 窗口 / 终端 / 后端 服务
  try {
    const { UpdateService } = require("./update-service");
    reg.set("update", new UpdateService({ logger }));
    const { WindowService } = require("./window-service");
    reg.set("window", new WindowService({ logger }));
    const { TerminalService } = require("./terminal-service");
    reg.set("terminal", new TerminalService({ logger }));
    const { BackendService } = require("./backend-service");
    reg.set("backend", new BackendService({
      mgr: d.backendMgr, userDataPath: d.userData,
      resourcesPath: d.isPackaged ? d.resourcesPath : "", devSrcDir: d.devSrcDir, logger,
    }));
  } catch (e) { logger.warn && logger.warn("bootstrap 服务组失败", { err: String(e) }); }

  // 环境隔离守卫 + IPC
  try {
    const G = require("../isolation/env-guard");
    const roots = G.allowedRoots({ installDir: d.installDir, userData: d.userData, tmpDir: d.tmpDir });
    reg.set("isolation", { roots, guard: G });
    handle("hashmm:isolation:policy", () => ({ ok: true, policy: G.policyChecklist(), roots }));
  } catch (e) { logger.warn && logger.warn("bootstrap isolation 失败", { err: String(e) }); }

  try { reg.startAll(); } catch (_) { /* */ }
  return { registry: reg, modelService: modelSvc };
}

module.exports = { bootstrapServices };
