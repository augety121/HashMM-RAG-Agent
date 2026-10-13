"use client";
import { useState } from "react";
import { Search, FileText, Play, Pencil, BookOpen, FolderSearch, FolderTree,
  FilePlus2, Presentation, Sheet, Terminal, FolderOpen, Globe2, History,
  RotateCcw, Settings2, CheckCircle2, XCircle, type LucideIcon } from "lucide-react";

interface Props {
  tool: string;
  detail: string;
  status: "running" | "done" | "error";
  durationMs?: number;
}

const ICONS: Record<string, LucideIcon> = {
  kb_search: Search, create_file: FileText, execute_code: Play,
  str_replace: Pencil, read_file: BookOpen, read_file_range: BookOpen,
  search_files: FolderSearch, file_tree: FolderTree, insert_lines: FilePlus2,
  create_pptx_from_plan: Presentation, create_document: FileText,
  create_xlsx: Sheet, create_pdf: FileText, run_shell: Terminal,
  list_files: FolderOpen, web_search: Globe2, pptx_summary: Presentation,
  pptx_edit_slide: Pencil, file_versions: History, file_restore: RotateCcw,
};

const LABELS: Record<string, string> = {
  kb_search: "搜索知识库", create_file: "创建文件", execute_code: "执行代码",
  str_replace: "编辑文件", read_file: "读取文件", read_file_range: "读取文件",
  search_files: "搜索文件", file_tree: "文件目录", insert_lines: "插入代码",
  create_pptx_from_plan: "创建 PPT", create_document: "创建文档",
  create_xlsx: "创建表格", create_pdf: "创建 PDF", run_shell: "执行命令",
  list_files: "列出文件", web_search: "搜索网络", pptx_summary: "PPT 摘要",
  pptx_edit_slide: "编辑幻灯片", file_versions: "版本历史", file_restore: "恢复文件",
};

function formatTime(ms: number): string {
  if (ms < 1000) return ms + "ms";
  return (ms / 1000).toFixed(1) + "s";
}

/** Parse create_file detail to extract filename, size, lines */
function parseFileDetail(detail: string): { filename?: string; chars?: string; lines?: string } | null {
  const m = detail.match(/文件\s+(\S+)\s+已创建.*?(\d+)\s*字符.*?(\d+)\s*行/);
  if (m) return { filename: m[1], chars: m[2], lines: m[3] };
  const m2 = detail.match(/文件\s+(\S+)/);
  if (m2) return { filename: m2[1] };
  return null;
}

/** Parse str_replace detail to extract filename */
function parseDiffDetail(detail: string): { filename?: string; added?: number; removed?: number } | null {
  const m = detail.match(/OK.*?(\S+\.\w+)/);
  if (!m) return null;
  return { filename: m[1] };
}

export function ToolStepCard({ tool, detail, status, durationMs }: Props) {
  const [open, setOpen] = useState(status === "error");
  const Icon = ICONS[tool] || Settings2;
  const label = LABELS[tool] || tool;

  // Parse detail for rich display
  let richContent: React.ReactNode = null;

  if (tool === "create_file" && detail) {
    const parsed = parseFileDetail(detail);
    if (parsed?.filename) {
      richContent = (
        <div className="tool-rich-detail">
          <span className="tool-filename">{parsed.filename}</span>
          {parsed.lines && <span className="tool-meta">{parsed.lines} 行</span>}
          {parsed.chars && <span className="tool-meta">{(parseInt(parsed.chars) / 1024).toFixed(1)}KB</span>}
        </div>
      );
    }
  }

  if (tool === "str_replace" && detail) {
    const parsed = parseDiffDetail(detail);
    if (parsed?.filename) {
      richContent = (
        <div className="tool-rich-detail">
          <span className="tool-filename">{parsed.filename}</span>
          {parsed.added != null && <span className="tool-diff-add">+{parsed.added}</span>}
          {parsed.removed != null && <span className="tool-diff-del">-{parsed.removed}</span>}
        </div>
      );
    }
  }

  if (tool === "kb_search" && detail) {
    const countMatch = detail.match(/找到\s*(\d+)\s*条/);
    richContent = (
      <div className="tool-rich-detail">
        {countMatch && <span className="tool-meta">找到 {countMatch[1]} 条结果</span>}
      </div>
    );
  }

  if (tool === "execute_code" && detail) {
    const success = detail.includes("\u2705") || detail.includes("执行成功");
    const failed = detail.includes("\u274c") || detail.includes("执行失败");
    richContent = (
      <div className="tool-rich-detail">
        <span className={`tool-exec-status ${success ? 'success' : failed ? 'error' : ''}`}>
          {success ? "执行成功" : failed ? "执行失败" : "已执行"}
        </span>
      </div>
    );
  }

  return (
    <div className={`tool-step-card ${status === "error" ? "tool-step-error" : ""}`}>
      <div className="tool-step-header" onClick={() => detail && setOpen(!open)}>
        <span className="tool-step-icon"><Icon size={14} /></span>
        <span className="tool-step-label">{label}</span>
        {richContent}
        <span className="flex-1" />
        {status === "running" && <span className="spinner" />}
        {status === "done" && <span className="tool-step-ok"><CheckCircle2 size={13} /></span>}
        {status === "error" && <span className="tool-step-err"><XCircle size={13} /></span>}
        {durationMs != null && durationMs > 0 && (
          <span className="tool-step-time">{formatTime(durationMs)}</span>
        )}
      </div>
      {open && detail && (
        <div className="tool-step-detail">{detail.slice(0, 300)}</div>
      )}
    </div>
  );
}
