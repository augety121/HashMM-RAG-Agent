/**
 * desktop/llm-preflight.js — LLM 连通性预检纯逻辑（V103.90，配置页"测试模型"用）。
 *
 * "LLM 配错"是桌面端"用不了"的头号原因：地址错、key 错、模型名打错，但用户只在聊天失败时
 * 才撞见一句天书报错。大厂做法：配完点一下"测试"，立刻知道 通不通 / key对不对 / 模型在不在，
 * 并把端点支持的模型列出来供选择。
 *
 * 本模块是预检的纯逻辑核心（解析 OpenAI 兼容 /models 响应、把 HTTP/网络结果翻成结论），
 * 不发网络请求（请求在 main 进程做），便于沙箱单测。
 */
"use strict";

/**
 * 从 /models 响应里抽出模型 id 列表。兼容多种形态：
 *   {data:[{id}]}（OpenAI 标准）、[{id}]、{models:[{id|name}]}、{data:["str"]}、["str"]。
 * 解析不出→空数组。
 */
function parseModelsResponse(json) {
  if (!json) return [];
  let arr = null;
  if (Array.isArray(json)) arr = json;
  else if (Array.isArray(json.data)) arr = json.data;
  else if (Array.isArray(json.models)) arr = json.models;
  if (!arr) return [];
  const ids = [];
  for (const it of arr) {
    if (typeof it === "string") { if (it) ids.push(it); }
    else if (it && typeof it === "object") {
      const id = it.id || it.name || it.model;
      if (id) ids.push(String(id));
    }
  }
  // 去重保序
  return [...new Set(ids)];
}

/** 把 HTTP 状态码翻成 LLM 语境下的人话（key/端点/限流等）。 */
function classifyHttpStatus(status) {
  status = Number(status) || 0;
  if (status === 401 || status === 403) return { level: "error", message: `认证失败（HTTP ${status}）：API Key 不对或无权限`, hint: "检查 Key 是否复制完整、是否过期或欠费" };
  if (status === 404) return { level: "error", message: "端点不存在（HTTP 404）", hint: "Base URL 可能写错，应指向 OpenAI 兼容服务（通常以 /v1 结尾）" };
  if (status === 429) return { level: "warn", message: "被限流（HTTP 429）", hint: "Key 有效但触发速率限制，稍后再试" };
  if (status >= 500) return { level: "error", message: `服务端错误（HTTP ${status}）`, hint: "目标服务异常，稍后再试或换端点" };
  if (status >= 400) return { level: "error", message: `请求被拒（HTTP ${status}）`, hint: "检查 Base URL、Key 与请求路径" };
  return { level: "ok", message: "", hint: "" };
}

/** 把网络层 errno 翻成人话（与 client-validate 同口径，独立保留避免耦合）。 */
function classifyNetKind(kind, code) {
  const k = String(kind || "").toLowerCase();
  const c = String(code || "").toUpperCase();
  if (k === "refused" || c === "ECONNREFUSED") return { message: "连不上：该地址没有服务在监听", hint: "确认 Base URL 与端口正确、服务已启动" };
  if (k === "timeout" || c === "ETIMEDOUT") return { message: "连接超时", hint: "检查网络/防火墙，或目标服务是否在忙" };
  if (c === "ENOTFOUND" || c === "EAI_AGAIN") return { message: "域名解析失败", hint: "Base URL 的主机名可能写错" };
  if (c.indexOf("CERT") !== -1 || c.indexOf("SELF_SIGNED") !== -1) return { message: "证书校验失败", hint: "自签名证书的私有部署需信任证书" };
  return { message: "连接失败", hint: "检查 Base URL 与网络" };
}

/**
 * 综合生成预检结论（纯函数）。
 * @param r {
 *   reachable: bool,         // 是否拿到了 HTTP 响应
 *   status: number,          // HTTP 状态码（reachable 时）
 *   netKind, netCode,        // 不可达时的网络错误
 *   models: string[],        // 已解析的模型列表（成功时）
 *   wantedModel: string,     // 用户填的模型名
 *   parseFailed: bool,       // 拿到 200 但响应解析不出模型列表
 * }
 * @returns { ok, level:"ok"|"warn"|"error", message, hint, models, modelFound }
 */
function buildVerdict(r) {
  r = r || {};
  const models = Array.isArray(r.models) ? r.models : [];
  const wanted = String(r.wantedModel || "").trim();

  if (!r.reachable) {
    const n = classifyNetKind(r.netKind, r.netCode);
    return { ok: false, level: "error", message: n.message, hint: n.hint, models: [], modelFound: false };
  }
  const status = Number(r.status) || 0;
  if (status >= 400) {
    const s = classifyHttpStatus(status);
    return { ok: false, level: s.level, message: s.message, hint: s.hint, models, modelFound: false };
  }
  // 2xx：能连、key 基本有效（/models 通常需要鉴权）
  if (r.parseFailed || !models.length) {
    // 连通且鉴权过，但没列出模型（有些自建服务 /models 返回非标准）——不算失败，给提示
    return {
      ok: true, level: "warn",
      message: "已连通、鉴权通过，但未能列出模型清单",
      hint: "该服务的 /models 返回非标准格式，模型名是否正确需以实际调用为准",
      models, modelFound: false,
    };
  }
  if (wanted) {
    const found = models.some(m => m === wanted);
    if (found) return { ok: true, level: "ok", message: `连通正常，模型「${wanted}」可用`, hint: "", models, modelFound: true };
    return {
      ok: true, level: "warn",
      message: `连通正常，但模型「${wanted}」不在端点清单里`,
      hint: "请从下方可用模型中选择，或确认模型名拼写",
      models, modelFound: false,
    };
  }
  return { ok: true, level: "ok", message: `连通正常，发现 ${models.length} 个可用模型`, hint: "", models, modelFound: false };
}

module.exports = { parseModelsResponse, classifyHttpStatus, classifyNetKind, buildVerdict };
