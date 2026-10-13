/**
 * lib/highlight.ts — 零依赖语法高亮（V49）
 *
 * 为什么不用 Prism/Shiki：项目铁律是零新增重依赖（真机不便随意 npm install），
 * 且此前 CodeBlock 里的 window.Prism 分支因 Prism 从未打进 bundle 而从未生效。
 * 这里用单遍正则 tokenizer 实现一套自有高亮：
 *   - 输出的 HTML 一定是 escape 过的（XSS 安全），且字符一个不丢
 *     （strip 掉 span 标签再反转义 == 原文，有 node 测试锁住这一点）
 *   - highlightToLines() 按行输出：跨行 token（块注释/三引号字符串）会被
 *     正确切割到每一行，供"行号 + 自动换行"的渲染场景使用
 *
 * 用法：
 *   highlightCode(code, "cpp")        → 整块 HTML
 *   highlightToLines(code, "python")  → 每行一段 HTML
 *   langFromFilename("main.cpp")      → "cpp"
 */

// token 类型 → CSS class 后缀（颜色定义见 v29-components.css 的 .hl-*）
//   kw 关键字 / str 字符串 / com 注释 / num 数字与常量 / fn 函数名 /
//   type 类型与内建 / pre 预处理与装饰器 / var 变量插值 / attr 属性键 / tag 标签
type TokType = "kw" | "str" | "com" | "num" | "fn" | "type" | "pre" | "var" | "attr" | "tag";

interface LangCfg {
  re: RegExp;
  groups: TokType[];
  /** json 等需要的后处理钩子 */
  post?: (toks: Tok[]) => void;
}

interface Tok {
  t: TokType | null;
  s: string;
}

const R = String.raw;

// m 标志下 $ 是行尾；未闭合容忍必须用"真正的文本末尾"，否则懒惰匹配在首行就停
const END = R`$(?![\s\S])`;

function build(parts: Array<[TokType, string]>, flags = "gm"): LangCfg {
  return {
    re: new RegExp(parts.map(p => `(${p[1]})`).join("|"), flags),
    groups: parts.map(p => p[0]),
  };
}

// ── 通用片段 ──
const NUM = R`\b0[xX][0-9a-fA-F_]+\b|\b0[bB][01_]+\b|\b\d[\d_]*(?:\.[\d_]+)?(?:[eE][+-]?\d+)?[fFlLuUn]*\b`;
const DQ_STR = R`"(?:\\.|[^"\\\n])*"?`;       // 双引号（容忍未闭合，行内）
const SQ_STR = R`'(?:\\.|[^'\\\n])*'?`;       // 单引号
const FN = R`[A-Za-z_$][\w$]*(?=\s*\()`;

const kw = (words: string) => R`\b(?:` + words.trim().split(/\s+/).join("|") + R`)\b`;

// ── 语言配置 ──

const CPP = build([
  ["com", R`\/\*[\s\S]*?(?:\*\/|${END})`],
  ["com", R`\/\/[^\n]*`],
  ["pre", R`^[ \t]*#[ \t]*\w+`],
  ["str", DQ_STR], ["str", SQ_STR],
  ["kw", kw(`if else for while do switch case default break continue return goto sizeof
    typedef struct union enum class public private protected virtual override final
    template typename namespace using new delete this operator const static extern
    inline friend constexpr consteval mutable volatile explicit try catch throw noexcept
    static_cast dynamic_cast const_cast reinterpret_cast auto decltype concept requires`)],
  ["num", kw(`true false nullptr NULL`)],
  ["type", kw(`void int char short long float double bool unsigned signed wchar_t
    size_t ssize_t ptrdiff_t int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t
    uint64_t std string vector map set unordered_map unordered_set shared_ptr unique_ptr
    weak_ptr pair tuple array deque list queue stack optional variant function`)],
  ["num", NUM],
  ["fn", FN],
]);

const PYTHON = build([
  ["str", R`(?:[rRbBfFuU]{0,2})(?:'''[\s\S]*?(?:'''|${END})|"""[\s\S]*?(?:"""|${END}))`],
  ["com", R`#[^\n]*`],
  ["str", R`(?:[rRbBfFuU]{0,2})` + DQ_STR],
  ["str", R`(?:[rRbBfFuU]{0,2})` + SQ_STR],
  ["pre", R`@[\w.]+`],
  ["kw", kw(`def class return if elif else for while in not and or is import from as
    with try except finally raise lambda yield global nonlocal pass break continue
    assert del async await match case`)],
  ["num", kw(`True False None`)],
  ["type", kw(`self cls print len range str int float list dict set tuple type bool bytes
    isinstance issubclass super open enumerate zip map filter sorted reversed sum min max
    abs round any all repr hasattr getattr setattr Exception ValueError TypeError KeyError
    IndexError RuntimeError StopIteration`)],
  ["num", NUM],
  ["fn", FN],
]);

const JS = build([
  ["com", R`\/\*[\s\S]*?(?:\*\/|${END})`],
  ["com", R`\/\/[^\n]*`],
  ["str", "`" + R`(?:\\.|[^\\` + "`" + R`])*` + "`?"],   // 模板串（跨行）
  ["str", DQ_STR], ["str", SQ_STR],
  ["pre", R`@[\w.]+`],
  ["kw", kw(`const let var function return if else for while do switch case default
    break continue new delete typeof instanceof in of class extends super this import
    export from as async await yield try catch finally throw void static get set
    interface type enum implements declare readonly public private protected namespace
    abstract satisfies keyof infer is asserts debugger with`)],
  ["num", kw(`true false null undefined NaN Infinity`)],
  ["type", kw(`console Math JSON Object Array String Number Boolean Promise Map Set
    WeakMap WeakSet Symbol Proxy Reflect window document globalThis Error TypeError
    RangeError RegExp Date Intl number string boolean any unknown never object symbol
    bigint void React useState useEffect useRef useMemo useCallback`)],
  ["num", NUM],
  ["fn", FN],
]);

const JAVA = build([
  ["com", R`\/\*[\s\S]*?(?:\*\/|${END})`],
  ["com", R`\/\/[^\n]*`],
  ["str", DQ_STR], ["str", SQ_STR],
  ["pre", R`@\w+`],
  ["kw", kw(`abstract assert break case catch class const continue default do else enum
    extends final finally for goto if implements import instanceof interface native new
    package private protected public return static strictfp super switch synchronized
    this throw throws transient try volatile while record sealed permits var yield`)],
  ["num", kw(`true false null`)],
  ["type", kw(`void int long short byte char float double boolean String Integer Long
    Double Float Boolean Character Object List Map Set ArrayList HashMap HashSet
    Optional Stream System Math Exception RuntimeException Thread Runnable`)],
  ["num", NUM],
  ["fn", FN],
]);

const GO = build([
  ["com", R`\/\*[\s\S]*?(?:\*\/|${END})`],
  ["com", R`\/\/[^\n]*`],
  ["str", "`" + R`[^` + "`" + R`]*` + "`?"],
  ["str", DQ_STR], ["str", SQ_STR],
  ["kw", kw(`func package import type struct interface map chan go defer select range
    const var return if else for switch case default break continue fallthrough goto`)],
  ["num", kw(`true false nil iota`)],
  ["type", kw(`string int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 uintptr
    float32 float64 complex64 complex128 bool byte rune error any comparable make new
    len cap append copy delete panic recover print println`)],
  ["num", NUM],
  ["fn", FN],
]);

const RUST = build([
  ["com", R`\/\*[\s\S]*?(?:\*\/|${END})`],
  ["com", R`\/\/[^\n]*`],
  ["pre", R`#!?\[[^\]\n]*\]?`],
  ["str", DQ_STR],
  ["kw", kw(`fn let mut pub use mod struct enum impl trait for in if else while loop
    match return break continue const static ref move async await dyn where unsafe
    crate super type as extern`)],
  ["num", kw(`true false None Some Ok Err self Self`)],
  ["type", kw(`i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 bool char
    str String Vec Option Result Box Rc Arc RefCell HashMap HashSet BTreeMap println
    print vec format panic`)],
  ["num", NUM],
  ["fn", FN],
]);

const BASH = build([
  ["com", R`#[^\n]*`],
  ["str", DQ_STR], ["str", SQ_STR],
  ["var", R`\$\{[^}\n]*\}?|\$\w+|\$[#?@*!$-]`],
  ["kw", kw(`if then else elif fi for in do done while until case esac function select
    return exit break continue export local readonly declare echo cd source set unset
    shift trap eval exec sudo`)],
  ["num", NUM],
  ["fn", FN],
]);

const JSON_CFG: LangCfg = {
  ...build([
    ["str", DQ_STR],
    ["num", kw(`true false null`)],
    ["num", R`-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b`],
  ]),
  // 后处理：后面紧跟冒号的字符串是"键"，染 attr 色
  post: (toks: Tok[]) => {
    for (let i = 0; i < toks.length; i++) {
      if (toks[i].t === "str") {
        const next = toks[i + 1];
        if (next && next.t === null && /^\s*:/.test(next.s)) toks[i].t = "attr";
      }
    }
  },
};

const YAML = build([
  ["com", R`#[^\n]*`],
  ["attr", R`^[ \t]*-?[ \t]*[\w."'/-]+(?=[ \t]*:(?:[ \t]|$))`],
  ["str", DQ_STR], ["str", SQ_STR],
  ["num", kw(`true false null yes no on off`)],
  ["num", NUM],
]);

const HTML_CFG = build([
  ["com", R`<!--[\s\S]*?(?:-->|${END})`],
  ["pre", R`<!\w[^>\n]*>?`],
  ["tag", R`<\/?[a-zA-Z][\w.:-]*|\/?>`],
  ["attr", R`[a-zA-Z_][\w.:-]*(?==)`],
  ["str", DQ_STR], ["str", SQ_STR],
  ["num", NUM],
]);

const CSS_CFG = build([
  ["com", R`\/\*[\s\S]*?(?:\*\/|${END})`],
  ["str", DQ_STR], ["str", SQ_STR],
  ["pre", R`@[\w-]+`],
  ["num", R`#[0-9a-fA-F]{3,8}\b`],
  ["attr", R`[\w-]+(?=\s*:)`],
  ["num", R`-?\b\d+(?:\.\d+)?(?:px|em|rem|vh|vw|vmin|vmax|%|s|ms|deg|fr|ch|ex|pt)?\b`],
  ["fn", FN],
]);

const SQL = {
  ...build([
    ["com", R`--[^\n]*`],
    ["com", R`\/\*[\s\S]*?(?:\*\/|${END})`],
    ["str", SQ_STR], ["str", DQ_STR],
    ["kw", kw(`select from where insert into values update delete set create table drop
      alter add index view join left right inner outer full cross on group by order
      having limit offset union all distinct as and or not null is primary key foreign
      references default check unique constraint between like in exists case when then
      else end begin commit rollback transaction if`)],
    ["type", kw(`int integer bigint smallint varchar char text date datetime timestamp
      boolean bool float double decimal numeric blob serial count sum avg min max`)],
    ["num", NUM],
    ["fn", FN],
  ] as Array<[TokType, string]>, "gim"),  // SQL 关键字大小写不敏感
};

const CONFIGS: Record<string, LangCfg> = {
  cpp: CPP, c: CPP, h: CPP, hpp: CPP, cs: JAVA,
  python: PYTHON, py: PYTHON,
  javascript: JS, js: JS, jsx: JS, typescript: JS, ts: JS, tsx: JS,
  java: JAVA, kotlin: JAVA, scala: JAVA,
  go: GO, golang: GO,
  rust: RUST, rs: RUST,
  bash: BASH, sh: BASH, shell: BASH, zsh: BASH, shellscript: BASH,
  json: JSON_CFG, jsonc: JSON_CFG,
  yaml: YAML, yml: YAML, toml: YAML, ini: YAML, conf: YAML,
  html: HTML_CFG, htm: HTML_CFG, xml: HTML_CFG, vue: HTML_CFG, svg: HTML_CFG,
  css: CSS_CFG, scss: CSS_CFG, less: CSS_CFG,
  sql: SQL,
};

const EXT_TO_LANG: Record<string, string> = {
  py: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
  ts: "typescript", tsx: "typescript", cpp: "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp",
  hh: "cpp", h: "cpp", c: "c", cs: "cs", java: "java", kt: "kotlin", go: "go", rs: "rust",
  sh: "bash", bash: "bash", zsh: "bash", json: "json", yaml: "yaml", yml: "yaml",
  toml: "toml", ini: "ini", html: "html", htm: "html", xml: "xml", vue: "vue", svg: "xml",
  css: "css", scss: "scss", less: "less", sql: "sql", md: "markdown", markdown: "markdown",
  txt: "text", log: "text", csv: "text",
};

export function langFromFilename(filename: string): string {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  return EXT_TO_LANG[ext] || "text";
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function tokenize(code: string, cfg: LangCfg): Tok[] {
  const re = cfg.re;
  re.lastIndex = 0;
  const toks: Tok[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(code)) !== null) {
    if (m.index > last) toks.push({ t: null, s: code.slice(last, m.index) });
    let type: TokType | null = null;
    for (let g = 0; g < cfg.groups.length; g++) {
      if (m[g + 1] !== undefined) { type = cfg.groups[g]; break; }
    }
    toks.push({ t: type, s: m[0] });
    last = re.lastIndex;
    if (m[0].length === 0) re.lastIndex++;   // 防零宽死循环
  }
  if (last < code.length) toks.push({ t: null, s: code.slice(last) });
  if (cfg.post) cfg.post(toks);
  return toks;
}

function emit(tok: Tok): string {
  return tok.t ? `<span class="hl-${tok.t}">${esc(tok.s)}</span>` : esc(tok.s);
}

/** 整块高亮（用于 white-space: pre 的 <code> 容器） */
export function highlightCode(code: string, lang?: string): string {
  const cfg = CONFIGS[(lang || "").toLowerCase()];
  if (!cfg || !code) return esc(code || "");
  try {
    return tokenize(code, cfg).map(emit).join("");
  } catch {
    return esc(code);   // 任何意外都退回纯转义文本，绝不报错、绝不丢字
  }
}

/** 按行高亮：跨行 token（块注释/三引号串）被切割到每一行，供行号渲染用 */
export function highlightToLines(code: string, lang?: string): string[] {
  const cfg = CONFIGS[(lang || "").toLowerCase()];
  const src = code || "";
  const fallback = () => src.split("\n").map(esc);
  if (!cfg) return fallback();
  try {
    const lines: string[] = [""];
    for (const tok of tokenize(src, cfg)) {
      const parts = tok.s.split("\n");
      for (let i = 0; i < parts.length; i++) {
        if (i > 0) lines.push("");
        if (parts[i]) lines[lines.length - 1] += emit({ t: tok.t, s: parts[i] });
      }
    }
    return lines;
  } catch {
    return fallback();
  }
}
