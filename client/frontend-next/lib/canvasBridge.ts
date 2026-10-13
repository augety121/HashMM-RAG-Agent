/** canvasBridge — 画布运行时协议桥（V249）。
 *
 * 根因：右栏工具条的 主色/字号（以及 编辑/划选即问）走的是 postMessage 协议
 * （wc:theme / wc:edit / wc:flush / wc:apply），协议的画布侧实现在
 * skills/packs/work-canvas/assets/canvas.js —— **AI 直接生成的画布 HTML 里没有这段
 * 脚本**，消息发进去无人接收，于是"点了没反应"。与 V246 修粗滚动条是同一类根因
 * （iframe 跨文档，宿主 CSS/JS 进不去，只能在塞 srcDoc 前注入）。
 *
 * 本模块导出 ensureCanvasBridge(html)：
 *   1) 注入细滚动条 CSS（从 ArtifactPanel 收编 V246 的既有逻辑，单一出口）；
 *   2) 若 HTML 未内置画布协议（检测 "wc:ready"），注入一段自守卫桥脚本，实现协议的
 *      最小完整子集：
 *        wc:theme  → --accent / --wc-accent 变量 + html.dark 切换 + html/body 双写字号
 *                    （body 写内联 px：AI 页常给 body 定死 px，只改 :root 会被盖掉）
 *        wc:ready  → 就绪握手（宿主收到后回推当前主题——工具条即刻生效）
 *        wc:edit   → body contentEditable + 虚线外框 + 输入 1.2s 防抖自动 wc:save
 *        wc:flush  → 立即 wc:save（整页 outerHTML 回传，宿主既有保存链路照常落盘）
 *        wc:apply  → append 兜底（答案插到文末，带 --accent 左边框引用样式）
 *      官方 canvas.js 已在页内时零注入（"wc:ready" 检测命中），互不打架。
 */

const SCROLLBAR_CSS =
  "<style>::-webkit-scrollbar{width:7px;height:7px}::-webkit-scrollbar-track{background:transparent}" +
  "::-webkit-scrollbar-thumb{background:rgba(140,140,140,0.35);border-radius:4px}" +
  "::-webkit-scrollbar-thumb:hover{background:rgba(140,140,140,0.55)}" +
  "html{scrollbar-width:thin;scrollbar-color:rgba(140,140,140,0.35) transparent}</style>";

/* 桥脚本：纯 ES5、自守卫、零依赖；只在页内没有画布协议时注入。 */
const BRIDGE_JS = `<script>(function(){
if (window.__wcBridge) return; window.__wcBridge = 1;
var root = document.documentElement;
var saveTimer = null;
function esc(s){var d=document.createElement('div');d.textContent=String(s||'');return d.innerHTML;}
function applyTheme(m){
  try{
    if (typeof m.dark === 'boolean') root.classList.toggle('dark', m.dark);
    if (typeof m.accent === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(m.accent)) {
      root.style.setProperty('--accent', m.accent);
      root.style.setProperty('--wc-accent', m.accent);
    }
    if (typeof m.fontSize === 'number' && m.fontSize >= 11 && m.fontSize <= 20) {
      root.style.fontSize = m.fontSize + 'px';
      if (document.body) document.body.style.fontSize = m.fontSize + 'px';
    }
  }catch(e){}
}
function saveNow(){
  try{ window.parent.postMessage({type:'wc:save', html:'<!DOCTYPE html>\\n'+document.documentElement.outerHTML}, '*'); }catch(e){}
}
function setEdit(on){
  try{
    if (!document.body) return;
    document.body.contentEditable = on ? 'true' : 'false';
    document.body.style.outline = on ? '1.5px dashed rgba(37,99,235,0.45)' : '';
    document.body.style.outlineOffset = on ? '-1.5px' : '';
    // V256 可视化编辑提示条：明确告诉用户"直接点文字改、自动保存"（不是改源码）
    var tip = document.getElementById('__wc_edit_tip');
    if (on && !tip){
      tip = document.createElement('div');
      tip.id = '__wc_edit_tip';
      tip.textContent = '可视化编辑中 — 点击任意文字直接修改，停顿即自动保存';
      tip.style.cssText = 'position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:99999;'
        + 'background:rgba(37,99,235,0.92);color:#fff;font-size:12px;padding:6px 14px;border-radius:999px;'
        + 'box-shadow:0 2px 10px rgba(0,0,0,0.18);pointer-events:none;font-family:system-ui,sans-serif;';
      document.body.appendChild(tip);
    } else if (!on && tip){ tip.remove(); }
    // V259 选区格式工具条：编辑态选中文字 → 浮出 B/I/U/H2/• 列表/引用 小条（execCommand，
    // 保存走既有防抖链路）；退出编辑即卸载。
    if (on){ document.addEventListener('mouseup', __wcSelBar); document.addEventListener('keyup', __wcSelBar); setTimeout(__wcWordCount, 50); }
    else { document.removeEventListener('mouseup', __wcSelBar); document.removeEventListener('keyup', __wcSelBar); __wcHideBar(); var wc=document.getElementById('__wc_wc'); if (wc) wc.remove(); }
  }catch(e){}
}
function __wcHideBar(){ try{ var b=document.getElementById('__wc_fmt'); if(b) b.remove(); }catch(e){} }
function __wcSelBar(){
  try{
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !String(sel.toString()).trim()){ __wcHideBar(); return; }
    var r = sel.getRangeAt(0).getBoundingClientRect();
    if (!r || (!r.width && !r.height)){ __wcHideBar(); return; }
    var bar = document.getElementById('__wc_fmt');
    if (!bar){
      bar = document.createElement('div');
      bar.id='__wc_fmt';
      bar.style.cssText='position:fixed;z-index:99998;display:flex;gap:2px;padding:4px;border-radius:10px;'
        +'background:rgba(17,24,39,0.94);box-shadow:0 4px 14px rgba(0,0,0,0.25);font-family:system-ui,sans-serif;';
      var mk=function(label,title,fn){
        var b=document.createElement('button');
        b.textContent=label; b.title=title;
        b.style.cssText='all:unset;cursor:pointer;color:#fff;font-size:12px;line-height:1;padding:5px 8px;border-radius:7px;min-width:14px;text-align:center;';
        b.onmouseenter=function(){b.style.background='rgba(255,255,255,0.16)';};
        b.onmouseleave=function(){b.style.background='';};
        b.onmousedown=function(ev){ ev.preventDefault(); ev.stopPropagation(); try{ fn(); clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 900); }catch(e){} };
        return b;
      };
      bar.appendChild(mk('B','加粗',function(){ document.execCommand('bold'); }));
      bar.appendChild(mk('I','斜体',function(){ document.execCommand('italic'); }));
      bar.appendChild(mk('U','下划线',function(){ document.execCommand('underline'); }));
      bar.appendChild(mk('H2','设为小标题',function(){ document.execCommand('formatBlock', false, 'h2'); }));
      bar.appendChild(mk('•','项目符号列表',function(){ document.execCommand('insertUnorderedList'); }));
      bar.appendChild(mk('引','引用块',function(){ document.execCommand('formatBlock', false, 'blockquote'); }));
      bar.appendChild(mk('⌫','清除格式',function(){ document.execCommand('removeFormat'); document.execCommand('formatBlock', false, 'p'); }));
      document.body.appendChild(bar);
    }
    var top = r.top - 40; if (top < 6) top = r.bottom + 8;
    bar.style.top = top + 'px';
    bar.style.left = Math.max(6, Math.min(window.innerWidth - 260, r.left + r.width/2 - 120)) + 'px';
  }catch(e){}
}
// V259 大纲（TOC）：给 h1-h3 编 id 并上报父窗，父侧渲染快速导航；wc:scrollto 跳转。
function __wcToc(){
  try{
    var hs = document.querySelectorAll('h1,h2,h3');
    var toc = [];
    for (var i=0;i<hs.length;i++){
      var h = hs[i];
      if (!h.id) h.id = '__wc_h' + i;
      var txt = (h.textContent||'').trim().slice(0,60);
      if (txt) toc.push({ id: h.id, level: Number(h.tagName[1]||2), text: txt });
    }
    window.parent.postMessage({ type:'wc:toc', items: toc.slice(0,60) }, '*');
  }catch(e){}
}
// ── V268 画布查找替换（大厂编辑器标配）────────────────────────────────────
// wc:find 高亮全部匹配并定位第一处；wc:findnav 上/下一处；wc:replace 当前/全部
// （仅编辑态生效，替换走既有防抖保存）；wc:findclear 清除。高亮用 <mark> 包裹，
// 清除时 unwrap 还原 DOM——不污染保存内容（保存前一律先清）。
var __wcHits = []; var __wcCur = -1;
function __wcFindClear(){
  try{
    var ms = document.querySelectorAll('mark.__wcmk');
    for (var i=0;i<ms.length;i++){
      var mk = ms[i]; var parent = mk.parentNode; if (!parent) continue;
      while (mk.firstChild) parent.insertBefore(mk.firstChild, mk);
      parent.removeChild(mk); parent.normalize();
    }
  }catch(e){}
  __wcHits = []; __wcCur = -1;
  try{ window.parent.postMessage({ type:'wc:findcount', n:0, cur:0 }, '*'); }catch(e){}
}
// V270 画布「插入」：表格 / 待办清单 / 分隔线 / 代码块——光标处优先，否则文末追加；
// 走既有 saveNow 防抖保存链。待办复选框用事件委托常驻可点（编辑/预览态都能勾），
// 勾选状态写回 checked 属性 → 随 HTML 一起持久化。
function __wcInsert(kind){
  try{
    var html='';
    var td='border:1px solid var(--border,#d8dee9);padding:6px 10px;';
    var th=td+'background:var(--bg-secondary,#f2f5fa);text-align:left;font-weight:600;';
    if(kind==='table'){
      html='<table style="border-collapse:collapse;width:100%;margin:12px 0;font-size:0.95em">'
        +'<tr><th style="'+th+'">列 1</th><th style="'+th+'">列 2</th><th style="'+th+'">列 3</th></tr>'
        +'<tr><td style="'+td+'">&nbsp;</td><td style="'+td+'">&nbsp;</td><td style="'+td+'">&nbsp;</td></tr>'
        +'<tr><td style="'+td+'">&nbsp;</td><td style="'+td+'">&nbsp;</td><td style="'+td+'">&nbsp;</td></tr></table>';
    } else if(kind==='todo'){
      var it='<li style="list-style:none;margin:4px 0"><label style="display:flex;gap:8px;align-items:center">'
        +'<input type="checkbox" data-wc-todo contenteditable="false" style="width:14px;height:14px"><span>待办事项</span></label></li>';
      html='<ul style="padding-left:4px;margin:12px 0">'+it+it+it+'</ul>';
    } else if(kind==='divider'){
      html='<hr style="border:none;border-top:1px solid var(--border,#d8dee9);margin:18px 0">';
    } else if(kind==='code'){
      html='<pre style="background:var(--bg-secondary,#f2f5fa);border:1px solid var(--border,#d8dee9);'
        +'border-radius:10px;padding:12px 14px;overflow:auto;margin:12px 0"><code>// 代码</code></pre>';
    } else { return; }
    var node=document.createElement('div'); node.innerHTML=html;
    var frag=document.createDocumentFragment(); while(node.firstChild) frag.appendChild(node.firstChild);
    var placed=false;
    try{
      var sel=window.getSelection&&window.getSelection();
      if(sel&&sel.rangeCount){ var r=sel.getRangeAt(0);
        if(document.body&&document.body.contains(r.commonAncestorContainer)){ r.collapse(false); r.insertNode(frag); placed=true; } }
    }catch(e){}
    if(!placed) (document.body||root).appendChild(frag);
    saveNow();
  }catch(e){}
}
document.addEventListener('click', function(ev){
  var t=ev.target;
  if(!t||!t.matches||!t.matches('input[type=checkbox][data-wc-todo]')) return;
  try{
    if(t.checked) t.setAttribute('checked',''); else t.removeAttribute('checked');
    var sp=t.parentElement&&t.parentElement.querySelector('span');
    if(sp) sp.style.textDecoration=t.checked?'line-through':'';
    saveNow();
  }catch(e){}
});
function __wcFind(q){
  __wcFindClear();
  if (!q) return;
  try{
    var ql = q.toLowerCase();
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function(n){
        if (!n.nodeValue || n.nodeValue.toLowerCase().indexOf(ql) < 0) return NodeFilter.FILTER_REJECT;
        var p = n.parentNode && n.parentNode.nodeName;
        if (p === 'SCRIPT' || p === 'STYLE' || p === 'MARK') return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = []; var nd;
    while ((nd = walker.nextNode())) nodes.push(nd);
    for (var i=0;i<nodes.length && __wcHits.length<500;i++){
      var node = nodes[i];
      while (node){
        var idx = node.nodeValue.toLowerCase().indexOf(ql);
        if (idx < 0) break;
        var range = document.createRange();
        range.setStart(node, idx); range.setEnd(node, idx + q.length);
        var mk = document.createElement('mark');
        mk.className = '__wcmk';
        mk.style.cssText = 'background:#ffe58f;color:inherit;padding:0;border-radius:2px;';
        try{ range.surroundContents(mk); }catch(e){ break; }
        __wcHits.push(mk);
        node = mk.nextSibling;   // 继续在剩余文本里找
      }
    }
  }catch(e){}
  if (__wcHits.length){ __wcCur = 0; __wcFocusHit(); }
  try{ window.parent.postMessage({ type:'wc:findcount', n:__wcHits.length, cur: __wcHits.length?1:0 }, '*'); }catch(e){}
}
function __wcFocusHit(){
  try{
    for (var i=0;i<__wcHits.length;i++)
      __wcHits[i].style.background = (i===__wcCur) ? '#ffb84d' : '#ffe58f';
    var el = __wcHits[__wcCur];
    if (el) el.scrollIntoView({behavior:'smooth', block:'center'});
  }catch(e){}
}
function __wcFindNav(d){
  if (!__wcHits.length) return;
  __wcCur = (__wcCur + d + __wcHits.length) % __wcHits.length;
  __wcFocusHit();
  try{ window.parent.postMessage({ type:'wc:findcount', n:__wcHits.length, cur:__wcCur+1 }, '*'); }catch(e){}
}
function __wcReplace(text, all){
  try{
    if (!document.body || document.body.contentEditable !== 'true') return;  // 只在编辑态
    if (!__wcHits.length) return;
    var doOne = function(mk){ if (mk && mk.parentNode) mk.parentNode.replaceChild(document.createTextNode(text), mk); };
    if (all){ for (var i=0;i<__wcHits.length;i++) doOne(__wcHits[i]); __wcHits=[]; __wcCur=-1; }
    else {
      doOne(__wcHits[__wcCur]); __wcHits.splice(__wcCur,1);
      if (__wcCur >= __wcHits.length) __wcCur = 0;
      __wcFocusHit();
    }
    try{ document.body.normalize(); }catch(e){}
    clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 900);
    window.parent.postMessage({ type:'wc:findcount', n:__wcHits.length, cur: __wcHits.length?__wcCur+1:0 }, '*');
  }catch(e){}
}
// 编辑保存前摘掉提示条（不让它被写进文件）
var _origSaveNow = saveNow;
saveNow = function(){
  try{ var tip = document.getElementById('__wc_edit_tip'); if (tip) tip.remove();
       __wcHideBar();
       if (__wcHits.length) __wcFindClear();   // 高亮 mark 不能存进内容
       _origSaveNow();
       if (document.body && document.body.contentEditable === 'true') setEdit(true);
       __wcToc();
  }catch(e){ _origSaveNow(); }
};
try{ if (document.readyState === 'complete' || document.readyState === 'interactive') setTimeout(__wcToc, 60);
     else document.addEventListener('DOMContentLoaded', function(){ setTimeout(__wcToc, 60); }); }catch(e){}
document.addEventListener('input', function(){
  if (!document.body || document.body.contentEditable !== 'true') return;
  clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 1200);
  __wcWordCount();
}, true);
// V267 字数统计条：编辑态右下角常驻（字数·约读几分钟），写长文时心里有数
function __wcWordCount(){
  try{
    if (!document.body || document.body.contentEditable !== 'true'){ 
      var old = document.getElementById('__wc_wc'); if (old) old.remove(); return;
    }
    var txt = (document.body.innerText || '').replace(/\s+/g, '');
    var n = txt.length;
    var mins = Math.max(1, Math.round(n / 400));
    var el = document.getElementById('__wc_wc');
    if (!el){
      el = document.createElement('div');
      el.id = '__wc_wc';
      el.style.cssText = 'position:fixed;right:10px;bottom:10px;z-index:99997;padding:3px 9px;'
        + 'border-radius:999px;background:rgba(17,24,39,0.72);color:#fff;font-size:10.5px;'
        + 'font-family:system-ui,sans-serif;pointer-events:none;';
      document.body.appendChild(el);
    }
    el.textContent = n + ' 字 · 约 ' + mins + ' 分钟';
  }catch(e){}
}
window.addEventListener('message', function(e){
  var m = e && e.data; if (!m || typeof m !== 'object') return;
  if (m.type === 'wc:theme') applyTheme(m);
  else if (m.type === 'wc:edit') setEdit(!!m.on);
  else if (m.type === 'wc:scrollto'){ try{ var el=document.getElementById(String(m.id||'')); if(el) el.scrollIntoView({behavior:'smooth',block:'start'}); }catch(e){} }
  else if (m.type === 'wc:gettoc') __wcToc();
  else if (m.type === 'wc:find') __wcFind(String(m.q||''));
  else if (m.type === 'wc:findnav') __wcFindNav(m.dir === 'prev' ? -1 : 1);
  else if (m.type === 'wc:replace') __wcReplace(String(m.text||''), !!m.all);
  else if (m.type === 'wc:findclear') __wcFindClear();
  else if (m.type === 'wc:insert') __wcInsert(String(m.kind||''));
  else if (m.type === 'wc:flush') saveNow();
  else if (m.type === 'wc:apply' && typeof m.text === 'string' && m.mode !== 'cancel') {
    try{
      var d = document.createElement('div');
      d.style.cssText = 'margin:14px 0;padding:10px 14px;border-left:3px solid var(--accent,#2563eb);border-radius:0 10px 10px 0;white-space:pre-wrap';
      d.innerHTML = esc(m.text);
      (document.body || root).appendChild(d);
      saveNow();
    }catch(e2){}
  }
});
try{ window.parent.postMessage({type:'wc:ready'}, '*'); }catch(e){}
})();</script>`;

/** 是否已内置画布协议（官方 canvas.js 或任何自带 wc:ready 握手的页面）。 */
function hasCanvasProtocol(html: string): boolean {
  return html.includes("wc:ready");
}

function injectBeforeBodyEnd(html: string, snippet: string): string {
  if (html.includes("</body>")) return html.replace("</body>", snippet + "</body>");
  return html + snippet;
}

function injectScrollbarCss(html: string): string {
  if (!html) return html;
  if (html.includes("::-webkit-scrollbar")) return html;   // 页面已自带（官方模板/V246 注入过）
  if (html.includes("</head>")) return html.replace("</head>", SCROLLBAR_CSS + "</head>");
  if (html.includes("<body")) return html.replace(/(<body[^>]*>)/i, "$1" + SCROLLBAR_CSS);
  return SCROLLBAR_CSS + html;
}

/** 统一出口：滚动条 CSS + （缺协议时）运行时桥。幂等，可重复调用。 */
export function ensureCanvasBridge(html: string): string {
  if (!html) return html;
  let out = injectScrollbarCss(html);
  if (!hasCanvasProtocol(out)) out = injectBeforeBodyEnd(out, BRIDGE_JS);
  return out;
}
