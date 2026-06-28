// desktop/services/remote-wol.js
// 远程开机（Wake-on-LAN）—— 对标 UU 远程「远程开机」。
//
// 原理：向局域网广播一个「魔术包」(magic packet)：6 字节 0xFF 同步头 + 目标网卡 MAC 地址
// 重复 16 次（共 102 字节），通过 UDP 端口 9（或 7）广播。支持 WOL 的网卡在主板通电、
// 系统关机/休眠时仍监听该包，命中即上电开机。这是跨网络远程控制的「先开机、再连」前置环节。
//
// 分层（与 remote-server.js 一致）：
//   1) buildMagicPacket / normalizeMac —— 纯函数，可单测，不依赖 dgram。
//   2) wake() —— 薄 IO 层，用 Node 内置 dgram 发 UDP 广播（无需任何 npm 依赖）。
//
// 跨网段说明：WOL 本质是二层广播，默认只在同一局域网生效。要跨网段「叫醒」需在目标网段
// 的路由器上配置「定向广播 / WOL 转发」，或经一台同网段的常开设备中继——这与 UU 等产品的
// 限制一致，不是本实现的缺陷。
"use strict";

const dgram = require("dgram");

const DEFAULT_PORT = 9;                 // WOL 常用端口 9（discard），部分设备用 7（echo）
const DEFAULT_BROADCAST = "255.255.255.255";

/**
 * 把任意常见写法的 MAC 规范化为 6 个字节。
 * 接受 "AA:BB:CC:DD:EE:FF" / "aa-bb-cc-dd-ee-ff" / "AABBCCDDEEFF" 等。
 * 非法（长度不对/含非十六进制）抛 Error——调用方据此给用户明确报错，而不是静默发错包。
 * @param {string} mac
 * @returns {Buffer} 6 字节
 */
function normalizeMac(mac) {
  const hex = String(mac || "").replace(/[^0-9a-fA-F]/g, "");
  if (hex.length !== 12) {
    throw new Error(`非法 MAC 地址（应为 6 字节 / 12 位十六进制）：${mac}`);
  }
  const buf = Buffer.alloc(6);
  for (let i = 0; i < 6; i++) {
    buf[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return buf;
}

/**
 * 构造 WOL 魔术包：6×0xFF + 16×MAC = 102 字节。纯函数。
 * @param {string} mac
 * @returns {Buffer} 102 字节魔术包
 */
function buildMagicPacket(mac) {
  const macBuf = normalizeMac(mac);
  const packet = Buffer.alloc(6 + 16 * 6);   // 102
  packet.fill(0xff, 0, 6);                   // 同步头
  for (let i = 0; i < 16; i++) {
    macBuf.copy(packet, 6 + i * 6);          // MAC 重复 16 次
  }
  return packet;
}

/**
 * 发送魔术包叫醒目标设备。返回 Promise。
 * @param {string} mac 目标网卡 MAC
 * @param {{port?:number, address?:string, count?:number}} [opts]
 *   port: UDP 端口（默认 9）；address: 广播地址（默认 255.255.255.255 或子网定向广播如 192.168.1.255）；
 *   count: 重复发送次数（默认 3，丢包时提高命中率）。
 * @returns {Promise<{sent:number, bytes:number}>}
 */
function wake(mac, opts = {}) {
  const port = opts.port || DEFAULT_PORT;
  const address = opts.address || DEFAULT_BROADCAST;
  const count = Math.max(1, Math.min(10, opts.count || 3));
  const packet = buildMagicPacket(mac);      // 先构包：MAC 非法会在这里同步抛错

  return new Promise((resolve, reject) => {
    const socket = dgram.createSocket("udp4");
    let remaining = count;
    let bytesTotal = 0;
    let settled = false;

    const done = (err) => {
      if (settled) return;
      settled = true;
      try { socket.close(); } catch (_e) { /* ignore */ }
      if (err) reject(err); else resolve({ sent: count, bytes: bytesTotal });
    };

    socket.on("error", (err) => done(err));
    socket.bind(() => {
      try { socket.setBroadcast(true); } catch (e) { return done(e); }
      const sendOne = () => {
        socket.send(packet, 0, packet.length, port, address, (err, bytes) => {
          if (err) return done(err);
          bytesTotal += bytes || 0;
          remaining -= 1;
          if (remaining <= 0) return done(null);
          setTimeout(sendOne, 120);          // 间隔重发，避免突发丢包
        });
      };
      sendOne();
    });
  });
}

module.exports = { buildMagicPacket, normalizeMac, wake, DEFAULT_PORT, DEFAULT_BROADCAST };
