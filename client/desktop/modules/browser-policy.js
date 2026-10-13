/**
 * Per-site policy for the controlled desktop browser.
 *
 * This module is deliberately Electron-free so URL validation, policy
 * decisions and persistence can be regression-tested with plain Node.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const VERSION = 1;
const MAX_HOSTS = 500;
const HARD_BLOCKED_HOSTS = new Set([
  "169.254.169.254",        // cloud instance metadata
  "100.100.100.200",       // Alibaba Cloud instance metadata
  "fd00:ec2::254",         // AWS IMDS IPv6
]);

function normalizeHost(value) {
  let host = String(value || "").trim().toLowerCase();
  if (host.startsWith("[") && host.endsWith("]")) host = host.slice(1, -1);
  host = host.replace(/\.+$/, "");
  if (!host || host.length > 253 || /[\s/@\\]/.test(host)) return "";
  return host;
}

function parseBrowserUrl(value) {
  let input = String(value || "").trim();
  if (!input) return { ok: false, error: "网址为空" };
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(input)) input = "https://" + input;
  let url;
  try { url = new URL(input); }
  catch (_e) { return { ok: false, error: "网址格式无效" }; }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, error: "仅允许 http/https 网址" };
  }
  if (url.username || url.password) {
    return { ok: false, error: "网址中不得携带用户名或密码" };
  }
  const host = normalizeHost(url.hostname);
  if (!host) return { ok: false, error: "网址主机名无效" };
  if (HARD_BLOCKED_HOSTS.has(host)) {
    return { ok: false, error: "已阻止云主机元数据地址" };
  }
  url.hash = "";
  return { ok: true, url: url.toString(), host, origin: url.origin };
}

function _hostList(value) {
  const seen = new Set();
  for (const item of Array.isArray(value) ? value : []) {
    const host = normalizeHost(item);
    if (host) seen.add(host);
    if (seen.size >= MAX_HOSTS) break;
  }
  return Array.from(seen).sort();
}

function normalizePolicy(value) {
  const raw = value && typeof value === "object" ? value : {};
  const allow = _hostList(raw.allow);
  const allowSet = new Set(allow);
  const block = _hostList(raw.block).filter((host) => !allowSet.has(host));
  return {
    version: VERSION,
    allow,
    block,
    updated_at: Number.isFinite(Number(raw.updated_at)) ? Number(raw.updated_at) : 0,
  };
}

function decideNavigation(value, policy, temporaryHosts) {
  const parsed = parseBrowserUrl(value);
  if (!parsed.ok) return { decision: "deny", reason: parsed.error, parsed };
  const normalized = normalizePolicy(policy);
  if (normalized.block.includes(parsed.host)) {
    return { decision: "deny", reason: `站点 ${parsed.host} 已在阻止列表`, parsed };
  }
  const temporary = temporaryHosts instanceof Set ? temporaryHosts : new Set(temporaryHosts || []);
  if (normalized.allow.includes(parsed.host) || temporary.has(parsed.host)) {
    return { decision: "allow", reason: "", parsed };
  }
  return { decision: "ask", reason: `首次访问站点 ${parsed.host}`, parsed };
}

/**
 * Permit only a narrow class of canonical, same-service redirects after the
 * user has already approved the source URL.  This intentionally is not a
 * generic "same registrable domain" check: without a public-suffix database,
 * treating foo.github.io and bar.github.io as one site would cross tenant
 * boundaries.  We accept root <-> www and www -> a sibling locale/service
 * host, which covers canonical redirects such as www.bing.com -> cn.bing.com.
 */
function isTrustedServiceRedirect(fromValue, toValue) {
  const from = parseBrowserUrl(fromValue);
  const to = parseBrowserUrl(toValue);
  if (!from.ok || !to.ok) return false;
  if (from.host === to.host) return true;
  if (`www.${from.host}` === to.host || `www.${to.host}` === from.host) return true;
  const fromParts = from.host.split(".");
  const toParts = to.host.split(".");
  return fromParts.length >= 3
    && fromParts.length === toParts.length
    && fromParts[0] === "www"
    && fromParts.slice(1).join(".") === toParts.slice(1).join(".");
}

function setHostDecision(policy, hostValue, decision) {
  const host = normalizeHost(hostValue);
  if (!host) throw new Error("主机名无效");
  const next = normalizePolicy(policy);
  next.allow = next.allow.filter((x) => x !== host);
  next.block = next.block.filter((x) => x !== host);
  if (decision === "allow") next.allow.push(host);
  else if (decision === "block") next.block.push(host);
  else if (decision !== "remove") throw new Error("未知站点策略");
  next.allow.sort(); next.block.sort(); next.updated_at = Date.now();
  return next;
}

class BrowserPolicyStore {
  constructor(filePath, fsImpl) {
    this.filePath = filePath;
    this.fs = fsImpl || fs;
    this.policy = null;
  }

  load() {
    if (this.policy) return this.policy;
    try { this.policy = normalizePolicy(JSON.parse(this.fs.readFileSync(this.filePath, "utf8"))); }
    catch (_e) { this.policy = normalizePolicy({}); }
    return this.policy;
  }

  save(value) {
    const next = normalizePolicy(value);
    next.updated_at = Number(value && value.updated_at) || Date.now();
    this.fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    const tmp = this.filePath + ".tmp-" + process.pid;
    this.fs.writeFileSync(tmp, JSON.stringify(next, null, 2), "utf8");
    this.fs.renameSync(tmp, this.filePath);
    this.policy = next;
    return next;
  }

  set(host, decision) { return this.save(setHostDecision(this.load(), host, decision)); }
}

module.exports = {
  VERSION, HARD_BLOCKED_HOSTS, normalizeHost, parseBrowserUrl, normalizePolicy,
  decideNavigation, isTrustedServiceRedirect, setHostDecision, BrowserPolicyStore,
};
