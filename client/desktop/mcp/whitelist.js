/**
 * desktop/mcp/whitelist.js — 工具白名单管理（V100，对标 Marvis 的 WhitelistManager）。
 *
 * Marvis 用 WhitelistManager 管控哪些 App/工具可被 agent 操作，支持远端拉取 + TTL 3600s。
 * 这里同款：维护一份允许清单 + 拉取时间戳；过期（超 TTL）则视为"需刷新"，并按
 * defaultPolicy（"deny" 默认更安全 / "allow"）兜底。纯逻辑，可单测。
 */
"use strict";

const DEFAULT_TTL_MS = 3600 * 1000; // 与 Marvis 一致：1 小时

class WhitelistManager {
  /**
   * @param {object} opts
   * @param {number} opts.ttlMs 白名单有效期（默认 3600s）
   * @param {"deny"|"allow"} opts.defaultPolicy 未命中/已过期时的兜底（默认 deny）
   * @param {string[]} opts.initial 初始允许清单
   */
  constructor(opts = {}) {
    this.ttlMs = opts.ttlMs || DEFAULT_TTL_MS;
    this.defaultPolicy = opts.defaultPolicy === "allow" ? "allow" : "deny";
    this._allow = new Set(opts.initial || []);
    this._fetchedAt = opts.initial ? Date.now() : 0;
  }

  /** 远端/本地刷新允许清单，记录拉取时间。 */
  setAllowed(list, now = Date.now()) {
    this._allow = new Set(Array.isArray(list) ? list : []);
    this._fetchedAt = now;
    return this;
  }

  /** 是否已过期（超过 TTL 没刷新）。从未拉取过也算过期。 */
  isExpired(now = Date.now()) {
    if (!this._fetchedAt) return true;
    return now - this._fetchedAt > this.ttlMs;
  }

  /** 是否需要刷新（过期即需要）。宿主据此决定何时重新拉取。 */
  needsRefresh(now = Date.now()) { return this.isExpired(now); }

  /**
   * 工具是否被允许。已过期 → 回退 defaultPolicy（deny 更安全）；未过期 → 看清单。
   */
  isAllowed(name, now = Date.now()) {
    if (this.isExpired(now)) return this.defaultPolicy === "allow";
    return this._allow.has(name);
  }

  snapshot() {
    return { allow: Array.from(this._allow), fetchedAt: this._fetchedAt, ttlMs: this.ttlMs, defaultPolicy: this.defaultPolicy };
  }
}

module.exports = { WhitelistManager, DEFAULT_TTL_MS };
