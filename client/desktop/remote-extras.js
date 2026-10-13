/**
 * desktop/remote-extras.js — 远控扩展的纯逻辑控制器（V103.90）。
 *
 * 补两个 UU 有、你这边还缺的能力（多屏切换你已经有了，不在此模块）：
 *   1) 双向剪贴板同步（对标 UU ClipboardChangeH/RequestH/ResponseH/DataBlockH）
 *      —— 现状只有单向"复制答案到本地"，两端之间没有同步。难点不是传输，是**防回环**
 *      （收到对端剪贴板写入本地后，剪贴板变更监听会再次触发、把同一份内容发回去，形成死循环）、
 *      去重、大小上限、防抖。这里把这套策略做成纯状态机。
 *   2) 会话录制（把远端画面录下来存文件）—— viewer 端用 MediaRecorder；纯逻辑部分是
 *      容器/编码选择、文件名、分块与时长计账、状态机。
 *
 * 纯函数/纯类、不抛异常、不碰 DOM/Electron API（便于沙箱单测）。真正读写剪贴板/落盘由调用方做。
 */
"use strict";

// ── 1. 剪贴板同步控制器 ──

const CLIP_MAX_BYTES = 256 * 1024;   // 单次同步上限 256KB（大内容截断，避免刷爆 DataChannel）

/** 轻量字符串哈希（FNV-1a 32 位），用于去重与回环判定。非加密用途。 */
function hashText(s) {
  s = String(s == null ? "" : s);
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return ("0000000" + h.toString(16)).slice(-8);
}

function _utf8Bytes(s) {
  try { return (typeof Buffer !== "undefined") ? Buffer.byteLength(s, "utf8") : new Blob([s]).size; }
  catch (_e) { return s.length; }
}

class ClipboardSync {
  /**
   * @param opts { maxBytes?, minIntervalMs? } 大小上限与最小发送间隔（防抖）
   */
  constructor(opts) {
    const o = opts || {};
    this.maxBytes = o.maxBytes || CLIP_MAX_BYTES;
    this.minIntervalMs = o.minIntervalMs == null ? 300 : o.minIntervalMs;
    this._lastSentHash = null;     // 我方最近发出的内容哈希
    this._lastAppliedHash = null;  // 我方最近写入本地剪贴板（来自对端）的哈希
    this._lastSentAt = 0;
  }

  /**
   * 本地剪贴板发生变更时调用，判断**是否应该把它发给对端**。返回 { send, payload?, reason }。
   * 关键：若本地内容正是我们刚从对端写进来的（_lastAppliedHash），则不发（防回环）。
   */
  onLocalChange(text, now) {
    now = now == null ? Date.now() : now;
    if (text == null || text === "") return { send: false, reason: "empty" };
    const bytes = _utf8Bytes(text);
    let truncated = false;
    if (bytes > this.maxBytes) {
      // 截断到上限附近（按字符粗切，避免超限）
      text = String(text).slice(0, this.maxBytes);
      truncated = true;
    }
    const h = hashText(text);
    if (h === this._lastAppliedHash) return { send: false, reason: "echo_from_peer" };  // 防回环
    if (h === this._lastSentHash) return { send: false, reason: "duplicate" };           // 去重
    if (now - this._lastSentAt < this.minIntervalMs) return { send: false, reason: "debounced" };
    this._lastSentHash = h;
    this._lastSentAt = now;
    return { send: true, payload: { text, hash: h, truncated }, reason: "ok" };
  }

  /**
   * 收到对端剪贴板内容时调用，判断**是否应该写入本地剪贴板**。返回 { apply, text?, reason }。
   * 写入前登记 _lastAppliedHash，这样紧接着触发的本地变更监听会被 onLocalChange 判为回环而不回传。
   */
  onRemoteData(payload) {
    const text = payload && payload.text;
    if (text == null || text === "") return { apply: false, reason: "empty" };
    const h = (payload && payload.hash) || hashText(text);
    if (h === this._lastAppliedHash) return { apply: false, reason: "already_applied" };
    this._lastAppliedHash = h;
    return { apply: true, text, reason: "ok" };
  }
}

// ── 2. 会话录制控制器 ──

// 优先 webm/VP9 → VP8 → 通用 webm → mp4（按浏览器/Electron 实际支持挑）
const _PREFERRED_MIME = [
  "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm", "video/mp4",
];

/** 从「受支持的 mime 列表」或一个 isSupported(mime) 判定函数里挑最优容器。挑不到→null。 */
function chooseMimeType(support) {
  const isSup = (m) => {
    if (typeof support === "function") { try { return !!support(m); } catch (_e) { return false; } }
    if (Array.isArray(support)) return support.indexOf(m) !== -1;
    return false;
  };
  for (const m of _PREFERRED_MIME) if (isSup(m)) return m;
  return null;
}

function _pad(n) { return (n < 10 ? "0" : "") + n; }

/** 生成带时间戳的录制文件名，扩展名据 mime 推断。 */
function makeRecordingFilename(prefix, mime, when) {
  const d = when instanceof Date ? when : new Date(when || Date.now());
  const ts = `${d.getFullYear()}${_pad(d.getMonth() + 1)}${_pad(d.getDate())}-${_pad(d.getHours())}${_pad(d.getMinutes())}${_pad(d.getSeconds())}`;
  const ext = String(mime || "").indexOf("mp4") !== -1 ? "mp4" : "webm";
  return `${prefix || "remote-session"}-${ts}.${ext}`;
}

class RecordingController {
  constructor() {
    this.state = "idle";       // idle / recording / stopped
    this.bytes = 0;
    this.chunks = 0;
    this.startedAt = 0;
    this.stoppedAt = 0;
    this.mime = null;
    this.filename = null;
  }

  /** 开始录制（登记 mime/文件名/起始时间）。已在录则忽略。返回 { ok, reason }。 */
  start(mime, now) {
    if (this.state === "recording") return { ok: false, reason: "already_recording" };
    if (!mime) return { ok: false, reason: "no_mime" };
    now = now == null ? Date.now() : now;
    this.state = "recording";
    this.mime = mime;
    this.bytes = 0; this.chunks = 0;
    this.startedAt = now; this.stoppedAt = 0;
    this.filename = makeRecordingFilename("remote-session", mime, now);
    return { ok: true, reason: "started", filename: this.filename };
  }

  /** 每收到一个数据块调用（size 字节）。非录制态忽略。 */
  onChunk(size) {
    if (this.state !== "recording") return false;
    const n = Number(size);
    if (Number.isFinite(n) && n > 0) { this.bytes += n; this.chunks += 1; }
    return true;
  }

  /** 停止录制。返回汇总 { ok, filename, bytes, chunks, durationMs }。 */
  stop(now) {
    if (this.state !== "recording") return { ok: false, reason: "not_recording" };
    now = now == null ? Date.now() : now;
    this.state = "stopped";
    this.stoppedAt = now;
    return {
      ok: true, filename: this.filename, mime: this.mime,
      bytes: this.bytes, chunks: this.chunks,
      durationMs: Math.max(0, this.stoppedAt - this.startedAt),
    };
  }

  isRecording() { return this.state === "recording"; }

  /** 当前录制摘要（供 UI 显示）。 */
  summary(now) {
    now = now == null ? Date.now() : now;
    const dur = this.state === "recording" ? now - this.startedAt
              : this.state === "stopped" ? this.stoppedAt - this.startedAt : 0;
    return {
      state: this.state, bytes: this.bytes, chunks: this.chunks,
      durationMs: Math.max(0, dur), filename: this.filename, mime: this.mime,
    };
  }
}

module.exports = {
  CLIP_MAX_BYTES, hashText, ClipboardSync,
  chooseMimeType, makeRecordingFilename, RecordingController,
};
