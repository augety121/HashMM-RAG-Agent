// lib/sanitize-html.ts — 渲染前 HTML 危险内容净化（V306，修 DESK-P0-03 渲染净化不足）。
//
// 背景：renderMsg 会把 LLM/文件内容拼进 HTML 再经 dangerouslySetInnerHTML 注入。旧的白名单
// 转义允许 <div>/<td> 等标签**带任意属性**通过（如 <div onclick=...>、<td onmouseover=...>），
// 且未剥离 <script>/<iframe>/<svg>/javascript: 等——桌面端 sandbox:false + 宽 IPC 下，这会形成
// "模型输出→脚本注入→高权限 IPC→文件/Shell/CU"的链（DESK-P0-03）。
//
// 本模块是**渲染前**的纯文本净化（零 DOM 依赖，SSR/浏览器都可用）：先去掉最危险的东西，
// 再交给 renderMsg 做 Markdown→HTML。它不是完整的 DOM 级消毒（那需要 DOMPurify），而是
// 针对本渲染管线已知注入面的定向收口，可被单测覆盖。

const _DANGEROUS_TAGS = [
  "script", "iframe", "object", "embed", "svg", "math",
  "style", "link", "meta", "base", "form", "input", "button",
  "textarea", "template", "noscript", "frame", "frameset", "applet",
];

/** 去掉一段 HTML/文本里的危险内容：危险标签（含其内容）、事件处理器属性、危险协议。 */
export function stripDangerousHtml(input: string): string {
  let t = String(input == null ? "" : input);

  // 1) 成对危险标签连同内容整体删除（<script>...</script> 等）。用非贪婪 + i 大小写不敏感。
  for (const tag of _DANGEROUS_TAGS) {
    t = t.replace(new RegExp(`<${tag}\\b[^>]*>[\\s\\S]*?<\\/${tag}\\s*>`, "gi"), "");
    // 自闭合 / 未闭合的孤立标签也删（<img> 这类留给 Markdown 层，这里只删危险集合）
    t = t.replace(new RegExp(`<\\/?${tag}\\b[^>]*>`, "gi"), "");
  }

  // 2) 事件处理器属性：on\w+ = 值。
  //    V306 property-fuzz 修真绕过：分隔符不止空白——浏览器也接受 `/` 作属性分隔（如
  //    `<img/onerror=alert(1)>` 会触发）。故分隔符用 [\s/]，值覆盖双引号/单引号/无引号/空；
  //    并 fixpoint 循环到不再变化，清掉相邻/链式处理器。
  const _onHandler = /[\s/]on[a-z0-9_-]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]*)/gi;
  for (let i = 0; i < 8; i++) {
    const before = t;
    t = t.replace(_onHandler, " ");
    if (t === before) break;
  }

  // 3) 危险 URI 协议（javascript:/vbscript:/data:text-html）——在 href/src 或裸文本里都中和。
  t = t.replace(/(href|src|xlink:href)\s*=\s*(["']?)\s*javascript:[^"'>\s]*/gi, "$1=$2#");
  t = t.replace(/(href|src|xlink:href)\s*=\s*(["']?)\s*vbscript:[^"'>\s]*/gi, "$1=$2#");
  t = t.replace(/(href|src)\s*=\s*(["']?)\s*data:text\/html[^"'>\s]*/gi, "$1=$2#");
  // 裸出现的 javascript: 伪协议（防被后续拼进属性）
  t = t.replace(/javascript:/gi, "javascript\u200b:");

  // 4) V308 跨 token 防线（呼应审计 P0-4）：流式渲染中危险标签可能被切成多段陆续到达
  //    （"<scr"+"ipt>"、"<img sr"+"c=x onerror=..."）。上面的成对/单标签规则只在标签
  //    【完整】时匹配；对"行尾残缺的危险标签开头"（如以 "<script" 结尾、闭合尖括号还没到），
  //    这里把该残段的 "<" 转义成 "&lt;"，使其在本帧无法形成可执行标签。下一帧补全后
  //    仍会走完整规则被删除。只针对危险标签集合，不影响正常 Markdown/表格标签。
  const _dangerHead = new RegExp(`<\\s*/?\\s*(?:${_DANGEROUS_TAGS.join("|")})\\b[^>]*$`, "i");
  t = t.replace(_dangerHead, (m) => "&lt;" + m.slice(1));

  return t;
}

export const DANGEROUS_TAGS = _DANGEROUS_TAGS;
