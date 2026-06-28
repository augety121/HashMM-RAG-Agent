"use client";
import { useState } from "react";
import { Copy, FileDown, Play, ChevronDown, ChevronUp, Check, Loader2, Terminal } from "lucide-react";
import { highlightToLines } from "@/lib/highlight";

interface Props {
  code: string;
  language: string;
  filename?: string;
  lineCount: number;
  convId?: string;
}

const LANG_LABELS: Record<string, string> = {
  python: "Python", javascript: "JavaScript", typescript: "TypeScript",
  java: "Java", cpp: "C++", c: "C", go: "Go", rust: "Rust",
  html: "HTML", css: "CSS", json: "JSON", yaml: "YAML",
  bash: "Bash", shell: "Shell", sql: "SQL", markdown: "Markdown",
  text: "Text",
};

export function CodeBlock({ code, language, filename, lineCount, convId }: Props) {
  const [expanded, setExpanded] = useState(lineCount <= 40);
  const [copied, setCopied] = useState(false);
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [outputOpen, setOutputOpen] = useState(true);

  const isPython = language === "python" || (filename?.endsWith(".py") ?? false);
  const lines = code.split("\n");
  // V49: 自有零依赖高亮 —— 整块 tokenize 后按行切，折叠时跨行 token（块注释等）颜色仍正确。
  // 此前这里依赖 window.Prism，但 Prism 从未打进 bundle，高亮从未生效过。
  const hlLines = highlightToLines(code, language || (filename ? filename.split(".").pop() : ""));
  const displayLines = expanded ? lines : lines.slice(0, 30);
  const displayHtml = (expanded ? hlLines : hlLines.slice(0, 30)).join("\n");
  const langLabel = LANG_LABELS[language] || language.toUpperCase() || "CODE";

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(code);
    } catch (_e) {
      const ta = document.createElement("textarea");
      ta.value = code;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const runCode = async () => {
    if (!convId || running) return;
    setRunning(true);
    setOutput(null);
    setOutputOpen(true);
    try {
      const token = typeof localStorage !== "undefined" ? localStorage.getItem("hmm_token") : null;
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(`/api/conversations/${convId}/execute`, {
        method: "POST",
        headers,
        body: JSON.stringify({ code }),
      });
      const data = await res.json();
      setOutput(data.output || data.error || "(无输出)");
    } catch (e) {
      setOutput(`错误: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRunning(false);
    }
  };

  const createFile = async () => {
    if (!convId || !filename) return;
    try {
      const token = typeof localStorage !== "undefined" ? localStorage.getItem("hmm_token") : null;
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      await fetch(`/api/conversations/${convId}/files/create`, {
        method: "POST",
        headers,
        body: JSON.stringify({ filename, content: code }),
      });
    } catch (_e) { /* no-op */ }
  };

  return (
    <div className="enhanced-code-block my-3">
      {/* Header */}
      <div className="ecb-header">
        <div className="ecb-header-left">
          <span className="ecb-lang">{langLabel}</span>
          {filename && <span className="ecb-filename">{filename}</span>}
          {lineCount > 3 && <span className="ecb-linecount">{lineCount} 行</span>}
        </div>
        <div className="ecb-header-right">
          {isPython && convId && (
            <button onClick={runCode} disabled={running} className="ecb-btn ecb-btn-run" title="运行">
              {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
              <span>{running ? "运行中" : "运行"}</span>
            </button>
          )}
          {filename && (
            <button onClick={createFile} className="ecb-btn" title="保存文件">
              <FileDown size={12} />
            </button>
          )}
          <button onClick={copyCode} className="ecb-btn" title="复制">
            {copied ? <Check size={12} className="text-green-500" /> : <Copy size={12} />}
            <span>{copied ? "已复制" : "复制"}</span>
          </button>
        </div>
      </div>

      {/* Code body with line numbers */}
      <div className="ecb-body">
        <div className="ecb-line-numbers" aria-hidden="true">
          {displayLines.map((_, i) => (
            <span key={i}>{i + 1}</span>
          ))}
        </div>
        <pre className="ecb-code">
          <code className={`language-${language}`}
            // 高亮器输出的 HTML 已全量转义（见 scripts/check_highlight.mjs 不变量校验），安全
            dangerouslySetInnerHTML={{ __html: displayHtml }} />
        </pre>
      </div>

      {/* Expand/collapse for long code */}
      {lineCount > 40 && (
        <button className="ecb-fold" onClick={() => setExpanded(!expanded)}>
          {expanded ? (
            <><ChevronUp size={13} /> 折叠代码</>
          ) : (
            <><ChevronDown size={13} /> 展开全部 {lineCount} 行</>
          )}
        </button>
      )}

      {/* Execution output */}
      {output !== null && (
        <div className="ecb-output">
          <button className="ecb-output-header" onClick={() => setOutputOpen(!outputOpen)}>
            <Terminal size={12} />
            <span>运行结果</span>
            <ChevronDown size={12} className={`ecb-output-chevron ${outputOpen ? "rotate-180" : ""}`} />
          </button>
          {outputOpen && (
            <pre className="ecb-output-content">{output}</pre>
          )}
        </div>
      )}
    </div>
  );
}
