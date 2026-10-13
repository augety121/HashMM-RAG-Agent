/**
 * desktop/client-validate.js — 客户端输入校验 + 错误归类（V103.90，客户端硬化第一块）。
 *
 * "皮毛"常见症状：连接/配置表单不校验就直接发请求，连不上只甩一句"失败"，用户不知道错在哪。
 * 大厂客户端的标配是：提交前**本地校验**（URL 格式、必填项、模型名），失败给**可操作的中文提示**；
 * 连接出错按 errno/状态码**归类**成人话（"后端没启动" vs "token 错了" vs "网络不通"）。
 *
 * 本模块是这套校验的纯逻辑核心，不碰 DOM/网络，便于单测；UI 调它拿 {ok, reason, normalized}。
 */
"use strict";

/** 规范化并校验后端 URL。返回 {ok, normalized?, reason?}。 */
function validateBackendUrl(input) {
  let s = String(input == null ? "" : input).trim();
  if (!s) return { ok: false, reason: "请填写后端地址" };
  // 容错：没写协议默认补 http://
  if (!/^https?:\/\//i.test(s)) s = "http://" + s;
  let u;
  try { u = new URL(s); } catch (_e) { return { ok: false, reason: "地址格式不对，应形如 http://127.0.0.1:6006" }; }
  if (!u.hostname) return { ok: false, reason: "地址缺少主机名" };
  // 主机名只允许 字母数字.-（或方括号 IPv6）；含非法字符（如 ! % 空格）判错
  const host = u.hostname;
  const isIpv6 = host.startsWith("[") && host.endsWith("]");
  if (!isIpv6 && !/^[a-zA-Z0-9.\-]+$/.test(host)) {
    return { ok: false, reason: "主机名含非法字符，应形如 127.0.0.1 或 api.example.com" };
  }
  if (u.port && !(Number(u.port) >= 1 && Number(u.port) <= 65535)) {
    return { ok: false, reason: "端口号应在 1~65535 之间" };
  }
  // 去掉末尾斜杠，统一形态
  let normalized = u.origin + (u.pathname === "/" ? "" : u.pathname.replace(/\/+$/, ""));
  return { ok: true, normalized };
}

/** 校验 API Key。required=false 时空值算合法（本地模型可不填）。 */
function validateApiKey(key, opts) {
  const o = opts || {};
  const s = String(key == null ? "" : key).trim();
  if (!s) return o.required ? { ok: false, reason: "请填写 API Key" } : { ok: true, normalized: "" };
  if (/\s/.test(s)) return { ok: false, reason: "API Key 不应包含空格，请确认完整复制" };
  if (s.length < 8) return { ok: false, reason: "API Key 看起来太短，请确认是否复制完整" };
  return { ok: true, normalized: s };
}

/** 校验模型名。 */
function validateModelName(name, opts) {
  const o = opts || {};
  const s = String(name == null ? "" : name).trim();
  if (!s) return o.required === false ? { ok: true, normalized: "" } : { ok: false, reason: "请填写模型名称" };
  if (s.length > 128) return { ok: false, reason: "模型名称过长" };
  return { ok: true, normalized: s };
}

/**
 * 聚合校验 LLM 配置。返回 {ok, errors:{field:reason}, normalized:{base,key,model}}。
 * keyRequired/modelRequired 控制必填（如自定义 OpenAI 兼容端点需 key；本地模型可不需要）。
 */
function validateLlmConfig(cfg, opts) {
  const o = opts || {};
  const c = cfg || {};
  const errors = {};
  const normalized = {};

  const vb = validateBackendUrl(c.base);
  if (!vb.ok) errors.base = vb.reason; else normalized.base = vb.normalized;

  const vk = validateApiKey(c.key, { required: !!o.keyRequired });
  if (!vk.ok) errors.key = vk.reason; else normalized.key = vk.normalized;

  const vm = validateModelName(c.model, { required: o.modelRequired !== false });
  if (!vm.ok) errors.model = vm.reason; else normalized.model = vm.normalized;

  return { ok: Object.keys(errors).length === 0, errors, normalized };
}

/** 校验要加入知识库的文件夹路径（基础合法性，不碰文件系统）。 */
function validateFolderPath(p) {
  const s = String(p == null ? "" : p).trim();
  if (!s) return { ok: false, reason: "未选择文件夹" };
  // 粗判：Windows 盘符路径 或 *nix 绝对路径 或 UNC
  const win = /^[a-zA-Z]:[\\/]/.test(s);
  const nix = s.startsWith("/");
  const unc = /^\\\\[^\\]+\\/.test(s);
  if (!win && !nix && !unc) return { ok: false, reason: "请使用绝对路径（如 C:\\\\docs 或 /home/user/docs）" };
  return { ok: true, normalized: s };
}

/**
 * 把连接异常归类成人话。入参可为 Error、{code|errno|status}、或字符串。
 * 返回 { kind, message, hint }。
 */
function classifyConnError(err) {
  const code = (err && (err.code || err.errno)) || "";
  const status = Number(err && err.status);
  const text = String((err && (err.message || err.reason)) || err || "").toLowerCase();

  const has = (...keys) => keys.some(k => String(code).toUpperCase() === k || text.includes(k.toLowerCase()));

  if (status === 401 || status === 403 || has("unauthorized", "forbidden")) {
    return { kind: "auth", message: "认证失败：token 或 API Key 不正确", hint: "检查连接令牌/密钥是否填对、是否过期" };
  }
  if (has("ECONNREFUSED") || text.includes("connection refused")) {
    return { kind: "refused", message: "连接被拒：后端没在该地址监听", hint: "确认后端已启动、地址和端口正确（本地后端可点「启动并进入」）" };
  }
  if (has("ETIMEDOUT", "ESOCKETTIMEDOUT") || text.includes("timeout")) {
    return { kind: "timeout", message: "连接超时：网络不通或后端无响应", hint: "检查网络/防火墙，或后端是否卡死" };
  }
  if (has("ENOTFOUND", "EAI_AGAIN") || text.includes("getaddrinfo")) {
    return { kind: "dns", message: "找不到主机：域名/地址解析失败", hint: "检查地址是否写错、DNS 是否正常" };
  }
  if (has("ECONNRESET", "EPIPE")) {
    return { kind: "reset", message: "连接被重置：链路中断", hint: "可能是代理/网络不稳定，稍后重试" };
  }
  if (has("CERT", "SELF_SIGNED", "UNABLE_TO_VERIFY") || text.includes("certificate")) {
    return { kind: "tls", message: "证书校验失败", hint: "若是自签名证书的私有部署，需在设置中信任该证书" };
  }
  if (status >= 500) {
    return { kind: "server", message: `后端内部错误（HTTP ${status}）`, hint: "查看后端日志定位异常" };
  }
  if (status === 404) {
    return { kind: "notfound", message: "接口不存在（HTTP 404）", hint: "后端版本可能与客户端不匹配" };
  }
  return { kind: "unknown", message: "连接失败" + (status ? `（HTTP ${status}）` : ""), hint: "检查地址、网络与后端状态" };
}

module.exports = {
  validateBackendUrl, validateApiKey, validateModelName,
  validateLlmConfig, validateFolderPath, classifyConnError,
};
