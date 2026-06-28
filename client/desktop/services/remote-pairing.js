// desktop/services/remote-pairing.js
// 远程配对的「6 位授权码」核心逻辑——纯逻辑、无网络/IO 依赖，可单测。
// 这是「扫码配对 + 输入 6 位授权码」里真正有逻辑难度的部分：生成、过期、尝试次数限制、
// 防时序攻击的常量时间比较、一次性消费。WebSocket/mDNS 传输是另一层（需真机联调），
// 不在本模块内。
"use strict";
const crypto = require("crypto");

const DEFAULT_TTL_MS = 5 * 60 * 1000;   // 授权码有效期 5 分钟
const DEFAULT_MAX_ATTEMPTS = 5;          // 最多试错 5 次，超过作废（防暴力穷举 6 位码）

/** 常量时间比较两个等长字符串，避免按字符提前返回造成的时序侧信道。 */
function constantTimeEqual(a, b) {
  a = String(a); b = String(b);
  const ba = Buffer.from(a, "utf8");
  const bb = Buffer.from(b, "utf8");
  if (ba.length !== bb.length) {
    // 长度不同时仍做一次等长比较，避免因长度差异提前返回泄露信息
    crypto.timingSafeEqual(ba, ba);
    return false;
  }
  return crypto.timingSafeEqual(ba, bb);
}

/** 生成一个 6 位数字授权码（前导零保留，用加密随机数避免可预测）。 */
function genCode() {
  // 0..999999 的加密随机，补足 6 位
  const n = crypto.randomInt(0, 1000000);
  return String(n).padStart(6, "0");
}

/** 生成一次性会话令牌（配对成功后下发，用于后续连接鉴权）。 */
function genToken() {
  return crypto.randomBytes(24).toString("hex");
}

class PairingManager {
  /** @param {{ttlMs?:number, maxAttempts?:number, now?:()=>number, codeGen?:()=>string, tokenGen?:()=>string}} [opts] */
  constructor(opts = {}) {
    this.ttlMs = opts.ttlMs || DEFAULT_TTL_MS;
    this.maxAttempts = opts.maxAttempts || DEFAULT_MAX_ATTEMPTS;
    this._now = opts.now || Date.now;          // 可注入时钟，便于测试过期
    this._codeGen = opts.codeGen || genCode;   // 可注入码生成，便于测试
    this._tokenGen = opts.tokenGen || genToken;
    this._active = null;   // { code, createdAt, expiresAt, attempts, used }
  }

  /** 签发一个新授权码（覆盖旧的——同一时刻只允许一个有效码）。返回 {code, expiresAt}。 */
  issue() {
    const now = this._now();
    this._active = {
      code: this._codeGen(),
      createdAt: now,
      expiresAt: now + this.ttlMs,
      attempts: 0,
      used: false,
    };
    return { code: this._active.code, expiresAt: this._active.expiresAt };
  }

  /** 当前有效码的展示信息（给宿主 UI 显示/生成二维码用）；无有效码或已过期/已用则返回 null。 */
  current() {
    const a = this._active;
    if (!a || a.used) return null;
    if (this._now() >= a.expiresAt) return null;
    return { code: a.code, expiresAt: a.expiresAt, remainingMs: a.expiresAt - this._now() };
  }

  /** 主动作废当前码（例如用户关闭配对面板）。 */
  revoke() { this._active = null; }

  /**
   * 校验对端提交的授权码。
   * @returns {{ok:true, token:string} | {ok:false, reason:"none"|"expired"|"locked"|"mismatch", remainingAttempts?:number}}
   */
  verify(input) {
    const a = this._active;
    const now = this._now();
    if (!a || a.used) return { ok: false, reason: "none" };
    if (now >= a.expiresAt) { this._active = null; return { ok: false, reason: "expired" }; }
    if (a.attempts >= this.maxAttempts) { this._active = null; return { ok: false, reason: "locked" }; }

    // 规整输入：去空格、仅保留数字（容忍对端把"123 456"或"123-456"传上来）
    const clean = String(input == null ? "" : input).replace(/\D/g, "");
    const match = clean.length === a.code.length && constantTimeEqual(clean, a.code);
    if (match) {
      a.used = true;                       // 一次性消费，防重放
      const token = this._tokenGen();
      this._active = null;                 // 配对完成即清除码
      return { ok: true, token };
    }
    a.attempts += 1;
    const remainingAttempts = this.maxAttempts - a.attempts;
    if (remainingAttempts <= 0) { this._active = null; return { ok: false, reason: "locked", remainingAttempts: 0 }; }
    return { ok: false, reason: "mismatch", remainingAttempts };
  }
}

module.exports = { PairingManager, genCode, genToken, constantTimeEqual, DEFAULT_TTL_MS, DEFAULT_MAX_ATTEMPTS };
