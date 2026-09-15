/**
 * desktop/filetransfer-extras.js — 远控文件传输扩展纯逻辑（V103.90）。
 *
 * 现有文件传输已能切块/收块/去重/进度/拖拽，缺的是**断点续传**与**速度/ETA**。本模块补这两块的
 * 纯逻辑核心（不动已测的 remote-filetransfer.js，零风险）：
 *   - 断点续传：算出接收端还缺哪些块（reconnect 后只重传缺块，而非整文件重来）；
 *   - 速度/ETA：按已传字节与耗时算瞬时/平均速率与剩余时间；
 *   - 人类可读格式化（字节/速率/时间）。
 *
 * 纯函数、不抛异常，便于沙箱单测。真实重传由调用方按缺块清单发送。
 */
"use strict";

/** 缺块计算：在 [0,totalChunks) 里，receivedSeqs 没有的就是缺块。 */
function missingChunks(receivedSeqs, totalChunks) {
  const have = new Set();
  for (const s of (receivedSeqs || [])) { const n = Number(s); if (Number.isFinite(n)) have.add(n); }
  const miss = [];
  for (let i = 0; i < (Number(totalChunks) || 0); i++) if (!have.has(i)) miss.push(i);
  return miss;
}

/** 续传计划：缺块数、占比、是否已完成。 */
function resumePlan(receivedSeqs, totalChunks) {
  const total = Number(totalChunks) || 0;
  const miss = missingChunks(receivedSeqs, total);
  const got = total - miss.length;
  return {
    complete: total > 0 && miss.length === 0,
    missing: miss,
    missingCount: miss.length,
    receivedCount: got,
    totalChunks: total,
    pct: total > 0 ? Math.round((got / total) * 100) : 0,
  };
}

/** 把缺块合并成连续区间，便于批量请求/展示（[0,1,2,5,6] → [[0,2],[5,6]]）。 */
function toRanges(seqs) {
  const a = [...new Set((seqs || []).map(Number).filter(Number.isFinite))].sort((x, y) => x - y);
  const ranges = [];
  let start = null, prev = null;
  for (const n of a) {
    if (start === null) { start = prev = n; continue; }
    if (n === prev + 1) { prev = n; continue; }
    ranges.push([start, prev]); start = prev = n;
  }
  if (start !== null) ranges.push([start, prev]);
  return ranges;
}

function formatBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
}

function formatSpeed(bytesPerSec) {
  const b = Number(bytesPerSec) || 0;
  return formatBytes(b) + "/s";
}

function formatEta(ms) {
  const raw = Number(ms);
  if (!isFinite(raw) || raw < 0) return "—";
  const s = Math.round(raw / 1000);
  if (s < 60) return s + " 秒";
  if (s < 3600) return Math.floor(s / 60) + " 分 " + (s % 60) + " 秒";
  return Math.floor(s / 3600) + " 时 " + Math.floor((s % 3600) / 60) + " 分";
}

/**
 * 传输统计：速率 + ETA。
 * @param bytesDone 已传字节
 * @param bytesTotal 总字节
 * @param elapsedMs 已耗时（ms）
 * @returns { pct, speed, speedLabel, etaMs, etaLabel, doneLabel, totalLabel }
 */
function transferStats(bytesDone, bytesTotal, elapsedMs) {
  const done = Math.max(0, Number(bytesDone) || 0);
  const total = Math.max(0, Number(bytesTotal) || 0);
  const ms = Math.max(0, Number(elapsedMs) || 0);
  const speed = ms > 0 ? done / (ms / 1000) : 0;     // bytes/s
  const remain = Math.max(0, total - done);
  const etaMs = speed > 0 ? (remain / speed) * 1000 : Infinity;
  return {
    pct: total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0,
    speed,
    speedLabel: formatSpeed(speed),
    etaMs,
    etaLabel: isFinite(etaMs) ? formatEta(etaMs) : "—",
    doneLabel: formatBytes(done),
    totalLabel: formatBytes(total),
  };
}

module.exports = {
  missingChunks, resumePlan, toRanges,
  formatBytes, formatSpeed, formatEta, transferStats,
};
