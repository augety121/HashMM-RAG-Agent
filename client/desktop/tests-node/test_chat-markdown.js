/** test_chat-markdown.js — 安全 Markdown 渲染器单测（含 XSS 防护）。 */
"use strict";
const assert = require("assert");
const M = require("../chat-markdown.js");
const R = M.renderMarkdown;

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { console.log("  ✗ " + name + "  <<< FAIL"); process.exitCode = 1; }
}

console.log("=== XSS 安全（最重要）===");
ok("脚本标签被转义", !R("<script>alert(1)</script>").includes("<script>"));
ok("img 标签被转义(非真标签)", !/<img/i.test(R('<img src=x onerror=alert(1)>')) && R('<img src=x onerror=alert(1)>').includes("&lt;img"));
ok("javascript: 链接被挡", !R("[x](javascript:alert(1))").includes("javascript:"));
ok("data: 链接被挡", !R("[x](data:text/html,xx)").includes("data:text/html"));
ok("正常 http 链接保留", R("[x](https://a.com)").includes('href="https://a.com"'));
ok("链接带 noopener", R("[x](https://a.com)").includes("noopener"));
ok("行内代码内容转义", R("`<b>`").includes("&lt;b&gt;"));
ok("代码块内容转义", R("```\n<script>\n```").includes("&lt;script&gt;"));

console.log("=== 代码块 ===");
const cb = R("```python\nprint(1)\n```");
ok("产出 pre>code", cb.includes("<pre>") && cb.includes("<code"));
ok("语言 class", cb.includes("lang-python"));
ok("语言标签", cb.includes("code-lang"));
ok("代码内容保留", cb.includes("print(1)"));
const cbNoLang = R("```\nplain\n```");
ok("无语言也成块", cbNoLang.includes("<pre>") && cbNoLang.includes("plain"));
ok("代码块内 # 不当标题", R("```\n# not heading\n```").includes("# not heading") && !R("```\n# not heading\n```").includes("<h1>"));

console.log("=== 标题 / 分隔线 ===");
ok("# h1", R("# 标题").includes("<h1>标题</h1>"));
ok("### h3", R("### 三级").includes("<h3>三级</h3>"));
ok("###### h6", R("###### 六级").includes("<h6>"));
ok("分隔线", R("---").includes("<hr>"));

console.log("=== 列表 ===");
const ul = R("- a\n- b");
ok("无序列表", ul.includes("<ul>") && (ul.match(/<li>/g) || []).length === 2);
const ol = R("1. one\n2. two");
ok("有序列表", ol.includes("<ol>") && (ol.match(/<li>/g) || []).length === 2);
ok("列表项格式化", R("- **粗**").includes("<strong>粗</strong>"));
ok("* 也是无序", R("* x").includes("<ul>"));
ok("列表后关闭", R("- a\n\n段落").includes("</ul>"));

console.log("=== 行内格式 ===");
ok("粗体", R("**粗**").includes("<strong>粗</strong>"));
ok("__粗体__", R("__粗__").includes("<strong>粗</strong>"));
ok("斜体", R("这是 *斜* 的").includes("<em>斜</em>"));
ok("删除线", R("~~删~~").includes("<del>删</del>"));
ok("行内代码", R("用 `code` 包").includes("<code>code</code>"));
ok("行内代码不被斜体吃", !R("`a*b*c`").includes("<em>"));

console.log("=== 引用 / 段落 / 换行 ===");
ok("引用", R("> 引用文字").includes("<blockquote>") && R("> 引用文字").includes("引用文字"));
ok("段落", R("普通文字").includes("<p>普通文字</p>"));
const multiline = R("第一行\n第二行");
ok("段内换行→br", multiline.includes("<br>"));
ok("空行分段", (R("段一\n\n段二").match(/<p>/g) || []).length === 2);

console.log("=== 综合 / 边界 ===");
const combo = R("# 标题\n\n正文 **粗** 和 `代码`\n\n```js\nlet x=1\n```\n\n- 项1\n- 项2");
ok("综合：标题", combo.includes("<h1>"));
ok("综合：代码块", combo.includes("<pre>"));
ok("综合：列表", combo.includes("<ul>"));
ok("空字符串→空", R("") === "");
ok("null 不崩", typeof R(null) === "string");
ok("纯换行不崩", typeof R("\n\n\n") === "string");

console.log("\n结果：PASS=" + pass + (process.exitCode ? "  有失败" : "  全部通过"));
