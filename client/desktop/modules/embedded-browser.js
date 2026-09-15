/**
 * Pure helpers for the Chat-side embedded browser surface.
 *
 * The actual WebContentsView stays in main.js.  Keeping URL, bounds and
 * preferred-browser decisions here makes the security boundary deterministic
 * and testable without booting Electron.
 */
"use strict";

const path = require("path");
const crypto = require("crypto");
const SAFE_EVIDENCE_SELECTOR = /^[a-zA-Z][a-zA-Z0-9_-]*(?::nth-of-type\([1-9][0-9]{0,4}\))?(?:\s*>\s*[a-zA-Z][a-zA-Z0-9_-]*(?::nth-of-type\([1-9][0-9]{0,4}\))?){0,15}$/;

function normalizeHttpUrl(value) {
  try {
    const parsed = new URL(String(value || "").trim());
    if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password) return null;
    return parsed.toString();
  } catch (_e) { return null; }
}

function normalizeBounds(input, parent) {
  const raw = input || {};
  const outer = parent || {};
  const pw = Math.max(1, Math.round(Number(outer.width) || 1));
  const ph = Math.max(1, Math.round(Number(outer.height) || 1));
  const x = Math.min(pw - 1, Math.max(0, Math.round(Number(raw.x) || 0)));
  const y = Math.min(ph - 1, Math.max(0, Math.round(Number(raw.y) || 0)));
  const width = Math.min(pw - x, Math.max(1, Math.round(Number(raw.width) || 1)));
  const height = Math.min(ph - y, Math.max(1, Math.round(Number(raw.height) || 1)));
  return { x, y, width, height };
}

function describeLoadError(errorCode, errorDescription) {
  const code = Number(errorCode);
  if (code === -3) return ""; // navigation was intentionally replaced/aborted
  if (code === -105) return "找不到该网站的地址，请检查网址或 DNS 设置后重试。";
  if (code === -106) return "当前设备似乎处于离线状态，请检查网络连接后重试。";
  if (code === -118 || code === -7) return "网页响应超时，请稍后重试或用 Google Chrome 打开。";
  if (code <= -200 && code >= -299) return "网页证书校验失败，为保护你的数据，HashMM 没有继续加载。";
  if (code === -102 || code === -109) return "网站拒绝了连接，请稍后重试或用 Google Chrome 打开。";
  if (code === -2) return "网页连接失败。可能是网络、代理或网站拒绝内嵌访问，请重试或用 Google Chrome 打开。";
  const detail = String(errorDescription || "").trim();
  if (detail && !/^ERR_[A-Z0-9_ -]+$/i.test(detail)) return `网页加载失败：${detail}`;
  return "网页暂时无法加载，请重试或用 Google Chrome 打开。";
}

/**
 * A rejected candidate navigation must not replace a document that Chromium
 * already committed successfully.  The block remains auditable in the main
 * process, while the trusted renderer keeps showing the last-known-good page.
 */
function blockedNavigationState(loadedUrl, blockedUrl, message) {
  const hasDocument = !!normalizeHttpUrl(loadedUrl);
  return {
    event: "blocked",
    loading: false,
    blockedUrl: String(blockedUrl || ""),
    error: hasDocument ? "" : String(message || "网页导航已被安全策略阻止。"),
    hasDocument,
    preservedDocument: hasDocument,
  };
}

function cleanEvidenceText(value, limit) {
  return String(value || "").replace(/\0/g, "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function normalizeEvidenceLocator(value) {
  const raw = value && typeof value === "object" ? value : {};
  let selector = cleanEvidenceText(raw.selector, 800);
  if (selector && !SAFE_EVIDENCE_SELECTOR.test(selector)) selector = "";
  const rectRaw = raw.rect && typeof raw.rect === "object" ? raw.rect : {};
  const number = (item) => Math.round(Math.max(-100000, Math.min(Number(item) || 0, 100000)) * 100) / 100;
  const fpRaw = raw.fingerprint && typeof raw.fingerprint === "object" ? raw.fingerprint : raw;
  const tag = cleanEvidenceText(fpRaw.tag, 32).toLowerCase().replace(/[^a-z0-9_-]/g, "");
  const role = cleanEvidenceText(fpRaw.role, 64).toLowerCase().replace(/[^a-z0-9_-]/g, "");
  const ariaLabel = cleanEvidenceText(fpRaw.aria_label || fpRaw.ariaLabel, 240);
  const ancestorPath = Array.isArray(fpRaw.ancestor_path || fpRaw.ancestorPath)
    ? (fpRaw.ancestor_path || fpRaw.ancestorPath)
      .slice(0, 16)
      .map((item) => cleanEvidenceText(item, 32).toLowerCase().replace(/[^a-z0-9_-]/g, ""))
      .filter(Boolean)
    : [];
  return {
    selector,
    exact: cleanEvidenceText(raw.exact, 2000),
    prefix: cleanEvidenceText(raw.prefix, 240),
    suffix: cleanEvidenceText(raw.suffix, 240),
    rect: { x: number(rectRaw.x), y: number(rectRaw.y), width: number(rectRaw.width), height: number(rectRaw.height) },
    fingerprint: { tag, role, aria_label: ariaLabel, ancestor_path: ancestorPath },
  };
}

function normalizeEvidenceSelection(value, browserSessionId) {
  const raw = value && typeof value === "object" ? value : {};
  const text = cleanEvidenceText(raw.text, 4000);
  const locator = normalizeEvidenceLocator(Object.assign({}, raw.locator || {}, { exact: text }));
  return {
    text,
    title: cleanEvidenceText(raw.title, 240),
    url: normalizeHttpUrl(raw.url) || "",
    locator,
    browser_session_id: cleanEvidenceText(browserSessionId, 120),
    selection_hash: text ? crypto.createHash("sha256").update(text, "utf8").digest("hex") : "",
  };
}

function buildEvidenceLocateScript(value) {
  const locator = normalizeEvidenceLocator(value);
  return `(() => {
    const locator = ${JSON.stringify(locator)};
    const norm = (v) => String(v || "").replace(/\\s+/g, " ").trim();
    const textOf = (el) => norm(el && (el.innerText || el.textContent || "")).slice(0, 4000);
    const wanted = norm(locator.exact);
    const fp = locator.fingerprint || {};
    const candidates = [];
    const all = document.body ? document.body.querySelectorAll("*") : [];
    const pathOf = (el) => {
      const out = [];
      let cur = el;
      while (cur && cur !== document.body && out.length < 16) {
        out.unshift(String(cur.tagName || "").toLowerCase());
        cur = cur.parentElement;
      }
      return out;
    };
    const contextOf = (el) => norm(el && el.parentElement
      && (el.parentElement.innerText || el.parentElement.textContent) || textOf(el)).slice(0, 4000);
    const score = (el) => {
      const text = textOf(el);
      if (!text || (wanted && !text.includes(wanted))) return null;
      let value = 0;
      const reasons = [];
      if (wanted) { value += 0.46; reasons.push("正文"); }
      const context = contextOf(el);
      const at = wanted ? context.indexOf(wanted) : -1;
      if (at >= 0 && locator.prefix
          && context.slice(Math.max(0, at - locator.prefix.length), at).includes(locator.prefix.slice(-120))) {
        value += 0.10; reasons.push("前文");
      }
      if (at >= 0 && locator.suffix
          && context.slice(at + wanted.length, at + wanted.length + 120).includes(locator.suffix.slice(0, 120))) {
        value += 0.10; reasons.push("后文");
      }
      const tag = String(el.tagName || "").toLowerCase();
      const role = String(el.getAttribute && el.getAttribute("role") || "").toLowerCase();
      if (fp.tag && tag === fp.tag) { value += 0.10; reasons.push("元素类型"); }
      if (fp.role && role === fp.role) { value += 0.08; reasons.push("语义角色"); }
      if (fp.aria_label && String(el.getAttribute && el.getAttribute("aria-label") || "") === fp.aria_label) {
        value += 0.08; reasons.push("辅助标签");
      }
      const expectedPath = Array.isArray(fp.ancestor_path) ? fp.ancestor_path : [];
      if (expectedPath.length && pathOf(el).slice(-expectedPath.length).join(">") === expectedPath.join(">")) {
        value += 0.08; reasons.push("结构路径");
      }
      if (locator.selector) {
        try { if (document.querySelector(locator.selector) === el) { value += 0.18; reasons.push("选择器"); } } catch (_) {}
      }
      const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
      if (rect && locator.rect && (locator.rect.width || locator.rect.height)) {
        const dx = Math.abs((rect.left + rect.width / 2)
          - (Number(locator.rect.x) + Number(locator.rect.width) / 2));
        const dy = Math.abs((rect.top + rect.height / 2)
          - (Number(locator.rect.y) + Number(locator.rect.height) / 2));
        if (dx < 160 && dy < 160) { value += 0.06; reasons.push("位置接近"); }
      }
      return { el, text, value, reasons };
    };
    for (const el of all) {
      const item = score(el);
      if (item) candidates.push(item);
      if (candidates.length >= 500) break;
    }
    candidates.sort((a, b) => b.value - a.value || a.text.length - b.text.length);
    const best = candidates[0];
    const minimum = wanted ? 0.62 : 0.50;
    if (!best || best.value < minimum) {
      return { found: false, matched: false, confidence: best ? Number(best.value.toFixed(3)) : 0,
        text: "", url: String(location.href || "") };
    }
    const element = best.el;
    try { element.scrollIntoView({ block: "center", inline: "nearest", behavior: "auto" }); } catch (_) {}
    const previous = element.style.outline;
    const previousOffset = element.style.outlineOffset;
    element.style.outline = "3px solid #2563eb";
    element.style.outlineOffset = "3px";
    window.setTimeout(() => { try { element.style.outline = previous; element.style.outlineOffset = previousOffset; } catch (_) {} }, 5000);
    return { found: true, matched: true, confidence: Number(best.value.toFixed(3)),
      match_reason: best.reasons.join("、"), text: wanted || best.text,
      title: String(document.title || "").slice(0, 240),
      url: String(location.href || "").slice(0, 2048) };
  })()`;
}

function chromeCandidates(platform, env) {
  const e = env || {};
  if (platform === "win32") {
    const roots = [e.PROGRAMFILES, e["PROGRAMFILES(X86)"], e.ProgramW6432, e.LOCALAPPDATA].filter(Boolean);
    return [...new Set(roots.map(root => path.join(root, "Google", "Chrome", "Application", "chrome.exe")))];
  }
  if (platform === "darwin") return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"];
  return ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/google-chrome"];
}

async function launchPreferredBrowser(value, deps) {
  const url = normalizeHttpUrl(value);
  if (!url) return { ok: false, error: "仅允许不含账号密码的 http/https 链接" };
  const d = deps || {};
  const exists = typeof d.exists === "function" ? d.exists : () => false;
  const candidate = chromeCandidates(d.platform || process.platform, d.env || process.env).find(exists);
  if (candidate && typeof d.spawn === "function") {
    try {
      const child = d.spawn(candidate, [url], { detached: true, stdio: "ignore", windowsHide: false });
      if (child && typeof child.unref === "function") child.unref();
      return { ok: true, browser: "chrome", executable: candidate };
    } catch (e) {
      // A present but broken Chrome install should not make the user lose the
      // link.  Fall through to the OS default browser and report the fallback.
      if (typeof d.openExternal !== "function") return { ok: false, error: String(e && e.message || e) };
    }
  }
  if (typeof d.openExternal !== "function") return { ok: false, error: "没有可用的外部浏览器启动器" };
  try {
    await d.openExternal(url);
    return { ok: true, browser: "system", fallback: !!candidate };
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
}

module.exports = {
  normalizeHttpUrl,
  normalizeBounds,
  describeLoadError,
  blockedNavigationState,
  normalizeEvidenceLocator,
  normalizeEvidenceSelection,
  buildEvidenceLocateScript,
  chromeCandidates,
  launchPreferredBrowser,
};
