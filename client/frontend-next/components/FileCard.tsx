"use client";
import { useState } from "react";
import { FileText, FileCode, Image, Table, File, Download, Eye, Play, Loader2 } from "lucide-react";
import { ensureFreshToken, executeConversationCode, withToken } from "@/lib/api";

interface Props {
  filename: string;
  downloadUrl: string;
  lines?: number;
  chars?: number;
  mtime?: number;
  language?: string;
  convId?: string;
  preview?: string;
}

const EXT_MAP: Record<string, { icon: React.ElementType; color: string; label: string }> = {
  py: { icon: FileCode, color: "#3572A5", label: "Python" },
  js: { icon: FileCode, color: "#f1e05a", label: "JavaScript" },
  ts: { icon: FileCode, color: "#2b7489", label: "TypeScript" },
  jsx: { icon: FileCode, color: "#f1e05a", label: "React" },
  tsx: { icon: FileCode, color: "#2b7489", label: "React TS" },
  java: { icon: FileCode, color: "#b07219", label: "Java" },
  cpp: { icon: FileCode, color: "#f34b7d", label: "C++" },
  c: { icon: FileCode, color: "#555555", label: "C" },
  go: { icon: FileCode, color: "#00ADD8", label: "Go" },
  rs: { icon: FileCode, color: "#dea584", label: "Rust" },
  html: { icon: FileCode, color: "#e34c26", label: "HTML" },
  css: { icon: FileCode, color: "#563d7c", label: "CSS" },
  json: { icon: FileText, color: "#292929", label: "JSON" },
  md: { icon: FileText, color: "#083fa1", label: "Markdown" },
  txt: { icon: FileText, color: "#71717a", label: "Text" },
  pdf: { icon: FileText, color: "#ef4444", label: "PDF" },
  docx: { icon: FileText, color: "#2563eb", label: "Word" },
  pptx: { icon: FileText, color: "#d97706", label: "PPT" },
  xlsx: { icon: Table, color: "#059669", label: "Excel" },
  csv: { icon: Table, color: "#059669", label: "CSV" },
  png: { icon: Image, color: "#8b5cf6", label: "PNG" },
  jpg: { icon: Image, color: "#8b5cf6", label: "JPEG" },
  svg: { icon: Image, color: "#FFB13B", label: "SVG" },
};

function getFileInfo(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  return EXT_MAP[ext] || { icon: File, color: "#71717a", label: ext.toUpperCase() || "FILE" };
}

export function FileCard({ filename, downloadUrl, lines, chars, language, convId, preview, mtime }: Props) {
  const info = getFileInfo(filename);
  const Icon = info.icon;
  const isPython = filename.endsWith(".py");
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState<string | null>(null);

  async function handleRun() {
    if (!convId || running) return;
    setRunning(true);
    try {
      await ensureFreshToken();
      const token = typeof localStorage !== "undefined" ? localStorage.getItem("hmm_token") : null;
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      // Read file content first
      const readRes = await fetch(`${downloadUrl}`, { headers });
      const code = await readRes.text();
      // Execute
      const data = await executeConversationCode(convId, code);
      setOutput(data.output || data.error || "(无输出)");
    } catch (e) {
      setOutput(`错误: ${e instanceof Error ? e.message : String(e)}`);
    }
    setRunning(false);
  }

  return (
    <div className="file-card my-2">
      {/* Header */}
      <div className="file-card-header">
        <Icon size={16} style={{ color: info.color }} className="flex-shrink-0" />
        <span className="file-card-name">{filename}</span>
        <span className="file-card-label" style={{ color: info.color, background: info.color + "15" }}>
          {language || info.label}
        </span>
        {lines && <span className="file-card-meta">{lines} 行</span>}
        {chars && <span className="file-card-meta">{chars.toLocaleString()} 字符</span>}
        {mtime && <span className="file-card-meta">{new Date(mtime * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>}
      </div>

      {/* Preview (if available) */}
      {preview && (
        <pre className="file-card-preview">
          <code>{preview.slice(0, 200)}</code>
        </pre>
      )}

      {/* Actions */}
      <div className="file-card-actions">
        <a href={withToken(downloadUrl)} download className="file-card-btn">
          <Download size={13} /> 下载
        </a>
        <a href={withToken(downloadUrl)} target="_blank" rel="noreferrer" className="file-card-btn">
          <Eye size={13} /> 预览
        </a>
        {isPython && convId && (
          <button onClick={handleRun} disabled={running} className="file-card-btn file-card-btn-run">
            {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
            {running ? "运行中" : "运行"}
          </button>
        )}
      </div>

      {/* Run output */}
      {output !== null && (
        <pre className="file-card-output">{output}</pre>
      )}
    </div>
  );
}
