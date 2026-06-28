/**
 * desktop/services/terminal-service.js — 终端服务（V101，从 main.js 抽出）。
 *
 * 内嵌终端（node-pty）里的**可测纯逻辑**抽到这里：
 *   - appendBuffer：输出回放缓冲的环形截尾（超容量保最新一段）
 *   - defaultShell：按平台选 shell
 *   - buildShellEnv：终端环境变量准备（TERM / HASHMM_DESKTOP / 非 Windows 中文 locale 兜底）
 *   - resolveStartCwd：起始目录解析（存在用之，否则回 home）
 * 以及一个会话注册表 SessionStore（管 id→pty 与 id→缓冲两张表，复用/回放/清理）。
 *
 * pty.spawn 仍由 main.js 调用（原生模块，注入），main.js 的 term:spawn 调用点委托到
 * 这里的纯函数与会话表，行为不变、逻辑可单测。
 */
"use strict";

const DEFAULT_BUF_CAP = 256 * 1024; // 回放缓冲上限（与 main.js TERM_BUF_CAP 一致量级）

/** 追加输出到回放缓冲并环形截尾：超 cap 则只保留最新 cap 字节。 */
function appendBuffer(existing, data, cap = DEFAULT_BUF_CAP) {
  const buf = (existing || "") + (data || "");
  return buf.length > cap ? buf.slice(buf.length - cap) : buf;
}

/** 按平台选默认 shell。 */
function defaultShell(platform = process.platform, env = process.env) {
  if (platform === "win32") return "powershell.exe"; // ConPTY 由 node-pty 自动启用
  return (env && env.SHELL) || "/bin/bash";
}

/** 终端环境变量准备：TERM + HASHMM_DESKTOP；非 Windows 且无 UTF-8 locale → 兜底 zh_CN.UTF-8。 */
function buildShellEnv(baseEnv = {}, platform = process.platform) {
  const env = Object.assign({}, baseEnv, { TERM: "xterm-256color", HASHMM_DESKTOP: "1" });
  if (platform !== "win32" && !/UTF-8/i.test(env.LC_ALL || env.LC_CTYPE || env.LANG || "")) {
    env.LANG = "zh_CN.UTF-8";
  }
  return env;
}

/** 起始目录解析：传入的 cwd 存在则用之，否则回 home。existsFn 由调用方注入。 */
function resolveStartCwd(cwd, existsFn, homeDir) {
  if (cwd && existsFn && existsFn(cwd)) return cwd;
  return homeDir;
}

/** 终端会话注册表：管 id→pty 与 id→回放缓冲。 */
class SessionStore {
  constructor(cap = DEFAULT_BUF_CAP) { this.cap = cap; this._proc = new Map(); this._buf = new Map(); }
  has(id) { return this._proc.has(id); }
  get(id) { return this._proc.get(id); }
  buffer(id) { return this._buf.get(id) || ""; }
  add(id, proc) { this._proc.set(id, proc); this._buf.set(id, ""); return proc; }
  append(id, data) { this._buf.set(id, appendBuffer(this._buf.get(id), data, this.cap)); return this._buf.get(id); }
  remove(id) { this._proc.delete(id); this._buf.delete(id); }
  ids() { return Array.from(this._proc.keys()); }
  size() { return this._proc.size; }
}

class TerminalService {
  constructor(opts = {}) {
    this.name = "terminal";
    this.store = new SessionStore(opts.bufferCap || DEFAULT_BUF_CAP);
    this.log = opts.logger || { info() {} };
  }
}

module.exports = {
  TerminalService, SessionStore,
  appendBuffer, defaultShell, buildShellEnv, resolveStartCwd, DEFAULT_BUF_CAP,
};
