/**
 * desktop/modules/browser-use.js — 桌面端 Browser Use（V172，受控浏览器型）。
 *
 * 演进路线（与 computeruse.js 一脉相承）：
 *   prompt → harness → loop → computer use(屏幕+OCR点击) → **browser use(受控浏览器)**
 *
 * 为什么要它：
 *   一期 computer use 操作网页靠「open_url 唤起系统浏览器 → 截屏 OCR 定位 → 真鼠标点击」，
 *   依赖前台窗口、OCR 估坐标、会抢用户鼠标，脆且慢（对标大厂前也确实点空）。
 *   本模块对标 Claude computer use / OpenAI Operator / browser-use：起一个**隔离的受控浏览器**，
 *   把【可交互元素树（带稳定编号）+ 截图】喂给模型，模型只输出「点 #编号 / 在 #编号 输入 / 滚动 / 导航」，
 *   主进程用 Electron 原生 `webContents.sendInputEvent` 把点击/按键**直接注入受控页面**——
 *   不动用户真实鼠标、可后台运行、点击命中率以 DOM 包围盒为准而非肉眼估。
 *
 * 实现完全基于 Electron 内置高层 API（无需 puppeteer/playwright/外部依赖）：
 *   - 导航      win.loadURL + did-finish-load/did-fail-load 等待
 *   - 元素树    win.webContents.executeJavaScript(注入遍历脚本，给每个可交互元素打 data-bu-id)
 *   - 截图      win.webContents.capturePage()（后台窗口同样可截）
 *   - 点击/按键 win.webContents.sendInputEvent(mouseDown/Up、keyDown/char/Up；不移动 OS 光标)
 *   - 滚动      executeJavaScript(window.scrollBy / element.scrollBy)（确定性）
 *   - 前进后退  webContents.goBack/goForward
 *
 * 安全：受控窗口完全沙箱化（nodeIntegration:false, contextIsolation:true, sandbox:true），
 *      恶意网页无法触达 Node；导航仅允许 http/https（禁 file:/javascript:/data:）；
 *      只读模式禁止 click/type/key（只许浏览读取）；可选 cuBrowserConfirm 对点击/输入逐次确认。
 *
 * 模块分两部分：
 *   1) 纯逻辑（validateBrowserAction / describeBrowserAction / assessBrowserAction）——可 tests-node 冒烟，不依赖 electron；
 *   2) 引擎（BrowserSession + perform/ensure/dispose）——需要 electron，在主进程跑。
 */
"use strict";

// ───────────────────────── 1) 纯逻辑（可单测，无 electron 依赖） ─────────────────────────

const ACTIONS = new Set(["navigate", "click", "type", "key", "scroll", "back", "forward", "read", "wait"]);
const SCROLL_DIRS = new Set(["up", "down", "left", "right"]);

function _int(v, lo, hi, dflt) {
  let n = Number(v);
  if (!isFinite(n)) n = dflt;
  n = Math.round(n);
  if (n < lo) n = lo; if (n > hi) n = hi;
  return n;
}

/** 校验一次 browser 动作，产出归一化 plan。纯函数。返回 {ok, plan} 或 {ok:false, error}。 */
function validateBrowserAction(args) {
  args = args || {};
  const action = String(args.action || "").trim();
  if (!ACTIONS.has(action)) {
    return { ok: false, error: `未知 browser 动作「${action}」。可用：navigate/click/type/key/scroll/back/forward/read/wait` };
  }
  const plan = { action };
  if (action === "navigate") {
    let url = String(args.url || "").trim();
    if (!url) return { ok: false, error: "navigate 缺少 url" };
    if (!/^https?:\/\//i.test(url)) {
      // 容错：用户/模型给了裸域名 → 补 https://；但显式的危险协议一律拒绝
      if (/^(file|javascript|data|chrome|about):/i.test(url)) {
        return { ok: false, error: "出于安全，仅允许打开 http/https 网址（已拒绝 " + url.split(":")[0] + ": 协议）" };
      }
      url = "https://" + url;
    }
    plan.url = url;
  } else if (action === "click") {
    if (args.index == null) return { ok: false, error: "click 缺少 index（来自上一步回传的元素清单编号）" };
    plan.index = _int(args.index, 0, 100000, 0);
  } else if (action === "type") {
    if (args.index == null) return { ok: false, error: "type 缺少 index（要输入的目标元素编号）" };
    if (args.text == null) return { ok: false, error: "type 缺少 text" };
    plan.index = _int(args.index, 0, 100000, 0);
    plan.text = String(args.text);
    plan.submit = !!args.submit;   // 可选：输入后自动回车
  } else if (action === "key") {
    const keys = String(args.keys || "").trim();
    if (!keys) return { ok: false, error: "key 缺少 keys（如 'enter'、'tab'、'ctrl+a'）" };
    plan.keys = keys.toLowerCase();
  } else if (action === "scroll") {
    const dir = String(args.scroll_direction || "down").toLowerCase();
    plan.scroll_direction = SCROLL_DIRS.has(dir) ? dir : "down";
    plan.scroll_amount = _int(args.scroll_amount, 1, 20, 3);
    if (args.index != null) plan.index = _int(args.index, 0, 100000, 0);   // 定向滚动某元素（可选）
  } else if (action === "wait") {
    plan.ms = _int(args.ms, 0, 10000, 500);
  }
  // back / forward / read 无参
  return { ok: true, plan };
}

/** 动作人话摘要（确认框/审计用）。纯函数。 */
function describeBrowserAction(plan) {
  if (!plan) return "(空动作)";
  switch (plan.action) {
    case "navigate": return `打开网页 ${plan.url}`;
    case "click": return `点击元素 #${plan.index}`;
    case "type": return `在元素 #${plan.index} 输入「${(plan.text || "").slice(0, 40)}${(plan.text || "").length > 40 ? "…" : ""}」${plan.submit ? "并回车" : ""}`;
    case "key": return `按键 ${plan.keys}`;
    case "scroll": return `滚动${{ up: "↑", down: "↓", left: "←", right: "→" }[plan.scroll_direction] || ""}${plan.index != null ? `（元素 #${plan.index}）` : ""}`;
    case "back": return "后退";
    case "forward": return "前进";
    case "read": return "读取页面正文";
    case "wait": return `等待 ${plan.ms}ms`;
    default: return plan.action;
  }
}

/**
 * 安全策略闸（纯函数）。返回 {decision:"allow"|"confirm"|"deny", reason}。
 * - 只读模式(cuReadOnly)：禁止会改变页面状态的 click/type/key（仅许 navigate/scroll/read/back/forward/wait）。
 * - cuBrowserConfirm：对 click/type 逐次原生确认（默认关——自主浏览的意义就在于不用每步点确认）。
 */
function classifySensitiveBrowserAction(plan, context) {
  const el = context && context.element || {};
  const label = [el.text, el.placeholder, el.type, el.role].filter(Boolean).join(" ").toLowerCase();
  if (plan && plan.action === "type") {
    if (el.type === "password" || /(password|passcode|otp|验证码|密码|cvv|card number|银行卡|api.?key|secret)/i.test(label)) {
      return { sensitive: true, reason: "即将向敏感输入框提交凭据或隐私数据" };
    }
  }
  if (plan && plan.action === "click" && /(pay|purchase|buy now|checkout|place order|delete|remove account|send|publish|grant|allow access|付款|支付|购买|下单|结算|删除|注销|发送|发布|授权|允许访问)/i.test(label)) {
    return { sensitive: true, reason: `即将点击可能产生外部副作用的控件「${String(el.text || el.placeholder || "").slice(0, 60)}」` };
  }
  return { sensitive: false, reason: "" };
}

function assessBrowserAction(plan, policy, context) {
  policy = policy || {};
  const mutating = (plan.action === "click" || plan.action === "type" || plan.action === "key");
  if (policy.cuReadOnly && mutating) {
    return { decision: "deny", reason: "只读模式：浏览器助手仅可浏览/读取，已禁止点击与输入" };
  }
  const sensitive = classifySensitiveBrowserAction(plan, context);
  if (sensitive.sensitive) return { decision: "confirm", reason: sensitive.reason, sensitive: true };
  if (policy.cuBrowserConfirm && (plan.action === "click" || plan.action === "type")) {
    return { decision: "confirm", reason: "已开启「浏览器操作逐次确认」" };
  }
  return { decision: "allow", reason: "" };
}

// ───────────────────────── 2) 引擎（需要 electron，主进程运行） ─────────────────────────

let _electron = null;
function _elec() {
  if (!_electron) _electron = require("electron");   // 延迟 require：纯逻辑部分被 tests-node 引入时不触发
  return _electron;
}

// 注入页面的「可交互元素树」提取脚本。返回 JSON-安全对象：
//   {url, title, scrollY, scrollMaxY, vw, vh, elements:[{i,tag,type,role,text,value,placeholder,href,inView,rect:{x,y,w,h}}], textLen}
// 给每个上榜元素打 data-bu-id=i，后续 click/type 按编号回找（布局变动也能重定位）。
const EXTRACT_JS = `(() => {
  try {
    const MAX = 120, MAXTXT = 80;
    const seen = new Set();
    const out = [];
    const vw = window.innerWidth, vh = window.innerHeight;
    const isInteractive = (el) => {
      const t = el.tagName.toLowerCase();
      if (["a","button","input","select","textarea","summary"].includes(t)) return true;
      const role = (el.getAttribute("role") || "").toLowerCase();
      if (["button","link","menuitem","tab","checkbox","radio","switch","option","searchbox","textbox"].includes(role)) return true;
      if (el.hasAttribute("onclick")) return true;
      if (el.isContentEditable) return true;
      const ti = el.getAttribute("tabindex");
      if (ti !== null && ti !== "-1") return true;
      return false;
    };
    const visible = (el, r) => {
      if (!r || r.width < 2 || r.height < 2) return false;
      const s = getComputedStyle(el);
      if (s.display === "none" || s.visibility === "hidden" || +s.opacity === 0) return false;
      if (r.bottom < -vh || r.top > vh * 2) return false;   // 远在视口之外（上/下一屏内仍收，可滚动可达）
      return true;
    };
    const text = (el) => {
      let s = (el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("alt") ||
               (el.tagName.toLowerCase() === "input" ? (el.getAttribute("value") || el.getAttribute("placeholder") || "") : "") ||
               el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
      return s.length > MAXTXT ? s.slice(0, MAXTXT) + "…" : s;
    };
    const all = document.querySelectorAll("a,button,input,select,textarea,summary,[role],[onclick],[contenteditable],[tabindex]");
    let id = 0;
    for (const el of all) {
      if (out.length >= MAX) break;
      if (!isInteractive(el)) continue;
      // 去重：若祖先里已有上榜的可交互元素（如 button>span），跳过子节点
      let anc = el.parentElement, nested = false;
      while (anc) { if (seen.has(anc)) { nested = true; break; } anc = anc.parentElement; }
      if (nested) continue;
      const r = el.getBoundingClientRect();
      if (!visible(el, r)) continue;
      seen.add(el);
      el.setAttribute("data-bu-id", String(id));
      const t = el.tagName.toLowerCase();
      out.push({
        i: id,
        tag: t,
        type: (el.getAttribute("type") || "").toLowerCase() || undefined,
        role: (el.getAttribute("role") || "").toLowerCase() || undefined,
        text: text(el),
        value: (t === "input" || t === "textarea") ? String(el.value || "").slice(0, MAXTXT) : undefined,
        placeholder: el.getAttribute("placeholder") || undefined,
        href: t === "a" ? (el.getAttribute("href") || undefined) : undefined,
        inView: r.top >= 0 && r.bottom <= vh,
        rect: { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
      });
      id++;
    }
    const body = document.body;
    return {
      url: location.href, title: document.title,
      scrollY: Math.round(window.scrollY), scrollMaxY: Math.max(0, Math.round((body ? body.scrollHeight : 0) - vh)),
      vw, vh, elements: out, textLen: (body && body.innerText ? body.innerText.length : 0),
    };
  } catch (e) { return { error: String(e && e.message || e), elements: [] }; }
})()`;

function _readTextJS(maxChars) {
  return `(() => { try {
    const t = (document.body && document.body.innerText || "").replace(/\\n{3,}/g, "\\n\\n").trim();
    return { url: location.href, title: document.title, text: t.slice(0, ${maxChars}), truncated: t.length > ${maxChars} };
  } catch (e) { return { error: String(e && e.message || e) }; } })()`;
}

function _locateForClickJS(index) {
  // 把目标滚进视口中央，回传其新的中心坐标（CSS px，视口坐标系 = sendInputEvent 坐标系）。
  return `(() => { try {
    const el = document.querySelector('[data-bu-id="${index}"]');
    if (!el) return { ok: false, reason: "元素 #${index} 已不在页面上（可能页面已变化，请重新读取元素清单）" };
    el.scrollIntoView({ block: "center", inline: "center" });
    const r = el.getBoundingClientRect();
    return { ok: true, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
             tag: el.tagName.toLowerCase(), w: Math.round(r.width), h: Math.round(r.height) };
  } catch (e) { return { ok: false, reason: String(e && e.message || e) }; } })()`;
}

function _typeJS(index, text, submit) {
  // 健壮输入：用原生 value setter 绕过 React/Vue 的受控追踪，再派发 input+change（这是给现代前端可靠输入的标准做法）。
  const t = JSON.stringify(String(text));
  return `(() => { try {
    const el = document.querySelector('[data-bu-id="${index}"]');
    if (!el) return { ok: false, reason: "元素 #${index} 已不在页面上" };
    el.focus();
    const tag = el.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea") {
      const proto = tag === "input" ? window.HTMLInputElement.prototype : window.HTMLTextAreaElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, ${t});
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (el.isContentEditable) {
      el.textContent = ${t};
      el.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      return { ok: false, reason: "元素 #${index} 不是可输入控件" };
    }
    return { ok: true, tag };
  } catch (e) { return { ok: false, reason: String(e && e.message || e) }; } })()`;
}

function _scrollJS(direction, amount, index) {
  const sign = (direction === "up" || direction === "left") ? -1 : 1;
  const axis = (direction === "left" || direction === "right") ? "x" : "y";
  const px = sign * amount * Math.round((axis === "y" ? 1 : 1) * 300);   // 每格约 300px
  const dx = axis === "x" ? px : 0, dy = axis === "y" ? px : 0;
  if (index != null) {
    return `(() => { try { const el = document.querySelector('[data-bu-id="${index}"]'); if (!el) return { ok:false }; el.scrollBy(${dx}, ${dy}); return { ok:true, scrollY: Math.round(window.scrollY) }; } catch(e){ return {ok:false}; } })()`;
  }
  return `(() => { try { window.scrollBy(${dx}, ${dy}); return { ok:true, scrollY: Math.round(window.scrollY) }; } catch(e){ return {ok:false}; } })()`;
}

// 组合键解析：'ctrl+a' → {keyCode:'A', modifiers:['control']}
const _MOD = { ctrl: "control", control: "control", shift: "shift", alt: "alt", option: "alt", meta: "meta", cmd: "meta", win: "meta", super: "meta" };
const _NAMED = {
  enter: "Return", return: "Return", tab: "Tab", esc: "Escape", escape: "Escape",
  backspace: "Backspace", delete: "Delete", del: "Delete", space: "Space",
  up: "Up", down: "Down", left: "Left", right: "Right",
  home: "Home", end: "End", pageup: "PageUp", pagedown: "PageDown",
};
function _parseKeys(keys) {
  const parts = String(keys).split("+").map((s) => s.trim()).filter(Boolean);
  const modifiers = [];
  let main = "";
  for (const p of parts) {
    if (_MOD[p]) { const m = _MOD[p]; if (!modifiers.includes(m)) modifiers.push(m); }
    else main = p;
  }
  let keyCode = _NAMED[main] || (main.length === 1 ? main.toUpperCase() : main);
  return { keyCode, modifiers };
}

const _sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class BrowserSession {
  constructor() {
    this.win = null; this._quitHooked = false; this._lastSnapshot = null;
    this._navigationBlocked = null; this._isNavigationAllowed = () => true; this._onSessionClosed = null;
  }

  ensure(opts) {
    opts = opts || {};
    const { BrowserWindow, app } = _elec();
    if (typeof opts.isNavigationAllowed === "function") this._isNavigationAllowed = opts.isNavigationAllowed;
    if (typeof opts.onSessionClosed === "function") this._onSessionClosed = opts.onSessionClosed;
    if (this.win && !this.win.isDestroyed()) {
      if (opts.headful && !this.win.isVisible()) this.win.showInactive();
      return this.win;
    }
    this.win = new BrowserWindow({
      width: 1280, height: 820,
      show: !!opts.headful,                  // 默认隐藏在后台跑；headful=true 弹出真窗口便于围观/排障
      backgroundThrottling: false,           // 后台也保持渲染（截图/JS 才稳）
      webPreferences: {
        nodeIntegration: false, contextIsolation: true, sandbox: true,   // 受控页彻底沙箱：恶意网页摸不到 Node
        backgroundThrottling: false, partition: "persist:browser-use",   // 独立会话分区（登录态与主应用隔离、自身持久）
      },
      title: "HashMM 浏览器助手",
    });
    const wc = this.win.webContents;
    const allowNavigation = (event, url, kind) => {
      let allowed = false;
      try { allowed = !!this._isNavigationAllowed(url); } catch (_e) { allowed = false; }
      if (allowed) return true;
      try { event && event.preventDefault(); } catch (_e) {}
      this._navigationBlocked = { url: String(url || ""), kind: kind || "navigation" };
      return false;
    };
    // Cross-site redirects and page-initiated navigations must not silently
    // escape the per-site approval boundary. The agent can explicitly navigate
    // to the reported URL, which runs through the native approval dialog.
    wc.on("will-navigate", (event, url) => { allowNavigation(event, url, "navigation"); });
    wc.on("will-redirect", (event, url) => { allowNavigation(event, url, "redirect"); });
    wc.setWindowOpenHandler(({ url }) => {
      let allowed = false; try { allowed = !!this._isNavigationAllowed(url); } catch (_e) {}
      this._navigationBlocked = { url: String(url || ""), kind: allowed ? "popup" : "unapproved-popup" };
      return { action: "deny" };
    });
    if (opts.headful) this.win.showInactive();
    // 进程退出兜底销毁
    if (!this._quitHooked) {
      this._quitHooked = true;
      try { app.on("before-quit", () => { try { this.dispose(); } catch (_e) {} }); } catch (_e) {}
    }
    return this.win;
  }

  dispose() {
    if (this.win && !this.win.isDestroyed()) { try { this.win.destroy(); } catch (_e) {} }
    this.win = null; this._lastSnapshot = null;
    this._navigationBlocked = null;
    const cb = this._onSessionClosed; this._onSessionClosed = null;
    if (typeof cb === "function") { try { cb(); } catch (_e) {} }
  }

  inspectPlan(plan) {
    const state = this._lastSnapshot || {};
    const element = plan && plan.index != null && Array.isArray(state.elements)
      ? state.elements.find((item) => item && item.i === plan.index) : null;
    if (!element) return { url: String(state.url || ""), element: null };
    return { url: String(state.url || ""), element: {
      i: element.i, tag: element.tag, type: element.type, role: element.role,
      text: String(element.text || ""), placeholder: String(element.placeholder || ""),
    } };
  }

  state() {
    return { active: !!(this.win && !this.win.isDestroyed()), visible: !!(this.win && !this.win.isDestroyed() && this.win.isVisible()),
      url: String(this._lastSnapshot && this._lastSnapshot.url || ""), title: String(this._lastSnapshot && this._lastSnapshot.title || "") };
  }

  _blockedResult(emit, action) {
    const hit = this._navigationBlocked;
    if (!hit) return null;
    this._navigationBlocked = null;
    const error = hit.kind === "popup"
      ? `网页尝试打开新窗口，已按隔离策略拦截。请显式 navigate 到 ${hit.url}`
      : `网页尝试访问尚未授权的站点，已拦截。请显式 navigate 到 ${hit.url} 以请求用户授权`;
    emit({ phase: "error", action, error, url: hit.url, blocked: true });
    return { ok: false, error };
  }

  _wc() { return this.win.webContents; }

  // 等待一次导航/加载稳定（resolve 不抛——失败也回结果，交给上层照常截图回传）。
  _waitLoad(timeoutMs) {
    const wc = this._wc();
    return new Promise((resolve) => {
      let done = false;
      const finish = (ok, err) => { if (done) return; done = true; cleanup(); resolve({ ok, err: err || "" }); };
      const onFinish = () => finish(true);
      const onFail = (_e, code, desc) => { if (code === -3) return; finish(false, `加载失败(${code}) ${desc || ""}`); };   // -3=ERR_ABORTED(重定向常见)，忽略
      const onStop = () => finish(true);
      const cleanup = () => {
        try { wc.removeListener("did-finish-load", onFinish); } catch (_e) {}
        try { wc.removeListener("did-fail-load", onFail); } catch (_e) {}
        try { wc.removeListener("did-stop-loading", onStop); } catch (_e) {}
        clearTimeout(timer);
      };
      const timer = setTimeout(() => finish(true, "加载超时（按当前状态继续）"), timeoutMs || 30000);
      wc.once("did-finish-load", onFinish);
      wc.once("did-fail-load", onFail);
      wc.once("did-stop-loading", onStop);
    });
  }

  async _eval(js) { try { return await this._wc().executeJavaScript(js, true); } catch (e) { return { error: String(e && e.message || e) }; } }

  // 截图（JPEG dataURL）。后台窗口亦可。失败回空串。
  async _shot() {
    try {
      const img = await this._wc().capturePage();
      if (!img || img.isEmpty()) return "";
      return "data:image/jpeg;base64," + img.toJPEG(70).toString("base64");
    } catch (_e) { return ""; }
  }

  async _snapshot() {
    const state = await this._eval(EXTRACT_JS);
    this._lastSnapshot = state;
    const image = await this._shot();
    return { state, image };
  }

  // 统一的"完成"事件：带截图 + 紧凑元素清单(含包围盒) + 页面信息，供前端 cockpit 画编号叠加框/时间线。
  _emitDone(emit, action, snap, extra) {
    const st = (snap && snap.state) || {};
    emit(Object.assign({
      phase: "done", action,
      image: snap && snap.image || "",
      url: st.url, title: st.title, vw: st.vw, vh: st.vh,
      scrollY: st.scrollY, scrollMaxY: st.scrollMaxY,
      elements: (st.elements || []).map((e) => ({ i: e.i, rect: e.rect, text: e.text, tag: e.tag,
        type: e.type, role: e.role, placeholder: e.placeholder, inView: e.inView })),
    }, extra || {}));
  }

  // 把元素树渲染成给模型看的紧凑文本清单。
  _renderElements(state) {
    if (!state || state.error) return `（页面读取出错：${state && state.error || "未知"}）`;
    const lines = [];
    lines.push(`页面：${state.title || "(无标题)"}  ·  ${state.url}`);
    const sy = state.scrollY, smax = state.scrollMaxY;
    if (smax > 0) lines.push(`滚动：${sy}/${smax}px${sy < smax ? "（下方还有内容，可 scroll down）" : "（已到底部）"}`);
    lines.push(`可交互元素（共 ${state.elements.length} 个；用 click(编号)/type(编号,文本) 操作）：`);
    for (const e of state.elements) {
      const bits = [`[${e.i}]`, `<${e.tag}${e.type ? " " + e.type : ""}>`];
      if (e.text) bits.push(`"${e.text}"`);
      if (e.value) bits.push(`值="${e.value}"`);
      else if (e.placeholder) bits.push(`占位="${e.placeholder}"`);
      if (!e.inView) bits.push("(需滚动)");
      lines.push("  " + bits.join(" "));
    }
    if (!state.elements.length) lines.push("  （没识别到可交互元素——可能页面还在加载，可 wait 后再 read，或 scroll 查看）");
    return lines.join("\n");
  }

  /** 执行一个 plan，返回 {ok, text, image, error}。text=给模型的元素清单/正文，image=截图 dataURL。 */
  async perform(plan, opts) {
    opts = opts || {};
    const emit = typeof opts.onEvent === "function" ? opts.onEvent : () => {};
    this.ensure(opts);
    const wc = this._wc();
    emit({ phase: "start", action: plan.action, desc: describeBrowserAction(plan),
      url: plan.url, index: plan.index, keys: plan.keys, dir: plan.scroll_direction });

    try {
      if (plan.action === "navigate") {
        this._navigationBlocked = null;
        await wc.loadURL(plan.url).catch(() => {});
        const r = await this._waitLoad(opts.navTimeoutMs);
        const blocked = this._blockedResult(emit, "navigate"); if (blocked) return blocked;
        await _sleep(450);                       // 给 SPA 水合一点时间
        const snap = await this._snapshot();
        this._emitDone(emit, "navigate", snap, { note: r.err });
        return { ok: true, text: (r.err ? `（${r.err}）\n` : "") + this._renderElements(snap.state), image: snap.image };
      }

      if (plan.action === "back" || plan.action === "forward") {
        this._navigationBlocked = null;
        const can = plan.action === "back" ? wc.canGoBack() : wc.canGoForward();
        if (!can) return { ok: false, error: `无法${plan.action === "back" ? "后退" : "前进"}（没有历史记录）` };
        if (plan.action === "back") wc.goBack(); else wc.goForward();
        await this._waitLoad(opts.navTimeoutMs); await _sleep(350);
        const blocked = this._blockedResult(emit, plan.action); if (blocked) return blocked;
        const snap = await this._snapshot();
        this._emitDone(emit, plan.action, snap);
        return { ok: true, text: this._renderElements(snap.state), image: snap.image };
      }

      if (plan.action === "read") {
        const r = await this._eval(_readTextJS(8000));
        const snap = await this._snapshot();
        // V344: keep a bounded excerpt in the local trajectory so the final
        // Chat message can persist real browser sources instead of model prose.
        // The backend applies its public-source bound again before storage.
        this._emitDone(emit, "read", snap, {
          snippet: r && !r.error ? String(r.text || "").slice(0, 1200) : "",
          truncated: !!(r && r.truncated),
        });
        if (r && r.error) return { ok: false, error: "读取失败：" + r.error };
        return { ok: true, text: `【${r.title}】${r.truncated ? "（正文已截断）" : ""}\n${r.text}\n\n---\n${this._renderElements(snap.state)}`, image: snap.image };
      }

      if (plan.action === "wait") {
        await _sleep(plan.ms);
        const snap = await this._snapshot();
        this._emitDone(emit, "wait", snap);
        return { ok: true, text: `已等待 ${plan.ms}ms。\n${this._renderElements(snap.state)}`, image: snap.image };
      }

      if (plan.action === "scroll") {
        await this._eval(_scrollJS(plan.scroll_direction, plan.scroll_amount, plan.index));
        await _sleep(200);
        const snap = await this._snapshot();
        this._emitDone(emit, "scroll", snap);
        return { ok: true, text: this._renderElements(snap.state), image: snap.image };
      }

      if (plan.action === "key") {
        this._navigationBlocked = null;
        const { keyCode, modifiers } = _parseKeys(plan.keys);
        wc.sendInputEvent({ type: "keyDown", keyCode, modifiers });
        if (keyCode.length === 1) wc.sendInputEvent({ type: "char", keyCode, modifiers });
        wc.sendInputEvent({ type: "keyUp", keyCode, modifiers });
        await _sleep(plan.keys.includes("enter") || keyCode === "Return" ? 600 : 200);
        const blocked = this._blockedResult(emit, "key"); if (blocked) return blocked;
        const snap = await this._snapshot();
        this._emitDone(emit, "key", snap, { keys: plan.keys });
        return { ok: true, text: this._renderElements(snap.state), image: snap.image };
      }

      if (plan.action === "click") {
        this._navigationBlocked = null;
        const loc = await this._eval(_locateForClickJS(plan.index));
        if (!loc || !loc.ok) return { ok: false, error: (loc && loc.reason) || "定位失败" };
        await _sleep(120);   // 等 scrollIntoView 落定
        wc.sendInputEvent({ type: "mouseMove", x: loc.x, y: loc.y });
        wc.sendInputEvent({ type: "mouseDown", x: loc.x, y: loc.y, button: "left", clickCount: 1 });
        wc.sendInputEvent({ type: "mouseUp", x: loc.x, y: loc.y, button: "left", clickCount: 1 });
        // 点击可能触发导航或 SPA 切换——给一点时间，再快照
        const r = await Promise.race([this._waitLoad(8000), _sleep(800).then(() => ({ ok: true }))]);
        await _sleep(250);
        const blocked = this._blockedResult(emit, "click"); if (blocked) return blocked;
        const snap = await this._snapshot();
        this._emitDone(emit, "click", snap, { index: plan.index });
        return { ok: true, text: this._renderElements(snap.state), image: snap.image };
      }

      if (plan.action === "type") {
        this._navigationBlocked = null;
        const r = await this._eval(_typeJS(plan.index, plan.text, plan.submit));
        if (!r || !r.ok) return { ok: false, error: (r && r.reason) || "输入失败" };
        if (plan.submit) {
          wc.sendInputEvent({ type: "keyDown", keyCode: "Return" });
          wc.sendInputEvent({ type: "keyUp", keyCode: "Return" });
          await this._waitLoad(8000); await _sleep(300);
        } else {
          await _sleep(150);
        }
        const blocked = this._blockedResult(emit, "type"); if (blocked) return blocked;
        const snap = await this._snapshot();
        this._emitDone(emit, "type", snap, { index: plan.index });
        return { ok: true, text: this._renderElements(snap.state), image: snap.image };
      }

      return { ok: false, error: "未实现的 browser 动作：" + plan.action };
    } catch (e) {
      emit({ phase: "error", action: plan.action, error: String(e && e.message || e) });
      return { ok: false, error: String(e && e.message || e) };
    }
  }
}

// 模块级单例（一个应用维持一个受控浏览器；loop 结束/退出时 dispose）。
let _session = null;
function _sess() { if (!_session) _session = new BrowserSession(); return _session; }

async function perform(plan, opts) { return _sess().perform(plan, opts); }
function ensure(opts) { return _sess().ensure(opts); }
function dispose() { if (_session) { _session.dispose(); } }
function inspectPlan(plan) { return _session ? _session.inspectPlan(plan) : { url: "", element: null }; }
function state() { return _session ? _session.state() : { active: false, visible: false, url: "", title: "" }; }
function show() {
  if (!_session || !_session.win || _session.win.isDestroyed()) return false;
  try { _session.win.show(); _session.win.focus(); return true; } catch (_e) { return false; }
}

module.exports = {
  // 纯逻辑（可单测）
  validateBrowserAction, describeBrowserAction, assessBrowserAction, classifySensitiveBrowserAction,
  _parseKeys, ACTIONS,
  // 引擎（主进程）
  perform, ensure, dispose, inspectPlan, state, show, BrowserSession,
};
