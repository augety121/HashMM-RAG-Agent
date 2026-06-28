/**
 * desktop/remote-quality.js — 远控自适应质量 + 编码偏好策略（V103.90，对标 UU 编码协商/自动降级）。
 *
 * 现状：质量是一个静态档位（fps + jpeg/kbps 三档映射），不随网络变化、也没有编码器偏好策略。
 * UU 的做法：① 多编码器探测 + 协商（NVENC/AMF/QuickSync/软编），② 老卡 H265 quirk（GTX
 * 6/7/8/9 与 Quadro 在**控制端**禁 H265 解码、被控端不受限），③ 网络变差时自动降帧/降码率。
 *
 * 在 Chromium WebRTC 栈里，硬件编码（VP8/VP9/H.264）本就由底层完成；我们能补的是上层策略：
 *   - 编码偏好排序（setCodecPreferences 用）+ 老卡 H265 quirk
 *   - 按 RTCStats（RTT/丢包/可用带宽）做**自适应升降档**（setParameters 用）
 *
 * 全是纯函数、不抛异常、可单测（tests-node/test_remote-quality.js）。
 */
"use strict";

// 质量档位（对齐 UU 的 fps 30/60/90/144 + 自定义码率思路）。index 越大越高清。
const TIERS = [
  { name: "省流",   maxFps: 15, bitrateKbps: 800,  scaleDown: 2.0 },
  { name: "流畅",   maxFps: 30, bitrateKbps: 2500, scaleDown: 1.5 },
  { name: "高清",   maxFps: 30, bitrateKbps: 4000, scaleDown: 1.0 },
  { name: "极清",   maxFps: 60, bitrateKbps: 8000, scaleDown: 1.0 },
  { name: "竞技",   maxFps: 144, bitrateKbps: 16000, scaleDown: 1.0 },
];
const DEFAULT_TIER = 2;   // 默认「高清」

function tierCount() { return TIERS.length; }
function clampTier(i) { return Math.min(TIERS.length - 1, Math.max(0, i | 0)); }
function tier(i) { return TIERS[clampTier(i)]; }

// ── 编码偏好 + 老卡 H265 quirk ──

// 老卡正则：GeForce GTX 6xx/7xx/8xx/9xx（含 GTX 1060 这类要排除——只点名 9xx 及以前）与 Quadro。
// 说明：GTX 10 系（Pascal）H265 解码正常，不在禁用列；这里只匹配 GTX 后三位为 6/7/8/9 开头的型号。
const _OLD_GTX = /\b(GTX\s?[6789]\d{2})\b/i;
const _QUADRO = /\bQuadro\b/i;

/**
 * 控制端（解码侧）是否应禁用 H265。对应 UU quirk：老 GTX/Quadro H265 解码有问题。
 * 仅作用于 role==="controller"（被控端编码不受限）。
 */
function shouldDisableH265Decode(gpuName, role) {
  if (role && role !== "controller") return false;
  const g = String(gpuName || "");
  if (_QUADRO.test(g)) return true;
  const m = g.match(_OLD_GTX);
  return !!m;
}

/**
 * 给 setCodecPreferences 用的编码偏好排序（codec mimeType 列表，越靠前越优先）。
 * 默认 H264 优先（跨端硬解兼容性最好），其后 VP9/VP8；H265 仅在两端都支持且非老卡时考虑。
 * @param available 形如 RTCRtpReceiver.getCapabilities('video').codecs 的列表（含 mimeType）
 */
function chooseCodecPreference(available, opts) {
  const o = opts || {};
  const disableH265 = !!o.disableH265Decode;
  const want = ["video/H264", "video/VP9", "video/VP8"];
  if (!disableH265 && o.peerSupportsH265) want.unshift("video/H265");
  const caps = Array.isArray(available) ? available : [];
  const byMime = (mime) => caps.filter(c => String(c.mimeType || "").toLowerCase() === mime.toLowerCase());
  const ordered = [];
  for (const m of want) ordered.push(...byMime(m));
  // 其余编解码（rtx/red/ulpfec 等）补在后面，避免丢失必要的辅助 codec
  const picked = new Set(ordered);
  for (const c of caps) if (!picked.has(c)) ordered.push(c);
  return ordered;
}

// ── 自适应升降档（按网络统计）──

// 阈值（可保守可激进；默认偏稳健，先保流畅再追画质）。
const _LOSS_BAD = 5.0;     // 丢包率(%) 高于此 → 降档
const _LOSS_GOOD = 1.0;    // 低于此且 RTT 好 → 可升档
const _RTT_BAD = 250;      // RTT(ms) 高于此 → 降档
const _RTT_GOOD = 120;     // 低于此 → 可升档

/**
 * 依据本次采样的网络统计，决定下一档位。纯函数。
 * @param stats { rttMs, packetLossPct, availableOutgoingBitrateKbps?, framesPerSecond? }
 * @param currentTierIndex 当前档位 index
 * @returns { tierIndex, changed, direction, reason, bitrateKbps, maxFps, scaleDown }
 */
function adaptQuality(stats, currentTierIndex) {
  const cur = clampTier(currentTierIndex == null ? DEFAULT_TIER : currentTierIndex);
  const s = stats || {};
  const loss = Number(s.packetLossPct);
  const rtt = Number(s.rttMs);
  const avail = Number(s.availableOutgoingBitrateKbps);

  let next = cur;
  let reason = "网络稳定，保持";
  let direction = "hold";

  const lossBad = Number.isFinite(loss) && loss >= _LOSS_BAD;
  const rttBad = Number.isFinite(rtt) && rtt >= _RTT_BAD;
  const lossGood = !Number.isFinite(loss) || loss <= _LOSS_GOOD;
  const rttGood = !Number.isFinite(rtt) || rtt <= _RTT_GOOD;

  if (lossBad || rttBad) {
    next = clampTier(cur - 1);
    direction = "down";
    reason = lossBad ? `丢包 ${loss.toFixed(1)}% 偏高，降档保流畅`
                     : `延迟 ${Math.round(rtt)}ms 偏高，降档保流畅`;
  } else if (lossGood && rttGood) {
    // 还要看可用带宽是否撑得起更高档的码率，避免升上去又被打回（抖动）
    const upTier = tier(cur + 1);
    if (!Number.isFinite(avail) || avail >= upTier.bitrateKbps * 0.9) {
      next = clampTier(cur + 1);
      if (next !== cur) { direction = "up"; reason = "网络良好，升档提画质"; }
    } else {
      reason = "网络良好但带宽不足以升档，保持";
    }
  }

  const t = tier(next);
  return {
    tierIndex: next, changed: next !== cur, direction, reason,
    name: t.name, bitrateKbps: t.bitrateKbps, maxFps: t.maxFps, scaleDown: t.scaleDown,
  };
}

/** 从 RTCStatsReport 萃取关键指标（在 host 渲染进程调用；纯解析，便于单测）。 */
function extractStats(reportEntries) {
  // reportEntries: Array<stat 对象>（report.forEach 收集而来）
  const out = { rttMs: NaN, packetLossPct: NaN, availableOutgoingBitrateKbps: NaN, framesPerSecond: NaN };
  let remoteInbound = null, outbound = null, candidatePair = null;
  for (const st of (reportEntries || [])) {
    if (!st || !st.type) continue;
    if (st.type === "remote-inbound-rtp") remoteInbound = st;
    else if (st.type === "outbound-rtp" && (st.kind === "video" || st.mediaType === "video")) outbound = st;
    else if (st.type === "candidate-pair" && (st.nominated || st.state === "succeeded")) candidatePair = st;
  }
  if (remoteInbound) {
    if (Number.isFinite(remoteInbound.roundTripTime)) out.rttMs = remoteInbound.roundTripTime * 1000;
    if (Number.isFinite(remoteInbound.fractionLost)) out.packetLossPct = remoteInbound.fractionLost * 100;
  }
  if (candidatePair) {
    if (!Number.isFinite(out.rttMs) && Number.isFinite(candidatePair.currentRoundTripTime)) {
      out.rttMs = candidatePair.currentRoundTripTime * 1000;
    }
    if (Number.isFinite(candidatePair.availableOutgoingBitrate)) {
      out.availableOutgoingBitrateKbps = candidatePair.availableOutgoingBitrate / 1000;
    }
  }
  if (outbound && Number.isFinite(outbound.framesPerSecond)) out.framesPerSecond = outbound.framesPerSecond;
  return out;
}

module.exports = {
  TIERS, DEFAULT_TIER, tierCount, clampTier, tier,
  shouldDisableH265Decode, chooseCodecPreference,
  adaptQuality, extractStats,
};
