/* ============================================================================
   work-canvas 交互模块 —— 全部 opt-in、自守卫（页面没放对应元素就静默跳过）。
   运行环境有三种，同一份代码全兼容：
     1) HashMM 右栏 CanvasPreview 的 iframe（sandbox="allow-scripts"，无同源）；
     2) Claude Artifacts（存在全局 sendPrompt）；
     3) 用户双击独立打开（无宿主 → 回喂降级为可粘贴文本）。
   硬约束：禁用 localStorage / sessionStorage（沙箱无同源时访问即抛 SecurityError），
   状态一律留在内存；绝不持有、请求或转发任何 token（对齐 CSswitch 纪律）。
   宿主桥契约（与 frontend-next/components/ArtifactPanel.tsx 的 CanvasPreview 对齐）：
     画布 → 宿主：{type:"wc:ready"}              就绪握手
                  {type:"wc:prompt", text}       回喂指令（宿主裁 4000 字并回填输入框）
     宿主 → 画布：{type:"wc:theme", dark, accent, fontSize}  主题同步
   ========================================================================== */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  function $(sel, ctx) { return (ctx || doc).querySelector(sel); }
  function $all(sel, ctx) { return Array.prototype.slice.call((ctx || doc).querySelectorAll(sel)); }
  function on(el, ev, fn) { if (el) el.addEventListener(ev, fn); }
  var framed = false;
  try { framed = window.parent && window.parent !== window; } catch (e) { framed = false; }

  /* ---- 宿主桥：收 wc:theme，发 wc:ready / wc:prompt ---- */
  function applyTheme(m) {
    try {
      if (typeof m.dark === "boolean") root.classList.toggle("dark", m.dark);
      if (typeof m.accent === "string" && /^#[0-9a-fA-F]{3,8}$/.test(m.accent)) {
        root.style.setProperty("--accent", m.accent);
        root.style.setProperty("--wc-accent", m.accent);   /* V249: 别名，供第三方画布主题变量取用 */
      }
      if (typeof m.fontSize === "number" && m.fontSize >= 11 && m.fontSize <= 20) {
        root.style.fontSize = m.fontSize + "px";
        /* V249: body 内联双写——不少（尤其 AI 生成的）页面给 body 定死 px 字号，
           只改 :root 会被样式表盖掉，内联样式优先级最高、字号立即可见。 */
        if (document.body) document.body.style.fontSize = m.fontSize + "px";
      }
    } catch (e) { /* no-op */ }
  }
  on(window, "message", function (e) {
    var m = e && e.data;
    if (!framed) return;
    try { if (e.source !== window.parent) return; } catch (err) { return; }
    if (!m || typeof m !== "object") return;
    if (m.type === "wc:theme") applyTheme(m);
    /* V222 画布 2.0（与 frontend-next/lib/canvasTemplate.ts 的 mini 运行时协议同步）：
       宿主→画布 wc:edit{on}/wc:flush；画布→宿主 wc:save{html}/wc:ask{text,context}。 */
    if (m.type === "wc:edit") wcEdit(!!m.on);
    if (m.type === "wc:flush") wcSaveNow();
    if (m.type === "wc:apply") wcApply(m);
  });
  /** 回喂宿主。成功返回 true；无任何宿主返回 false（由 composer 降级为可粘贴文本）。 */
  window.wcSendPrompt = function (text) {
    text = String(text == null ? "" : text).slice(0, 4000);
    if (!text) return false;
    if (typeof window.sendPrompt === "function") {            // Claude Artifacts 宿主
      try { window.sendPrompt(text); return true; } catch (e) { /* fallthrough */ }
    }
    if (framed) {                                             // HashMM 右栏宿主
      try { window.parent.postMessage({ type: "wc:prompt", text: text }, "*"); return true; }
      catch (e) { /* fallthrough */ }
    }
    return false;
  };

  /* ---- 主题本地切换（独立打开时用；宿主里 wc:theme 仍是权威） ---- */
  function initThemeToggle() {
    $all("[data-wc-theme]").forEach(function (btn) {
      on(btn, "click", function () { root.classList.toggle("dark"); });
    });
  }

  /* ---- 调整栏：主色 / 密度 / 字号 —— 只改 CSS 变量与类，非破坏 ---- */
  function initTweaks() {
    $all("[data-wc-accent]").forEach(function (sw) {
      on(sw, "click", function () {
        applyTheme({ accent: sw.getAttribute("data-wc-accent") });
        $all("[data-wc-accent]").forEach(function (s) { s.classList.remove("on"); });
        sw.classList.add("on");
      });
    });
    $all("[data-wc-density]").forEach(function (btn) {
      on(btn, "click", function () {
        doc.body.classList.toggle("wc-compact", btn.getAttribute("data-wc-density") === "compact");
        $all("[data-wc-density]").forEach(function (b) { b.classList.remove("on"); });
        btn.classList.add("on");
      });
    });
    var fs = $("[data-wc-fontsize]");
    on(fs, "input", function () {
      applyTheme({ fontSize: parseInt(fs.value, 10) });
      var out = $("[data-wc-fontsize-out]");
      if (out) out.textContent = fs.value + "px";
    });
  }

  /* ---- 变更看板：文件行 ↔ 对应 diff 面板 ---- */
  function initDiffNav() {
    var files = $all(".wc-file[data-target]");
    if (!files.length) return;
    function activate(id) {
      files.forEach(function (f) { f.classList.toggle("on", f.getAttribute("data-target") === id); });
      $all(".wc-diff[id]").forEach(function (d) {
        if (d.id === id) d.removeAttribute("hidden"); else d.setAttribute("hidden", "");
      });
    }
    files.forEach(function (f) { on(f, "click", function () { activate(f.getAttribute("data-target")); }); });
    activate(files[0].getAttribute("data-target"));
  }

  /* ---- 复制：data-wc-copy="#选择器"，复制目标 textContent ---- */
  function copyText(text, done) {
    function legacy() {
      var ta = doc.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.left = "-9999px";
      doc.body.appendChild(ta); ta.select();
      try { doc.execCommand("copy"); done(true); } catch (e) { done(false); }
      doc.body.removeChild(ta);
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText)
        navigator.clipboard.writeText(text).then(function () { done(true); }, legacy);
      else legacy();
    } catch (e) { legacy(); }
  }
  function initCopy() {
    $all("[data-wc-copy]").forEach(function (btn) {
      on(btn, "click", function () {
        var target = $(btn.getAttribute("data-wc-copy"));
        if (!target) return;
        copyText(target.textContent || "", function (ok) {
          var old = btn.textContent;
          btn.textContent = ok ? "已复制" : "复制失败";
          setTimeout(function () { btn.textContent = old; }, 1400);
        });
      });
    });
  }

  /* ---- 发回 agent：快捷 chips + 发送；无宿主时降级为可粘贴文本 ---- */
  function initComposer() {
    var form = $("[data-wc-composer]");
    if (!form) return;
    var ta = $("textarea", form), sent = $(".wc-sent", form), paste = $(".wc-paste", form);
    $all(".wc-chip[data-text]", form).forEach(function (c) {
      on(c, "click", function () { if (ta) { ta.value = c.getAttribute("data-text"); ta.focus(); } });
    });
    on(form, "submit", function (e) {
      e.preventDefault();
      var text = ta ? ta.value.replace(/^\s+|\s+$/g, "") : "";
      if (!text) { if (ta) ta.focus(); return; }
      if (sent) sent.classList.remove("show");
      if (paste) paste.classList.remove("show");
      if (window.wcSendPrompt(text)) {
        if (sent) sent.classList.add("show");
      } else if (paste) {
        var code = $("code", paste);
        if (code) code.textContent = text;
        paste.classList.add("show");
      }
    });
  }

  /* ---- V216 迷你趋势图：<span data-wc-spark="3,5,2,8">（CSswitch「用量统计」的画布化词汇）
         生成内联 SVG 折线 + 末点圆点，颜色随 --accent（currentColor），零外部依赖。 ---- */
  function initSparklines() {
    $all("[data-wc-spark]").forEach(function (el) {
      if (el.__wcSpark) return; el.__wcSpark = true;
      var nums = (el.getAttribute("data-wc-spark") || "").split(",").map(parseFloat)
        .filter(function (n) { return isFinite(n); });
      if (nums.length < 2) return;
      var w = parseInt(el.getAttribute("data-wc-spark-w"), 10) || 72;
      var h = parseInt(el.getAttribute("data-wc-spark-h"), 10) || 22;
      var min = Math.min.apply(null, nums), max = Math.max.apply(null, nums), span = (max - min) || 1;
      var pts = nums.map(function (n, i) {
        var x = (i / (nums.length - 1)) * (w - 4) + 2;
        var y = h - 3 - ((n - min) / span) * (h - 6);
        return x.toFixed(1) + "," + y.toFixed(1);
      });
      var ns = "http://www.w3.org/2000/svg";
      var svg = doc.createElementNS(ns, "svg");
      svg.setAttribute("viewBox", "0 0 " + w + " " + h);
      svg.setAttribute("width", w); svg.setAttribute("height", h);
      svg.setAttribute("class", "wc-spark"); svg.setAttribute("aria-hidden", "true");
      var pl = doc.createElementNS(ns, "polyline");
      pl.setAttribute("points", pts.join(" "));
      svg.appendChild(pl);
      var last = pts[pts.length - 1].split(",");
      var dot = doc.createElementNS(ns, "circle");
      dot.setAttribute("cx", last[0]); dot.setAttribute("cy", last[1]); dot.setAttribute("r", "2");
      svg.appendChild(dot);
      el.appendChild(svg);
    });
  }

  /* ---- V216 表头排序：table.wc-table[data-wc-sort] 点击/回车排序，数字感知，aria-sort ---- */
  function initSort() {
    $all("table.wc-table[data-wc-sort]").forEach(function (tb) {
      var ths = $all("th", tb), tbody = $("tbody", tb);
      if (!tbody || !ths.length) return;
      ths.forEach(function (th, ci) {
        th.classList.add("sortable"); th.setAttribute("tabindex", "0"); th.setAttribute("role", "button");
        function sortBy() {
          var dir = th.classList.contains("asc") ? -1 : 1;
          ths.forEach(function (t) { t.classList.remove("asc", "desc"); t.removeAttribute("aria-sort"); });
          th.classList.add(dir === 1 ? "asc" : "desc");
          th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
          var rows = $all("tr", tbody);
          rows.sort(function (a, b) {
            var ta = (a.children[ci] ? a.children[ci].textContent : "").replace(/^\s+|\s+$/g, "");
            var tx = (b.children[ci] ? b.children[ci].textContent : "").replace(/^\s+|\s+$/g, "");
            var na = parseFloat(ta.replace(/[^\d.+-]/g, "")), nb = parseFloat(tx.replace(/[^\d.+-]/g, ""));
            if (isFinite(na) && isFinite(nb) && /\d/.test(ta) && /\d/.test(tx)) return (na - nb) * dir;
            return ta.localeCompare(tx, "zh") * dir;
          });
          rows.forEach(function (r) { tbody.appendChild(r); });
        }
        on(th, "click", sortBy);
        on(th, "keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(); } });
      });
    });
  }

  function boot() {
    initThemeToggle(); initTweaks(); initDiffNav(); initCopy(); initComposer();
    initSparklines(); initSort();
  /* ---- V222/V224 画布 2.0 运行时（与 frontend-next/lib/canvasTemplate.ts mini 版协议同步）：
          就地编辑 wc:edit/自动保存 wc:save/立即保存 wc:flush/答案插入 wc:apply/选中即问 wc:ask ---- */
  var _editing = false, _saveT = null;
  function _editRoot() { return document.querySelector(".wc-container") || document.body; }
  function wcSaveNow() {
    if (!framed) return;
    try { window.parent.postMessage({ type: "wc:save", html: "<!DOCTYPE html>\n" + document.documentElement.outerHTML }, "*"); } catch (e) { /* no-op */ }
  }
  function wcEdit(on) {
    _editing = !!on;
    var r = _editRoot();
    r.contentEditable = _editing ? "true" : "false";
    r.style.outline = _editing ? "1.5px dashed color-mix(in srgb, var(--accent) 45%, transparent)" : "";
    r.style.borderRadius = _editing ? "12px" : "";
  }
  function _findAsk(id) { return document.querySelector('[data-wc-ask="' + id + '"]'); }
  /* V225: 三模式——replace=用答案替换被问的原文（打过标记的选区）；append=插入文末；cancel=撤掉标记 */
  function wcApply(m) {
    var mode = (m && m.mode) || "append";
    if (mode === "cancel") {
      var sp = _findAsk(m.askId);
      if (sp) { while (sp.firstChild) sp.parentNode.insertBefore(sp.firstChild, sp); sp.remove(); }
      return;
    }
    if (mode === "replace") {
      var sp2 = _findAsk(m.askId);
      if (sp2) {
        var rep = document.createElement("span");
        rep.style.whiteSpace = "pre-wrap"; rep.textContent = String(m.text || "");
        sp2.replaceWith(rep); wcSaveNow(); return;
      }
    }
    var r = _editRoot(), d = document.createElement("div");
    d.style.cssText = "margin:14px 0;padding:10px 14px;border-left:3px solid var(--accent);background:color-mix(in srgb,var(--accent) 6%,transparent);border-radius:0 10px 10px 0;white-space:pre-wrap";
    d.textContent = String((m && m.text) || ""); r.appendChild(d); wcSaveNow();
  }
  document.addEventListener("input", function () {
    if (!_editing) return;
    clearTimeout(_saveT); _saveT = setTimeout(wcSaveNow, 1200);
  });
  (function () {
    if (!framed) return;
    var b = document.createElement("button");
    b.type = "button"; b.textContent = "问一下 ✦";
    b.style.cssText = "position:absolute;display:none;z-index:99;background:var(--accent);color:#fff;border:0;border-radius:99px;font-size:12px;padding:5px 11px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.18)";
    document.body.appendChild(b);
    document.addEventListener("mouseup", function () {
      setTimeout(function () {
        var sel = window.getSelection(), txt = sel ? String(sel.toString()).trim() : "";
        if (!txt || txt.length < 2 || _editing) { b.style.display = "none"; return; }
        var rect = sel.getRangeAt(0).getBoundingClientRect();
        b.style.display = "block";
        b.style.left = (window.scrollX + rect.left + rect.width / 2 - 34) + "px";
        b.style.top = (window.scrollY + rect.top - 38) + "px";
        b.onclick = function () {
          /* V225: 给被问原文打标记（高亮），宿主答案可一键「替换原文」；surroundContents 跨节点失败则退提取重插，再失败退无标记 */
          var id = "a" + Date.now();
          var range = sel.getRangeAt(0);
          var sp = document.createElement("span");
          sp.className = "wc-asked"; sp.setAttribute("data-wc-ask", id);
          sp.style.cssText = "background:color-mix(in srgb,var(--accent) 14%,transparent);border-bottom:1.5px dashed var(--accent);border-radius:3px";
          try { range.surroundContents(sp); } catch (err) {
            try { sp.appendChild(range.extractContents()); range.insertNode(sp); } catch (e2) { sp = null; }
          }
          var full = _editRoot().innerText || "";
          var i = full.indexOf(txt);
          var ctx = i >= 0 ? full.slice(Math.max(0, i - 200), i + txt.length + 200) : txt;
          try { window.parent.postMessage({ type: "wc:ask", text: txt, context: ctx, askId: sp ? id : "" }, "*"); } catch (e) { /* no-op */ }
          b.style.display = "none";
          try { sel.removeAllRanges(); } catch (e) { /* no-op */ }
        };
      }, 10);
    });
  })();
    if (framed) { try { window.parent.postMessage({ type: "wc:ready" }, "*"); } catch (e) { /* no-op */ } }
  }
  if (doc.readyState === "loading") on(doc, "DOMContentLoaded", boot); else boot();
})();
