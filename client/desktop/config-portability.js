/**
 * desktop/config-portability.js — 配置导入导出纯逻辑（V103.90）。
 *
 * 应用配置散在两处：主进程 hashmm-config.json（后端列表/url/token）+ 渲染层 localStorage
 * （LLM、视觉模型配置）。本模块把两边合成一份**可移植**的配置文件，并支持：
 *   - 导出：含密钥（自用备份）/ 脱敏（分享给同事，抹掉 token 与 apiKey）；
 *   - 导入：校验版本与结构，再拆回主配置补丁 + 渲染配置。
 *
 * 纯函数、不抛异常，便于沙箱单测。真实读写文件/localStorage 由调用方做。
 */
"use strict";

const EXPORT_VERSION = 1;
const REDACTED = "";

/**
 * 组装导出对象。
 * @param parts { mainConfig, llmCfg, visionCfg, preferences }
 * @param opts { includeSecrets:boolean }
 */
function buildExport(parts, opts) {
  parts = parts || {};
  const includeSecrets = !!(opts && opts.includeSecrets);
  const main = parts.mainConfig || {};
  const llm = parts.llmCfg || {};
  const vis = parts.visionCfg || {};

  // 后端列表：当前 + recent，去重（按 url）
  const backends = [];
  const seen = new Set();
  const pushBackend = (url, token) => {
    const u = String(url || "").trim();
    if (!u || seen.has(u)) return;
    seen.add(u);
    backends.push(includeSecrets ? { url: u, token: token || "" } : { url: u });
  };
  if (main.url) pushBackend(main.url, main.token);
  for (const r of (main.recent || [])) pushBackend(r.url, r.token);

  return {
    app: "HashMM",
    kind: "hashmm-config-export",
    version: EXPORT_VERSION,
    exportedAt: new Date().toISOString(),
    secretsIncluded: includeSecrets,
    backends,
    llm: {
      base: llm.base || "",
      model: llm.model || "",
      key: includeSecrets ? (llm.key || "") : REDACTED,
    },
    vision: {
      base: vis.base || "",
      model: vis.model || "",
      key: includeSecrets ? (vis.key || "") : REDACTED,
    },
    preferences: parts.preferences || {},
  };
}

/** 序列化为带缩进的 JSON 字符串（写文件用）。 */
function stringifyExport(parts, opts) {
  try { return JSON.stringify(buildExport(parts, opts), null, 2); }
  catch (_e) { return "{}"; }
}

/**
 * 校验并解析导入内容。入参可为字符串或对象。
 * @returns { ok, data?, errors?:[], warnings?:[] }
 */
function parseImport(input) {
  let obj = input;
  if (typeof input === "string") {
    try { obj = JSON.parse(input); } catch (_e) { return { ok: false, errors: ["不是合法的 JSON 文件"] }; }
  }
  if (!obj || typeof obj !== "object") return { ok: false, errors: ["配置内容为空或格式不对"] };
  if (obj.kind !== "hashmm-config-export") return { ok: false, errors: ["不是 HashMM 导出的配置文件"] };
  if (Number(obj.version) > EXPORT_VERSION) return { ok: false, errors: [`配置版本(${obj.version})高于当前客户端支持(${EXPORT_VERSION})，请升级客户端`] };

  const warnings = [];
  const backends = Array.isArray(obj.backends)
    ? obj.backends.filter(b => b && b.url).map(b => ({ url: String(b.url), token: typeof b.token === "string" ? b.token : "" }))
    : [];
  if (!backends.length) warnings.push("配置里没有后端地址");
  if (obj.secretsIncluded === false) warnings.push("该配置为脱敏导出，密钥/令牌需手动重新填写");

  const llm = obj.llm || {};
  const vision = obj.vision || {};
  return {
    ok: true,
    warnings,
    data: {
      backends,
      llm: { base: String(llm.base || ""), model: String(llm.model || ""), key: typeof llm.key === "string" ? llm.key : "" },
      vision: { base: String(vision.base || ""), model: String(vision.model || ""), key: typeof vision.key === "string" ? vision.key : "" },
      preferences: (obj.preferences && typeof obj.preferences === "object") ? obj.preferences : {},
      secretsIncluded: !!obj.secretsIncluded,
    },
  };
}

/**
 * 把导入数据拆成「主配置补丁」+「渲染配置」，供两边分别落地。
 * @returns { mainPatch, llmCfg, visionCfg, preferences }
 */
function splitForApply(data) {
  data = data || {};
  const backends = data.backends || [];
  const first = backends[0] || {};
  // recent：保留全部后端（去重），首个作为当前
  const recent = [];
  const seen = new Set();
  for (const b of backends) { if (b.url && !seen.has(b.url)) { seen.add(b.url); recent.push({ url: b.url, token: b.token || "", ts: Date.now() }); } }
  return {
    mainPatch: { url: first.url || "", token: first.token || "", recent: recent.slice(0, 10) },
    llmCfg: data.llm || {},
    visionCfg: data.vision || {},
    preferences: data.preferences || {},
  };
}

module.exports = { EXPORT_VERSION, buildExport, stringifyExport, parseImport, splitForApply };
