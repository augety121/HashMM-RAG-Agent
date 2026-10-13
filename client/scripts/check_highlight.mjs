#!/usr/bin/env node
/**
 * scripts/check_highlight.mjs — lib/highlight.ts 的沙箱校验（无需 node_modules）
 *
 * 不变量：
 *   1. 无损：strip(<span>) + unescape == 原文（任何语言、任何输入，一个字符都不能丢/变）
 *   2. 安全：输出中不存在未转义的 < > &（除 span 标签本身）
 *   3. 覆盖：各语言的关键字/字符串/注释确实被着色
 *   4. 按行：highlightToLines 行数 == 原文行数，跨行 token 正确分割
 *
 * 运行：node scripts/check_highlight.mjs   （应输出 ALL N CHECKS PASSED）
 */
import { createRequire } from "module";
import { readFileSync, writeFileSync, mkdtempSync } from "fs";
import { tmpdir } from "os";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

// 转译 TS → CJS 后 require
let ts;
try { ts = require("typescript"); }
catch { ts = require(join(process.env.HOME || "/root", ".npm-global/lib/node_modules/typescript")); }
const src = readFileSync(join(root, "frontend-next/lib/highlight.ts"), "utf8");
const out = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 },
});
const tmp = mkdtempSync(join(tmpdir(), "hl-"));
writeFileSync(join(tmp, "highlight.cjs"), out.outputText);
const { highlightCode, highlightToLines, langFromFilename } = require(join(tmp, "highlight.cjs"));

const unesc = s => s.replace(/<span class="hl-\w+">/g, "").replace(/<\/span>/g, "")
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

let n = 0, failed = 0;
function check(name, cond) {
  n++;
  if (!cond) { failed++; console.error(`  ✗ ${name}`); }
  else console.log(`  ✓ ${name}`);
}

const SAMPLES = {
  cpp: `/* 多行
注释 */
#include <iostream>
template <typename Key, typename Compare = std::less<Key>>
class RBTree {
  int black_height(Node* node) { // 行注释
    if (node == nil_) return 1;
    std::string s = "RED \\" quote";
  }
};`,
  python: `@decorator
def hello(name: str) -> bool:
    '''doc
    string'''
    x = 0x1F + 3.14e-2  # comment
    return f"hi {name}" if x else None`,
  javascript: "const f = async (x) => {\n  // note\n  return `tpl ${x} <tag>` + \"str\";\n};",
  bash: `#!/bin/bash
g++ -std=c++17 -O2 main.cpp -o rbtree_demo
OUT=$HOME/build; echo built: $OUT \${VER} # done`,
  json: `{"name": "hashmm", "ok": true, "n": 3.14, "arr": [1, 2]}`,
  sql: `SELECT id, COUNT(*) FROM users WHERE age > 18 -- adults
GROUP BY id;`,
  html: `<!-- c --><div class="box" id=main>5 &amp; 6</div>`,
  yaml: `# conf
name: hashmm
port: 6006
debug: true`,
  go: `func main() {\n\t// hi\n\tfmt.Println("go", 42)\n}`,
  rust: `fn main() {\n    let mut v: Vec<i32> = vec![1, 2]; // c\n    println!("{}", v.len());\n}`,
};

// 1+2. 无损 + 转义安全（含恶意输入）
const evil = `<script>alert("xss & </code>")</script>\n'unterminated\n"also`;
for (const [lang, code] of [...Object.entries(SAMPLES), ["cpp", evil], ["text", evil], ["python", ""], ["json", "{"]]) {
  const html = highlightCode(code, lang);
  check(`${lang}: 无损往返`, unesc(html) === code);
  check(`${lang}: 无裸标签`, !/<(?!span class="hl-\w+">|\/span>)/.test(html));
}

// 3. token 覆盖
check("cpp 关键字着色", highlightCode(SAMPLES.cpp, "cpp").includes('hl-kw">class<'));
check("cpp 块注释跨行着色", highlightCode(SAMPLES.cpp, "cpp").includes('hl-com">/* 多行'));
check("cpp 预处理着色", highlightCode(SAMPLES.cpp, "cpp").includes('hl-pre">#include<'));
check("py 三引号串着色", highlightCode(SAMPLES.python, "python").includes("hl-str\">'''doc"));
check("py 装饰器着色", highlightCode(SAMPLES.python, "python").includes('hl-pre">@decorator<'));
check("js 模板串着色", /hl-str">`tpl/.test(highlightCode(SAMPLES.javascript, "javascript")));
check("bash 变量着色", highlightCode(SAMPLES.bash, "bash").includes('hl-var">'));
check("json 键值区分", (() => { const h = highlightCode(SAMPLES.json, "json");
  return h.includes('hl-attr">&quot;name&quot;'.replace(/&quot;/g, "\"")) || h.includes('hl-attr">"name"'); })());
check("sql 大小写不敏感", /hl-kw">SELECT</.test(highlightCode(SAMPLES.sql, "sql")));
check("html 标签着色", /hl-tag">&lt;div</.test(highlightCode(SAMPLES.html, "html")));

// 4. 按行
for (const [lang, code] of Object.entries(SAMPLES)) {
  const lines = highlightToLines(code, lang);
  check(`${lang}: 行数一致`, lines.length === code.split("\n").length);
  check(`${lang}: 按行无损`, lines.map(unesc).join("\n") === code);
}
check("跨行注释每行着色", (() => {
  const lines = highlightToLines(SAMPLES.cpp, "cpp");
  return lines[0].includes("hl-com") && lines[1].includes("hl-com");
})());

// 5. 扩展名映射
check("langFromFilename", langFromFilename("main.cpp") === "cpp"
  && langFromFilename("a.py") === "python" && langFromFilename("x.unknown") === "text");

// 6. 性能护栏：5000 行文件 < 1s
const big = Array.from({ length: 5000 }, (_, i) => `void f${i}(int x) { return x + ${i}; } // c`).join("\n");
const t0 = Date.now();
highlightToLines(big, "cpp");
const dt = Date.now() - t0;
check(`5000 行性能 (${dt}ms < 1000ms)`, dt < 1000);

console.log(failed === 0 ? `\nALL ${n} CHECKS PASSED` : `\n${failed}/${n} FAILED`);
process.exit(failed === 0 ? 0 : 1);
