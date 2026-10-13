/**
 * desktop/logging/logger.js — 结构化日志系统（V100，对标 Marvis 的 logs/ 基建）。
 *
 * 大厂桌面应用（VS Code/Slack/Discord）都有的日志基建，与框架无关：
 *   - 分级（debug/info/warn/error），按 level 过滤
 *   - 结构化 JSON 行输出（一行一条，便于检索/上报）
 *   - 文件轮转（超大小切片，保留最近 N 份）
 *   - 密钥脱敏（token/password/secret/authorization 等自动打码，绝不落盘明文）
 *   - 内存环形缓冲（保留最近 N 条，崩溃时随报告一起带走）
 *   - 子 logger（携带上下文字段，如 {module:"mcp"}）
 *
 * 纯格式化/脱敏/轮转判定是纯函数，可单测；文件写入用 node fs（沙箱可跑）。
 */
"use strict";
const fs = require("fs");
const path = require("path");

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40, silent: 100 };
const REDACT_DEFAULT = ["token", "password", "passwd", "secret", "authorization", "auth", "apikey", "api_key", "cookie", "accesstoken", "refreshtoken"];

/** 递归脱敏：key 命中（不分大小写、去下划线）→ 值打码。返回新对象，不改原值。 */
function redact(obj, keys = REDACT_DEFAULT, _seen) {
  const norm = (k) => String(k).toLowerCase().replace(/[_-]/g, "");
  const deny = new Set(keys.map(norm));
  const seen = _seen || new WeakSet();
  function walk(v) {
    if (v === null || typeof v !== "object") return v;
    if (seen.has(v)) return "[Circular]";
    seen.add(v);
    if (Array.isArray(v)) return v.map(walk);
    const out = {};
    for (const [k, val] of Object.entries(v)) {
      out[k] = deny.has(norm(k)) ? "[REDACTED]" : walk(val);
    }
    return out;
  }
  return walk(obj);
}

/** 一条日志 → JSON 行（含时间/级别/名字/消息 + 脱敏后的字段）。 */
function formatLine(level, name, msg, fields, redactKeys) {
  const rec = Object.assign(
    { ts: new Date().toISOString(), level, name: name || "app", msg: String(msg == null ? "" : msg) },
    redact(fields || {}, redactKeys)
  );
  return JSON.stringify(rec);
}

/** 轮转判定：当前文件大小 + 即将写入的字节 > 上限 → 该轮转。 */
function shouldRotate(currentSize, incomingBytes, maxSizeBytes) {
  if (!maxSizeBytes || maxSizeBytes <= 0) return false;
  return currentSize + incomingBytes > maxSizeBytes;
}

class Logger {
  constructor(opts = {}) {
    this.name = opts.name || "app";
    this.level = typeof opts.level === "number" ? opts.level : (LEVELS[opts.level] || LEVELS.info);
    this.dir = opts.dir || null;
    this.file = opts.file || "hashmm.log";
    this.maxSizeBytes = opts.maxSizeBytes || 5 * 1024 * 1024; // 5MB/片
    this.maxFiles = opts.maxFiles || 5;
    this.redactKeys = opts.redactKeys || REDACT_DEFAULT;
    this.context = opts.context || {};
    this.ring = opts.ring || { buf: [], max: opts.ringMax || 500 };
    this._size = 0;
    if (this.dir) {
      try {
        fs.mkdirSync(this.dir, { recursive: true });
        const p = this._path();
        this._size = fs.existsSync(p) ? fs.statSync(p).size : 0;
      } catch (_) { this.dir = null; /* 落盘不可用则只进内存/控制台 */ }
    }
  }

  _path() { return path.join(this.dir, this.file); }

  /** 携带上下文的子 logger（共享同一文件与环形缓冲）。 */
  child(context) {
    return new Logger({
      name: this.name, level: this.level, dir: this.dir, file: this.file,
      maxSizeBytes: this.maxSizeBytes, maxFiles: this.maxFiles, redactKeys: this.redactKeys,
      context: Object.assign({}, this.context, context || {}), ring: this.ring,
    });
  }

  _emit(levelName, msg, fields) {
    if (LEVELS[levelName] < this.level) return;
    const merged = Object.assign({}, this.context, fields || {});
    const line = formatLine(levelName, this.name, msg, merged, this.redactKeys);
    // 环形缓冲（崩溃报告用）
    this.ring.buf.push(line);
    if (this.ring.buf.length > this.ring.max) this.ring.buf.shift();
    // 控制台
    (levelName === "error" || levelName === "warn" ? console.error : console.log)(line);
    // 文件（带轮转）
    if (this.dir) this._writeFile(line + "\n");
  }

  _writeFile(text) {
    try {
      const bytes = Buffer.byteLength(text);
      if (shouldRotate(this._size, bytes, this.maxSizeBytes)) this._rotate();
      fs.appendFileSync(this._path(), text);
      this._size += bytes;
    } catch (_) { /* 落盘失败不影响主流程 */ }
  }

  _rotate() {
    try {
      const base = this._path();
      // hashmm.log → hashmm.1.log → ... 删除最旧，保留 maxFiles 份
      for (let i = this.maxFiles - 1; i >= 1; i--) {
        const src = i === 1 ? base : `${base}.${i - 1}`;
        const dst = `${base}.${i}`;
        if (fs.existsSync(src)) { try { fs.rmSync(dst, { force: true }); } catch (_) {} fs.renameSync(src, dst); }
      }
      this._size = 0;
    } catch (_) { /* */ }
  }

  debug(msg, f) { this._emit("debug", msg, f); }
  info(msg, f) { this._emit("info", msg, f); }
  warn(msg, f) { this._emit("warn", msg, f); }
  error(msg, f) {
    // Error 对象 → 提取 message/stack
    if (msg instanceof Error) { f = Object.assign({ stack: msg.stack }, f); msg = msg.message; }
    this._emit("error", msg, f);
  }

  /** 取最近 N 条（崩溃报告）。 */
  recent(n) { const b = this.ring.buf; return n ? b.slice(-n) : b.slice(); }
}

function createLogger(opts) { return new Logger(opts); }

module.exports = { createLogger, Logger, redact, formatLine, shouldRotate, LEVELS, REDACT_DEFAULT };
