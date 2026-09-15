/** 画布 2.0 模板库（V225）——「点击即开」升级为四模板一键起稿。
 *  单一 shell（样式+迷你运行时）注入不同起始正文；运行时与技能包
 *  skills/packs/work-canvas/assets/canvas.js 协议段保持同步（两处注释互指）。
 *  V225 运行时升级：选中提问时给原文打标记（高亮），答案可「替换原文/插入文末/取消」。 */

function canvasShell(title: string, subtitle: string, bodyHtml: string): string {
  const t = title.replace(/</g, "&lt;");
  const sub = subtitle.replace(/</g, "&lt;");
  return `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${t}</title>
<style>
:root{--accent:#2563eb;--bg:#fff;--fg:#1a1a1a;--muted:#6b7280;--border:#e5e7eb;--card:#f8fafc}
html.dark{--bg:#0b0b0d;--fg:#ececf1;--muted:#9ca3af;--border:#27272a;--card:#141417}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.75 Inter,'Noto Sans SC',system-ui,sans-serif;padding:28px 30px 90px}::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:rgba(128,128,128,0.25);border-radius:4px}::-webkit-scrollbar-thumb:hover{background:rgba(128,128,128,0.45)}html{scrollbar-width:thin;scrollbar-color:rgba(128,128,128,0.25) transparent}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--muted);font-size:12.5px;margin-bottom:18px}
h2{font-size:15px;margin:22px 0 8px;padding-left:9px;border-left:3px solid var(--accent)}
ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
.wc-cols{display:flex;gap:14px;flex-wrap:wrap}.wc-card{flex:1;min-width:240px;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.wc-card h3{margin:0 0 8px;font-size:13.5px}
.wc-tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:99px;background:color-mix(in srgb,var(--accent) 12%,transparent);color:var(--accent);margin-right:6px}
.wc-warn{color:#b45309}.wc-ok{color:#15803d}
.wc-asked{background:color-mix(in srgb,var(--accent) 14%,transparent);border-bottom:1.5px dashed var(--accent);border-radius:3px}
#wc-body{min-height:220px;outline:none}#wc-body:empty:before{content:"这是你的工作画布。点右上「编辑」直接书写；写好的内容 agent 下一轮就能看到。也可以先在下方输入框让 agent 起个头。";color:var(--muted)}
#wc-body[contenteditable=true]{border:1.5px dashed color-mix(in srgb,var(--accent) 45%,transparent);border-radius:12px;padding:14px;background:color-mix(in srgb,var(--accent) 4%,transparent)}
.wc-hint{position:fixed;left:50%;transform:translateX(-50%);bottom:60px;background:var(--fg);color:var(--bg);font-size:12px;padding:6px 12px;border-radius:99px;opacity:0;transition:.25s;pointer-events:none}
.wc-hint.on{opacity:.92}
#wc-ask{position:absolute;display:none;z-index:9;background:var(--accent);color:#fff;border:0;border-radius:99px;font-size:12px;padding:5px 11px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.18)}
footer{position:fixed;left:0;right:0;bottom:0;padding:10px 30px;font-size:11px;color:var(--muted);background:linear-gradient(transparent,var(--bg) 40%)}
</style></head><body>
<h1>${t}</h1><div class="sub">${sub}</div>
<div id="wc-body">${bodyHtml}</div>
<button id="wc-ask" type="button">问一下</button>
<div class="wc-hint" id="wc-hint"></div>
<footer>HashMM 工作画布 · 编辑自动保存并进版本历史 · 划选可就地提问</footer>
<script>
/* mini 运行时（与技能包 canvas.js V225 协议段同步）*/
(function(){
var host=window.parent,editing=false,t=null,hintT=null;
function post(m){try{host.postMessage(m,"*")}catch(e){}}
function hint(s){var h=document.getElementById("wc-hint");h.textContent=s;h.classList.add("on");clearTimeout(hintT);hintT=setTimeout(function(){h.classList.remove("on")},1800)}
function root(){return document.getElementById("wc-body")}
function save(){post({type:"wc:save",html:"<!DOCTYPE html>\\n"+document.documentElement.outerHTML});hint("已保存")}
function findAsk(id){return document.querySelector('[data-wc-ask="'+id+'"]')}
function apply(m){
 var mode=m.mode||"append";
 if(mode==="cancel"){var sp=findAsk(m.askId);if(sp){while(sp.firstChild)sp.parentNode.insertBefore(sp.firstChild,sp);sp.remove();}return}
 if(mode==="replace"){var sp2=findAsk(m.askId);
  if(sp2){var rep=document.createElement("span");rep.style.whiteSpace="pre-wrap";rep.textContent=String(m.text);sp2.replaceWith(rep);hint("已替换原文");save();return}}
 var d=document.createElement("div");d.style.cssText="margin:14px 0;padding:10px 14px;border-left:3px solid var(--accent);background:color-mix(in srgb,var(--accent) 6%,transparent);border-radius:0 10px 10px 0;white-space:pre-wrap";d.textContent=String(m.text);root().appendChild(d);hint("已插入文末");save();
}
window.addEventListener("message",function(e){var m=e.data||{};
 if(m.type==="wc:theme"){document.documentElement.classList.toggle("dark",!!m.dark);if(m.accent)document.documentElement.style.setProperty("--accent",m.accent);if(m.fontSize)document.body.style.fontSize=m.fontSize+"px";}
 if(m.type==="wc:edit"){editing=!!m.on;root().contentEditable=editing?"true":"false";hint(editing?"编辑中 · 停笔 1 秒自动保存":"已退出编辑");}
 if(m.type==="wc:flush"){save();}
 if(m.type==="wc:apply"){apply(m);}
});
document.addEventListener("input",function(ev){if(!editing)return;clearTimeout(t);t=setTimeout(save,1200)});
/* 选中即问：划选 → 标记原文（高亮）→ wc:ask{text,context,askId}，宿主可回投 替换/插入/取消 */
var ask=document.getElementById("wc-ask");
document.addEventListener("mouseup",function(){setTimeout(function(){
 var s=window.getSelection(),txt=s?String(s.toString()).trim():"";
 if(!txt||txt.length<2||editing){ask.style.display="none";return}
 var range=s.getRangeAt(0),rect=range.getBoundingClientRect();
 ask.style.display="block";ask.style.left=(window.scrollX+rect.left+rect.width/2-34)+"px";ask.style.top=(window.scrollY+rect.top-38)+"px";
 ask.onclick=function(){
  var id="a"+Date.now();
  var sp=document.createElement("span");sp.className="wc-asked";sp.setAttribute("data-wc-ask",id);
  try{range.surroundContents(sp)}catch(err){try{sp.appendChild(range.extractContents());range.insertNode(sp)}catch(e2){sp=null}}
  var full=root().innerText||"";var i=full.indexOf(txt);
  var ctx=i>=0?full.slice(Math.max(0,i-200),i+txt.length+200):txt;
  post({type:"wc:ask",text:txt,context:ctx,askId:sp?id:""});
  ask.style.display="none";try{s.removeAllRanges()}catch(e){}
 };
},10)});
/* V230 数据看板：SVG 迷你图表——读 .wc-chart 的 data-* 现画现改（编辑数据后点「重画」） */
window.wcChartAll=function(){document.querySelectorAll(".wc-chart").forEach(function(box){
 try{
  var labels=(box.getAttribute("data-labels")||"").split(",").map(function(x){return x.trim()}).filter(Boolean);
  var vals=(box.getAttribute("data-values")||"").split(",").map(function(x){return parseFloat(x)||0});
  var type=box.getAttribute("data-type")||"bar";var W=560,H=200,P=28;var max=Math.max.apply(null,vals.concat([1]));
  var n=Math.max(vals.length,1),iw=(W-P*2)/n;var svg='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;max-width:'+W+'px">';
  svg+='<line x1="'+P+'" y1="'+(H-P)+'" x2="'+(W-P)+'" y2="'+(H-P)+'" stroke="var(--border)"/>';
  if(type==="line"){var pts=vals.map(function(v,i){return (P+iw*i+iw/2)+","+(H-P-(v/max)*(H-P*2))}).join(" ");
   svg+='<polyline points="'+pts+'" fill="none" stroke="var(--accent)" stroke-width="2.5"/>';
   vals.forEach(function(v,i){svg+='<circle cx="'+(P+iw*i+iw/2)+'" cy="'+(H-P-(v/max)*(H-P*2))+'" r="3.5" fill="var(--accent)"/>'});
  }else{vals.forEach(function(v,i){var bh=(v/max)*(H-P*2);
   svg+='<rect x="'+(P+iw*i+iw*0.18)+'" y="'+(H-P-bh)+'" width="'+(iw*0.64)+'" height="'+bh+'" rx="4" fill="var(--accent)" opacity="0.85"/>';
   svg+='<text x="'+(P+iw*i+iw/2)+'" y="'+(H-P-bh-5)+'" font-size="10" text-anchor="middle" fill="var(--muted)">'+v+'</text>'});}
  labels.forEach(function(l,i){svg+='<text x="'+(P+iw*i+iw/2)+'" y="'+(H-P+14)+'" font-size="10" text-anchor="middle" fill="var(--muted)">'+String(l).replace(/</g,"&lt;")+'</text>'});
  svg+='</svg>';box.querySelector(".wc-chart-svg").innerHTML=svg;
 }catch(e){}
});};
window.wcChartAll();
post({type:"wc:ready"});
})();
</script></body></html>`;
}

const PROGRESS_BODY = `
<h2>本轮概览</h2><ul><li>目标：<span class="wc-tag">填写</span>一句话说清这轮要交付什么</li><li>整体进度：约 __%</li></ul>
<h2>已完成</h2><ul><li>…（改了哪些文件 / 关键 diff）</li></ul>
<h2>进行中</h2><ul><li>…</li></ul>
<h2>风险与待拍板</h2><ul><li class="wc-warn">…（需要用户决定的问题写这里）</li></ul>
<h2>下一步</h2><ul><li>…</li></ul>`;

const REVIEW_BODY = `
<h2>结论</h2><ul><li><span class="wc-tag">通过 / 有条件通过 / 打回</span>一句话总评</li></ul>
<h2>亮点</h2><ul><li class="wc-ok">…</li></ul>
<h2>问题清单</h2><ul><li class="wc-warn">P0 ｜ 位置 ｜ 问题 ｜ 建议修法</li><li>P1 ｜ … ｜ … ｜ …</li></ul>
<h2>建议修改</h2><ul><li>…</li></ul>`;

const COMPARE_BODY = `
<h2>候选方案</h2>
<div class="wc-cols">
  <div class="wc-card"><h3>方案 A：<span class="wc-tag">名称</span></h3><ul><li>优点：…</li><li>缺点：…</li><li>成本/工期：…</li></ul></div>
  <div class="wc-card"><h3>方案 B：<span class="wc-tag">名称</span></h3><ul><li>优点：…</li><li>缺点：…</li><li>成本/工期：…</li></ul></div>
</div>
<h2>关键维度对比</h2><ul><li>性能：A … ｜ B …</li><li>可维护性：A … ｜ B …</li><li>风险：A … ｜ B …</li></ul>
<h2>结论与理由</h2><ul><li class="wc-ok">倾向：__，因为 …</li></ul>`;

const DATA_BODY = `
<h2>数据看板 <span class="wc-tag">改完数据点「重画」</span></h2>
<div class="wc-chart" data-type="bar" data-labels="一月,二月,三月,四月" data-values="120,180,150,220">
  <div style="font-size:12px;color:var(--muted)">柱状 · 双击下方数据行编辑 data-values / data-labels</div>
  <div class="wc-chart-svg"></div>
</div>
<div class="wc-chart" data-type="line" data-labels="周一,周二,周三,周四,周五" data-values="35,42,38,51,47">
  <div style="font-size:12px;color:var(--muted)">折线 · 同上可改</div>
  <div class="wc-chart-svg"></div>
</div>
<p><button onclick="wcChartAll()" style="font-size:12px;padding:5px 14px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:var(--fg);cursor:pointer">重画全部图表</button></p>
<h2>数据说明</h2><ul><li>口径：…</li><li>结论：…</li></ul>`;

// V294 多智能体作战室：画布 × 多 agent 的结合点。人来当"总指挥"——在画布上写清目标、
// 编队与分工、交接约定、验收标准；派活后各角色产出贴回对应卡片，冲突/裁决留档在"作战记录"。
// 划选任意分工可就地"问一下"让 agent 细化——这正是把画布用成多智能体协作观察窗的模板底座。
const WARROOM_BODY = `
<h2>作战目标</h2><ul><li><span class="wc-tag">一句话</span>本次多智能体要合力交付什么（越具体越好）</li><li>截止/预算约束：…</li></ul>
<h2>编队与分工 <span class="wc-tag">2-4 个角色</span></h2>
<div class="wc-cols">
  <div class="wc-card"><h3>研究员</h3><ul><li>分工：收集与目标直接相关的事实/数据/出处</li><li>产出贴这里：…</li></ul></div>
  <div class="wc-card"><h3>分析员</h3><ul><li>分工：利弊/风险/优先级，给可比较的结论</li><li>产出贴这里：…</li></ul></div>
  <div class="wc-card"><h3>写作员</h3><ul><li>分工：把结论组织成可直接使用的成稿</li><li>产出贴这里：…</li></ul></div>
</div>
<h2>交接约定</h2><ul><li>模式：<span class="wc-tag">并行 / 流水线</span>（流水线时后一棒必须先读前面全部产出）</li><li>冲突裁决：谁说了算 / 按什么标准</li></ul>
<h2>验收标准</h2><ul><li class="wc-ok">必须满足：…</li><li class="wc-warn">不允许出现：编造事实 / 遗漏关键风险 / 互相矛盾未标注</li></ul>
<h2>作战记录</h2><ul><li>[时间] 谁 → 做了什么 / 裁决了什么：…</li></ul>`;

export type CanvasTplKind = "blank" | "progress" | "review" | "compare" | "data" | "warroom";

export function canvasTemplateHtml(kind: CanvasTplKind): string {
  switch (kind) {
    case "progress": return canvasShell("进度报告", "本轮做了什么 · 风险 · 待拍板（可编辑，agent 可续写）", PROGRESS_BODY);
    case "review": return canvasShell("评审意见", "结论 · 亮点 · 问题清单（可编辑，agent 可续写）", REVIEW_BODY);
    case "compare": return canvasShell("方案对比", "候选 · 维度 · 结论（可编辑，agent 可续写）", COMPARE_BODY);
    case "data": return canvasShell("数据看板", "SVG 图表 · 改数据即重画（可编辑，agent 可续写）", DATA_BODY);
    case "warroom": return canvasShell("多智能体作战室", "目标 · 编队分工 · 交接约定 · 验收标准（划选分工可就地让 agent 细化）", WARROOM_BODY);
    default: return canvasShell("工作画布", "工作画布 · 可编辑 · 划选任意内容可就地提问（不打扰主对话）", "");
  }
}

/** 兼容旧引用：空白画布。 */
export function blankCanvasHtml(_title: string): string { return canvasTemplateHtml("blank"); }
