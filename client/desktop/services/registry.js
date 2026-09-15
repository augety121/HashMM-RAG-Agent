/**
 * desktop/services/registry.js — 服务注册表 / 定位器（V101）。
 *
 * 对标 Marvis 的服务化结构（CLoginService / 各独立服务）。提供一个集中的服务定位器：
 * 各服务以名字注册，其它地方按名解析；带生命周期（startAll/stopAll）。
 *
 * 这是「主进程按服务拆分」的骨架——**不做大爆炸重写**（main.js 1755 行能跑，硬拆风险大）。
 * 策略：新逻辑（ModelService）直接长在服务层；已有单例（logger/storage/mcp host）增量
 * 收口进来，旧的访问方式继续可用，逐步迁移。纯逻辑，可单测。
 */
"use strict";

class ServiceRegistry {
  constructor() { this._services = new Map(); this._started = false; }

  /** 注册一个服务实例（重复名抛错，避免静默覆盖）。 */
  register(name, instance) {
    if (!name) throw new Error("服务名必填");
    if (this._services.has(name)) throw new Error(`服务已存在：${name}`);
    this._services.set(name, instance);
    return this;
  }

  /** 注册或替换（迁移期允许覆盖）。 */
  set(name, instance) { this._services.set(name, instance); return this; }

  has(name) { return this._services.has(name); }
  get(name) {
    if (!this._services.has(name)) throw new Error(`未注册的服务：${name}`);
    return this._services.get(name);
  }
  tryGet(name) { return this._services.get(name) || null; }
  names() { return Array.from(this._services.keys()); }
  size() { return this._services.size; }

  /** 依次 start() 所有实现了该方法的服务（幂等：只 start 一次）。 */
  async startAll() {
    if (this._started) return;
    for (const [, svc] of this._services) {
      if (svc && typeof svc.start === "function") { try { await svc.start(); } catch (e) { /* 单个服务失败不连坐 */ } }
    }
    this._started = true;
  }

  /** 依次 stop() 所有实现了该方法的服务（逆序，best-effort）。 */
  async stopAll() {
    const entries = Array.from(this._services.entries()).reverse();
    for (const [, svc] of entries) {
      if (svc && typeof svc.stop === "function") { try { await svc.stop(); } catch (_) { /* */ } }
    }
    this._started = false;
  }
}

// 进程级单例（main.js 用同一个定位器）
let _singleton = null;
function getRegistry() { if (!_singleton) _singleton = new ServiceRegistry(); return _singleton; }

module.exports = { ServiceRegistry, getRegistry };
