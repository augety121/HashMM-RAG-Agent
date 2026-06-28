// desktop/services/remote-filetransfer.js
// 远程文件传输协议（纯逻辑层）—— 对标 UU 远程「文件传输：类型/大小无限制」。
//
// 设计：真正的文件字节走 viewer↔host 的 **WebRTC DataChannel**（P2P 直连，与屏幕视频同一条
// 通道体系，不经服务器中转——所以「大小无限制」且省服务器带宽）。本模块只放**纯逻辑**：
//   · 把文件切成定长块（chunk），生成有序的传输描述；
//   · offer / accept / reject / chunk / ack / done 的状态机；
//   · 进度计算与完整性校验（每块大小 + 总块数 + 简单校验和）。
// 这样协议本身可在不碰 fs / RTCDataChannel 的情况下单测；真实读写文件、发送 DataChannel
// 帧在 main.js / 渲染进程里接这套纯逻辑。
//
// 为什么不直接走服务器：服务器中转大文件既费带宽又有大小上限；P2P DataChannel 与 UU/向日葵
// 等商用方案做法一致——控制信令(小)走信令通道，数据(大)走 P2P。
"use strict";

const DEFAULT_CHUNK_SIZE = 64 * 1024;       // 64KB/块：DataChannel 友好（避免超过 SCTP 单消息上限）
const MAX_CHUNK_SIZE = 256 * 1024;

/** 轻量校验和（FNV-1a 32-bit），用于校验整文件字节未损坏。纯函数。 */
function checksum(buf) {
  let h = 0x811c9dc5;
  const bytes = Buffer.isBuffer(buf) ? buf : Buffer.from(String(buf));
  for (let i = 0; i < bytes.length; i++) {
    h ^= bytes[i];
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h >>> 0;
}

/**
 * 根据文件元信息构造一个传输描述（不读内容，仅算分块计划）。纯函数。
 * @param {{name:string, size:number, mime?:string}} file
 * @param {number} [chunkSize]
 * @returns {{id:string, name:string, size:number, mime:string, chunkSize:number, totalChunks:number}}
 */
function planTransfer(file, chunkSize = DEFAULT_CHUNK_SIZE) {
  const size = Math.max(0, Number(file && file.size) || 0);
  const cs = Math.max(1, Math.min(MAX_CHUNK_SIZE, chunkSize || DEFAULT_CHUNK_SIZE));
  const totalChunks = size === 0 ? 0 : Math.ceil(size / cs);
  return {
    id: `ft_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    name: String((file && file.name) || "file"),
    size,
    mime: String((file && file.mime) || "application/octet-stream"),
    chunkSize: cs,
    totalChunks,
  };
}

/** 把整文件 Buffer 切成有序块。纯函数。返回 [{seq, data}] 。 */
function sliceChunks(buffer, chunkSize = DEFAULT_CHUNK_SIZE) {
  const cs = Math.max(1, Math.min(MAX_CHUNK_SIZE, chunkSize || DEFAULT_CHUNK_SIZE));
  const out = [];
  const buf = Buffer.isBuffer(buffer) ? buffer : Buffer.from(buffer);
  for (let off = 0, seq = 0; off < buf.length; off += cs, seq++) {
    out.push({ seq, data: buf.subarray(off, Math.min(off + cs, buf.length)) });
  }
  return out;
}

/**
 * 接收端重组器：按 seq 收块、防重复、防越界，凑齐后拼成完整 Buffer 并校验。
 * 纯逻辑、无 IO——真实落盘由调用方在 onComplete 回调里做。
 */
class ChunkAssembler {
  /** @param {{id:string, size:number, totalChunks:number, expectedChecksum?:number}} meta */
  constructor(meta) {
    this.id = meta.id;
    this.size = meta.size;
    this.totalChunks = meta.totalChunks;
    this.expectedChecksum = meta.expectedChecksum;
    this.received = new Map();      // seq -> Buffer
    this.bytes = 0;
    this.done = false;
  }

  /** 收一块。返回 {ok, duplicate?, outOfRange?, complete?, progress} 。 */
  addChunk(seq, data) {
    if (this.done) return { ok: false, complete: true, progress: 1 };
    if (seq < 0 || seq >= this.totalChunks) {
      return { ok: false, outOfRange: true, progress: this.progress() };
    }
    if (this.received.has(seq)) {
      return { ok: true, duplicate: true, progress: this.progress() };
    }
    const buf = Buffer.isBuffer(data) ? data : Buffer.from(data);
    this.received.set(seq, buf);
    this.bytes += buf.length;
    const complete = this.received.size === this.totalChunks;
    if (complete) this.done = true;
    return { ok: true, complete, progress: this.progress() };
  }

  progress() {
    if (this.totalChunks === 0) return 1;
    return this.received.size / this.totalChunks;
  }

  /** 凑齐后拼接成完整 Buffer。未齐返回 null。校验和不匹配抛错（防损坏文件落盘）。 */
  assemble() {
    if (this.received.size !== this.totalChunks) return null;
    const parts = [];
    for (let seq = 0; seq < this.totalChunks; seq++) {
      const p = this.received.get(seq);
      if (!p) return null;          // 缺块
      parts.push(p);
    }
    const full = Buffer.concat(parts);
    if (this.expectedChecksum !== undefined && checksum(full) !== this.expectedChecksum) {
      throw new Error(`文件校验失败（${this.id}）：字节在传输中损坏`);
    }
    return full;
  }
}

/**
 * 传输安全策略：限制单文件大小 / 总并发，防止恶意端塞爆磁盘。纯函数判定。
 * @param {{size:number}} meta
 * @param {{maxFileSize?:number, maxConcurrent?:number, currentConcurrent?:number}} [policy]
 * @returns {{allowed:boolean, reason?:string}}
 */
function checkPolicy(meta, policy = {}) {
  const maxFileSize = policy.maxFileSize || 2 * 1024 * 1024 * 1024;   // 默认单文件上限 2GB
  const maxConcurrent = policy.maxConcurrent || 5;
  const cur = policy.currentConcurrent || 0;
  if ((meta && meta.size || 0) > maxFileSize) {
    return { allowed: false, reason: `文件超过上限（${Math.round(maxFileSize / 1024 / 1024)}MB）` };
  }
  if (cur >= maxConcurrent) {
    return { allowed: false, reason: `并发传输已达上限（${maxConcurrent}）` };
  }
  return { allowed: true };
}

module.exports = {
  checksum, planTransfer, sliceChunks, ChunkAssembler, checkPolicy,
  DEFAULT_CHUNK_SIZE, MAX_CHUNK_SIZE,
};
