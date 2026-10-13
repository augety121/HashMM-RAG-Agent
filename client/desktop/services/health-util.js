/**
 * desktop/services/health-util.js — 连接心跳判定纯函数（V101）。
 *
 * 修"离线时后端误报断开"：后端解析大文件（如 40s 解析年报，CPU 密集占住 GIL）期间，/api/health
 * 心跳排队**超时**——但 TCP 连接是被 uvicorn 接受了的，后端在听、只是忙。旧逻辑把"超时"和
 * "连接被拒"都当失败 → 误弹"后端连接中断"。这里区分二者：
 *   - ok        → 正常
 *   - busy      → 超时（后端在听但忙）→ 不弹掉线横幅
 *   - down      → 连接被拒/网络错误（后端真没了）→ 计入掉线，连续达阈值才弹
 * 纯逻辑可测。
 */
"use strict";

/** 把一次健康检查结果归类。result: { ok, kind }（kind 由 checkHealth 给：ok/http/timeout/refused）。 */
function classifyHealth(result) {
  if (result && result.ok) return "ok";
  if (result && result.kind === "timeout") return "busy"; // 在听但忙（解析大文件等）
  return "down"; // refused / 其它网络错误 = 真下线
}

/**
 * 推进心跳状态机。
 * @param {object} prev { downStreak, busyStreak }
 * @param {object} result 健康检查结果
 * @param {object} opts { downThreshold=2 }
 * @returns {{ downStreak, busyStreak, cls, showOffline, clearOffline }}
 *   showOffline：是否应显示掉线横幅（仅真下线连续达阈值）
 *   clearOffline：是否应移除掉线横幅（恢复正常）
 */
function nextHeartbeatState(prev, result, opts = {}) {
  const downThreshold = opts.downThreshold || 2;
  const cls = classifyHealth(result);
  let downStreak = (prev && prev.downStreak) || 0;
  let busyStreak = (prev && prev.busyStreak) || 0;
  if (cls === "ok") { downStreak = 0; busyStreak = 0; }
  else if (cls === "busy") { busyStreak++; /* downStreak 不动——忙不算掉线 */ }
  else { downStreak++; busyStreak = 0; }
  return {
    downStreak, busyStreak, cls,
    showOffline: cls === "down" && downStreak >= downThreshold,
    clearOffline: cls === "ok",
  };
}

module.exports = { classifyHealth, nextHeartbeatState };
