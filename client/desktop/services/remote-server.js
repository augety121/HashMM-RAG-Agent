// desktop/services/remote-server.js
// 远程传输（远程桌面）服务端 —— 零依赖 WebSocket（Node 内置 http+crypto 手写 RFC6455 握手与帧），
// 不引入 `ws`，因而**无需 npm install / 重新 rebuild**。
//
// 分层（每层都可独立测，传输/协议层在 127.0.0.1 loopback 上端到端可测）：
//   1) WS 编解码：acceptKey / encodeFrame / decodeFrames —— 纯函数。
//   2) 会话与配对：连上先要 6 位授权码（复用 remote-pairing 的 PairingManager），
//      校验通过才下发 token 并开始推屏；错误次数受限、码会过期（防穷举/防重放）。
//   3) 媒体与输入：截屏帧推送 + 输入事件注入，均以**注入式回调**接入（captureFrame / injectInput），
//      故 main.js 接真实 desktopCapturer + cu-driver，单测接假回调。
//
// 协议（控制消息走 WS text=JSON；屏幕帧走 WS binary=JPEG）：
//   服务端→端：{type:"hello", needPair:true, name}              连上即发
//             {type:"paired", token}                            配对成功
//             {type:"pairFail", reason, remainingAttempts}      配对失败
//             {type:"meta", sw, sh, fw, fh}                     屏幕/帧尺寸（端据此换算坐标）
//             <binary JPEG>                                     一帧画面
//             {type:"pong", t}                                  心跳回应
//   端→服务端：{type:"pair", code:"123456"}
//             {type:"input", action, x, y, x2, y2, text, keys, scroll_direction, scroll_amount}
//                                                                x/y 为 0..1000 归一坐标（与 computer 工具一致）
//             {type:"ping", t}
"use strict";

const http = require("http");
const crypto = require("crypto");
const { PairingManager } = require("./remote-pairing");

const WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

/** RFC6455 握手应答 key：base64(sha1(clientKey + GUID))。 */
function acceptKey(clientKey) {
  return crypto.createHash("sha1").update(String(clientKey) + WS_GUID).digest("base64");
}

/** 编码一帧服务端→端（不掩码）。opcode: 0x1 text, 0x2 binary, 0x8 close, 0x9 ping, 0xA pong。 */
function encodeFrame(payload, opcode = 0x1) {
  const data = Buffer.isBuffer(payload) ? payload : Buffer.from(String(payload), "utf8");
  const len = data.length;
  let header;
  if (len < 126) {
    header = Buffer.alloc(2);
    header[1] = len;
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header[1] = 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[1] = 127;
    // 高 32 位写 0（单帧不超过 4GiB，足够），低 32 位写长度
    header.writeUInt32BE(0, 2);
    header.writeUInt32BE(len >>> 0, 6);
  }
  header[0] = 0x80 | (opcode & 0x0f); // FIN=1
  return Buffer.concat([header, data]);
}

/**
 * 从累积缓冲里解析出尽可能多的完整帧（端→服务端必须掩码）。
 * @returns {{frames:{opcode:number,payload:Buffer}[], rest:Buffer}}
 */
function decodeFrames(buf) {
  const frames = [];
  let off = 0;
  while (off + 2 <= buf.length) {
    const b0 = buf[off];
    const b1 = buf[off + 1];
    const opcode = b0 & 0x0f;
    const masked = (b1 & 0x80) !== 0;
    let len = b1 & 0x7f;
    let p = off + 2;
    if (len === 126) {
      if (p + 2 > buf.length) break;
      len = buf.readUInt16BE(p); p += 2;
    } else if (len === 127) {
      if (p + 8 > buf.length) break;
      // 只取低 32 位（够用）；高位假定为 0
      const hi = buf.readUInt32BE(p); const lo = buf.readUInt32BE(p + 4); p += 8;
      len = hi * 4294967296 + lo;
    }
    let maskKey = null;
    if (masked) {
      if (p + 4 > buf.length) break;
      maskKey = buf.slice(p, p + 4); p += 4;
    }
    if (p + len > buf.length) break; // 半帧，等更多字节
    let payload = buf.slice(p, p + len);
    if (masked && maskKey) {
      const out = Buffer.allocUnsafe(len);
      for (let i = 0; i < len; i++) out[i] = payload[i] ^ maskKey[i & 3];
      payload = out;
    }
    frames.push({ opcode, payload });
    off = p + len;
  }
  return { frames, rest: buf.slice(off) };
}

/** 单个已升级连接的会话封装：缓冲拆帧、配对状态、收发。 */
class Conn {
  constructor(socket, server) {
    this.socket = socket;
    this.server = server;
    this.authed = false;
    this.buf = Buffer.alloc(0);
    this.alive = true;
    socket.on("data", (chunk) => this._onData(chunk));
    socket.on("close", () => this._onClose());
    socket.on("error", () => this._onClose());
  }
  sendText(obj) { try { if (this.alive) this.socket.write(encodeFrame(JSON.stringify(obj), 0x1)); } catch (_e) {} }
  sendBinary(buf) { try { if (this.alive && this.authed) this.socket.write(encodeFrame(buf, 0x2)); } catch (_e) {} }
  close() { try { this.socket.write(encodeFrame(Buffer.alloc(0), 0x8)); } catch (_e) {} try { this.socket.end(); } catch (_e) {} this._onClose(); }
  _onClose() {
    if (!this.alive) return;
    this.alive = false;
    this.server._drop(this);
  }
  _onData(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    const { frames, rest } = decodeFrames(this.buf);
    this.buf = rest;
    for (const f of frames) {
      if (f.opcode === 0x8) { this.close(); return; }       // close
      if (f.opcode === 0x9) { try { this.socket.write(encodeFrame(f.payload, 0xA)); } catch (_e) {} continue; } // ping→pong
      if (f.opcode === 0xA) continue;                        // pong
      if (f.opcode === 0x1) {                                // text=JSON 控制消息
        let msg = null;
        try { msg = JSON.parse(f.payload.toString("utf8")); } catch (_e) { continue; }
        this.server._onMessage(this, msg);
      }
      // binary from client 暂不使用
    }
  }
}

class RemoteServer {
  /**
   * @param {{
   *   captureFrame?: ()=>Promise<Buffer|null>,  // 返回一帧 JPEG（宿主接 desktopCapturer）
   *   injectInput?: (ev)=>Promise<void>|void,   // 注入一次输入（宿主接 cu-driver）
   *   frameMeta?: ()=>{sw:number,sh:number,fw:number,fh:number},  // 屏幕/帧尺寸（坐标换算）
   *   pairing?: PairingManager,                 // 注入便于测试；默认自建
   *   trustedDevices?: {verify:(id:string,token:string)=>boolean,issue:(id:string)=>string|null},
   *   fps?: number,                             // 推流帧率（默认 8，远程桌面够用、带宽友好）
   *   name?: string,                            // 宿主显示名
   * }} [opts]
   */
  constructor(opts = {}) {
    this.captureFrame = opts.captureFrame || (async () => null);
    this.injectInput = opts.injectInput || (() => {});
    this.frameMeta = opts.frameMeta || (() => ({ sw: 0, sh: 0, fw: 0, fh: 0 }));
    this.pairing = opts.pairing || new PairingManager();
    this.trustedDevices = opts.trustedDevices || null;
    this.fps = Math.max(1, Math.min(30, opts.fps || 8));
    this.name = opts.name || "HashMM";
    this.conns = new Set();
    this.server = null;
    this.port = 0;
    this._timer = null;
    this._sending = false;
    // ── WebRTC 信令（二期：P2P 视频）──
    // 宿主端的「投屏渲染进程」(host-renderer) 也作为一个 WS 客户端连进来，凭 hostToken 注册为 host；
    // 服务端只在 viewer 与 host 之间**中继** offer/answer/ICE，真正的视频流是 viewer 与 host 直连 P2P。
    this.hostConn = null;                 // 已注册的投屏渲染进程连接
    this._vidSeq = 1;                     // 给每个 viewer 连接分配的会话 id
    this.hostToken = opts.hostToken || crypto.randomBytes(16).toString("hex");
    // ICE：默认只用免费公共 STUN（绝大多数家庭网络靠 STUN 即可 P2P 打通）；
    // 对称 NAT 等少数情况需 TURN 中继——可由 setIceServers 注入（含免费公共 TURN）。
    this.iceServers = Array.isArray(opts.iceServers) && opts.iceServers.length
      ? opts.iceServers
      : [{ urls: "stun:stun.l.google.com:19302" }, { urls: "stun:stun1.l.google.com:19302" }];
    this.viewerHtml = opts.viewerHtml || null;   // 查看端网页（main.js 读盘注入），GET / 直接吐出
    this.jpegQuality = Math.max(30, Math.min(95, opts.jpegQuality || 70));   // V103.51 MJPEG 画质（真彩≈高质量）
  }

  /** 运行时更新 ICE 服务器（用户在设置里填了自己的/免费 TURN 后调用）。 */
  setIceServers(arr) { if (Array.isArray(arr) && arr.length) this.iceServers = arr; }

  /** V103.51 运行时更新画质：fps（推流帧率）+ jpeg（MJPEG 质量）。变更 fps 时重启推流定时器。 */
  setQuality(q) {
    const fps = Math.max(1, Math.min(30, Number(q && q.fps) || this.fps));
    const jpeg = Math.max(30, Math.min(95, Number(q && q.jpeg) || this.jpegQuality));
    this.jpegQuality = jpeg;
    if (fps !== this.fps) {
      this.fps = fps;
      if (this._timer) { clearInterval(this._timer); this._timer = null; this._startLoop(); }
    }
    return { fps: this.fps, jpeg: this.jpegQuality };
  }

  /** 启动 HTTP/WS 服务。host 默认 0.0.0.0（同局域网设备可连）。返回 {ok, port} 或 {ok:false,error}。 */
  start(port = 17690, host = "0.0.0.0") {
    return new Promise((resolve) => {
      if (this.server) return resolve({ ok: true, port: this.port });
      const srv = http.createServer((req, res) => this._onHttp(req, res));
      srv.on("upgrade", (req, socket) => this._onUpgrade(req, socket));
      srv.on("error", (e) => resolve({ ok: false, error: e.message }));
      srv.listen(port, host, () => {
        this.server = srv;
        this.port = srv.address().port;
        this._startLoop();
        resolve({ ok: true, port: this.port });
      });
    });
  }

  /** 停止服务并断开所有连接，作废当前授权码。 */
  stop() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    for (const c of Array.from(this.conns)) { try { c.close(); } catch (_e) {} }
    this.conns.clear();
    try { this.pairing.revoke(); } catch (_e) {}
    if (this.server) { try { this.server.close(); } catch (_e) {} this.server = null; }
  }

  /** 签发新授权码（宿主 UI 点「显示配对码」时调用）。 */
  issueCode() { return this.pairing.issue(); }
  /** 当前授权码展示信息（剩余有效期等）。 */
  currentCode() { return this.pairing.current(); }
  status() {
    const viewers = Array.from(this.conns).filter(c => c.role === "viewer");
    return { running: !!this.server, port: this.port, clients: viewers.length,
             paired: viewers.filter(c => c.authed).length,
             hostConnected: !!(this.hostConn && this.hostConn.alive),
             webrtcActive: viewers.filter(c => c.rtcActive).length };
  }

  /** 普通 HTTP：GET / 吐出查看端网页（控制方浏览器打开即用，免安装）；其余 404。 */
  _onHttp(req, res) {
    const url = (req.url || "/").split("?")[0];
    if (req.method === "GET" && (url === "/" || url === "/index.html" || url === "/viewer" || url === "/viewer.html")) {
      if (this.viewerHtml) {
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
        res.end(this.viewerHtml);
      } else {
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        res.end("<!doctype html><meta charset=utf-8><title>HashMM 远程</title><body style='font-family:sans-serif;padding:2rem'>查看端页面未注入。请在宿主端升级到带 remote-viewer.html 的版本。</body>");
      }
      return;
    }
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Not Found");
  }

  _onUpgrade(req, socket) {
    const key = req.headers["sec-websocket-key"];
    if ((req.headers.upgrade || "").toLowerCase() !== "websocket" || !key) {
      try { socket.write("HTTP/1.1 400 Bad Request\r\n\r\n"); socket.destroy(); } catch (_e) {}
      return;
    }
    const resHeaders = [
      "HTTP/1.1 101 Switching Protocols",
      "Upgrade: websocket",
      "Connection: Upgrade",
      "Sec-WebSocket-Accept: " + acceptKey(key),
      "\r\n",
    ].join("\r\n");
    try { socket.write(resHeaders); } catch (_e) { try { socket.destroy(); } catch (_e2) {} return; }
    try { socket.setNoDelay(true); } catch (_e) {}
    const conn = new Conn(socket, this);
    conn.vid = this._vidSeq++;       // 会话 id（信令中继按它定位 viewer）
    conn.role = "viewer";            // 默认查看端；投屏渲染进程会 hostRegister 改成 host
    this.conns.add(conn);
    // hello 里带上 ICE 配置，viewer 据此建 RTCPeerConnection
    conn.sendText({ type: "hello", needPair: true, name: this.name, iceServers: this.iceServers });
  }

  _drop(conn) {
    this.conns.delete(conn);
    if (conn === this.hostConn) { this.hostConn = null; return; }   // 投屏进程掉线
    if (conn.role === "viewer" && this.hostConn && this.hostConn.alive) {
      try { this.hostConn.sendText({ type: "viewerLeft", vid: conn.vid }); } catch (_e) {}
    }
  }

  _onMessage(conn, msg) {
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "ping") { conn.sendText({ type: "pong", t: msg.t }); return; }
    if (msg.type === "resume") {
      const trusted = !!(this.trustedDevices && this.trustedDevices.verify(msg.deviceId, msg.trustToken));
      if (!trusted) { conn.sendText({ type: "resumeRejected", reason: "untrusted" }); return; }
      conn.authed = true;
      conn.deviceId = String(msg.deviceId || "").slice(0, 128);
      conn.sendText({ type: "paired", trusted: true, resumed: true });
      try { conn.sendText(Object.assign({ type: "meta" }, this.frameMeta())); } catch (_e) {}
      if (this.hostConn && this.hostConn.alive) {
        try { this.hostConn.sendText({ type: "viewerJoined", vid: conn.vid }); } catch (_e) {}
      }
      return;
    }
    if (msg.type === "pair") {
      const r = this.pairing.verify(msg.code);
      if (r.ok) {
        conn.authed = true;
        conn.deviceId = String(msg.deviceId || "").slice(0, 128);
        const trustToken = this.trustedDevices ? this.trustedDevices.issue(conn.deviceId) : null;
        conn.sendText({ type: "paired", token: r.token, trustToken: trustToken || undefined,
          deviceId: conn.deviceId || undefined, trusted: !!trustToken });
        try { conn.sendText(Object.assign({ type: "meta" }, this.frameMeta())); } catch (_e) {}
        // 配对成功 → 若投屏渲染进程在线，通知它「有新查看端就绪」，由 host 发起 WebRTC offer。
        if (this.hostConn && this.hostConn.alive) {
          try { this.hostConn.sendText({ type: "viewerJoined", vid: conn.vid }); } catch (_e) {}
        }
      } else {
        conn.sendText({ type: "pairFail", reason: r.reason, remainingAttempts: r.remainingAttempts });
      }
      return;
    }
    if (msg.type === "input") {
      if (!conn.authed) return;                 // 未配对者的输入一律忽略
      try { this.injectInput(msg); } catch (_e) {}
      return;
    }
    // viewer 的 WebRTC 视频已接通 → 暂停给它推 MJPEG（省带宽）；断开/降级则恢复
    if (msg.type === "rtcOn") { conn.rtcActive = true; return; }
    if (msg.type === "rtcOff") { conn.rtcActive = false; return; }
    // ── 宿主投屏渲染进程注册（凭 hostToken；远程 viewer 不知此 token，无法冒充）──
    if (msg.type === "hostRegister") {
      if (String(msg.token || "") !== this.hostToken) { conn.sendText({ type: "hostRejected" }); return; }
      conn.role = "host"; conn.authed = true; this.hostConn = conn;
      conn.sendText({ type: "hostReady", iceServers: this.iceServers });
      // 把已在线、已配对的 viewer 补发给 host，让它给每个都建 PC
      for (const c of this.conns) {
        if (c !== conn && c.role === "viewer" && c.authed) {
          try { conn.sendText({ type: "viewerJoined", vid: c.vid }); } catch (_e) {}
        }
      }
      return;
    }
    // ── WebRTC 信令中继：viewer ⇄ host 之间转发 offer/answer/ICE（真正视频是 P2P 直连）──
    if (msg.type === "rtcSignal") {
      if (conn.role === "host") {
        // host → 指定 viewer
        const target = Array.from(this.conns).find(c => c.vid === msg.vid && c.role === "viewer");
        if (target && target.alive) target.sendText({ type: "rtcSignal", kind: msg.kind, data: msg.data });
      } else {
        // viewer → host（必须已配对，未配对不允许参与信令）
        if (!conn.authed) return;
        if (this.hostConn && this.hostConn.alive) {
          this.hostConn.sendText({ type: "rtcSignal", vid: conn.vid, kind: msg.kind, data: msg.data });
        } else {
          // 投屏进程未就绪 → 告知 viewer 回退 MJPEG
          conn.sendText({ type: "rtcUnavailable" });
        }
      }
      return;
    }
  }

  _startLoop() {
    const interval = Math.round(1000 / this.fps);
    this._timer = setInterval(() => this._tick(), interval);
  }

  async _tick() {
    if (this._sending) return;                  // 上一帧没发完不叠帧（防积压）
    // 只给「已配对、且未走 WebRTC」的 viewer 推 MJPEG；host 投屏进程与 WebRTC 已接通者都不推。
    const targets = Array.from(this.conns).filter(c => c.role === "viewer" && c.authed && c.alive && !c.rtcActive);
    if (!targets.length) return;
    this._sending = true;
    try {
      const jpg = await this.captureFrame();
      if (jpg && jpg.length) for (const c of targets) c.sendBinary(jpg);
    } catch (_e) { /* 截屏失败这一帧跳过，不崩 */ }
    this._sending = false;
  }
}

module.exports = { RemoteServer, Conn, acceptKey, encodeFrame, decodeFrames, WS_GUID };
