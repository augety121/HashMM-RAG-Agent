/**
 * desktop/isolation/env-guard.js — 环境隔离守卫（V101）。
 *
 * 硬约束："软件安装别的东西，不能影响用户电脑的其他环境配置。" 这里把隔离策略**显式化 +
 * 可校验**：HashMM 只在自己的目录里读写，绝不动用户系统环境。
 *
 * 允许写入的根（仅这三类）：
 *   1. 安装目录    <installDir>      —— app 自身、runtime/、models/、HashMM Files 数据夹
 *   2. 用户数据    userData          —— 配置/日志/缓存（Electron 标准每应用隔离目录）
 *   3. 系统临时    tmpdir            —— 自删脚本等一次性文件
 *
 * 明确**不做**的事（保证不污染系统）：
 *   - 不改系统/用户 PATH 环境变量
 *   - 不动系统 Python / 不建全局 venv（runtime/ 用内嵌 Python，见 prepare-runtime.py）
 *   - 不全局 npm install（onnxruntime-node 等原生模块随 app 自带，asarUnpack 解包加载）
 *   - 不写系统级注册表（仅 HKCU 用户级卸载项，卸载时自删）
 *   - 不装系统服务 / 不改防火墙 / 不动其它软件目录
 *
 * 纯逻辑（路径包含判定 + 策略清单），可单测；运行期可用 assertContained 兜底。
 */
"use strict";
const path = require("path");

/** 规范化为小写、统一分隔符、去尾分隔符，便于包含判定（Windows 大小写不敏感）。 */
function _norm(p) {
  return String(p || "").replace(/[\\/]+/g, "\\").replace(/\\+$/, "").toLowerCase();
}

/**
 * 路径 target 是否落在 roots 任一根之内（含根自身）。
 * 用规范化前缀 + 分隔符边界判定，避免 C:\App 误配 C:\AppOther。
 */
function isContained(target, roots) {
  const t = _norm(target);
  if (!t) return false;
  for (const r of roots || []) {
    const rn = _norm(r);
    if (!rn) continue;
    if (t === rn) return true;
    if (t.startsWith(rn + "\\")) return true;
  }
  return false;
}

/** 取允许写入的根集合（安装目录 / userData / tmp）。 */
function allowedRoots({ installDir, userData, tmpDir }) {
  return [installDir, userData, tmpDir].filter(Boolean);
}

/** 运行期兜底：写入路径必须在允许根内，否则抛（防御性，挡住越界写）。 */
function assertContained(target, roots) {
  if (!isContained(target, roots)) {
    throw new Error(`隔离违规：路径越出 app 允许的目录范围：${target}`);
  }
  return target;
}

/** 隔离策略自检清单（给文档/诊断用）：每条 = { rule, ok:true }。 */
function policyChecklist() {
  return [
    { rule: "只写安装目录 / userData / tmp 三类根", ok: true },
    { rule: "不修改系统或用户 PATH", ok: true },
    { rule: "不动系统 Python，runtime 用内嵌 Python（无全局 venv）", ok: true },
    { rule: "原生模块随 app 自带（asarUnpack），不全局 npm install", ok: true },
    { rule: "仅 HKCU 用户级卸载注册项，卸载自删", ok: true },
    { rule: "不装系统服务 / 不改防火墙 / 不动其它软件目录", ok: true },
    { rule: "派生子进程使用净化环境（剔除敏感凭据键），不泄露无关密钥", ok: true },
    { rule: "日志写入前脱敏 PII（邮箱 / 手机 / 卡号 / 凭据）", ok: true },
  ];
}

/**
 * 检查一组计划写入的路径是否都合规，返回 { ok, violations }。
 * 供安装/运行流程在落盘前批量自检。
 */
function auditPaths(paths, roots) {
  const violations = [];
  for (const p of paths || []) if (!isContained(p, roots)) violations.push(p);
  return { ok: violations.length === 0, violations };
}

// ── 隐私加固（V108）：子进程环境净化 + 日志 PII 脱敏 ──

/** 敏感环境变量名匹配（凭据/密钥类）。派生子进程不必继承这些，缩小泄露面。 */
const SENSITIVE_ENV_RE = /(SECRET|TOKEN|PASSWORD|PASSWD|_PWD|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|SESSION|CREDENTIAL|AUTH)/i;

/**
 * 为派生子进程（内嵌 runtime / 一次性脚本）生成"净化后的环境"副本：
 * 剔除主进程里与运行无关的敏感凭据键，最小化泄露面（隐私加固）。
 * 不修改全局环境，只产出传给 child_process 的一次性对象。可用 keep 白名单保留个别键。
 */
function sanitizeEnvForChild(env, opts) {
  const src = env || {};
  const keepSet = new Set(((opts && opts.keep) || []).map((k) => String(k)));
  const out = {};
  for (const k of Object.keys(src)) {
    if (keepSet.has(k)) { out[k] = src[k]; continue; }
    if (SENSITIVE_ENV_RE.test(k)) continue; // 丢弃敏感键
    out[k] = src[k];
  }
  return out;
}

/** 日志脱敏（与后端 privacy.redact_pii 对齐）：掩码 JWT/密钥/邮箱/手机/卡号。永不抛错。 */
function redactSensitive(text) {
  if (!text) return text == null ? "" : String(text);
  try {
    let s = String(text);
    s = s.replace(/\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b/g, "<jwt>");
    s = s.replace(/(?:sk|pk|rk|ghp|gho|ghs|xox[baprs])[-_][A-Za-z0-9]{12,}/g, "<key>");
    s = s.replace(/\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g, "<email>");
    s = s.replace(/(?<!\d)1[3-9]\d{9}(?!\d)/g, "<phone>");
    s = s.replace(/(?<!\d)\d{13,19}(?!\d)/g, "<num>");
    return s;
  } catch (_e) {
    return String(text).slice(0, 200);
  }
}

module.exports = {
  isContained, allowedRoots, assertContained, policyChecklist, auditPaths,
  sanitizeEnvForChild, redactSensitive, SENSITIVE_ENV_RE,
};
