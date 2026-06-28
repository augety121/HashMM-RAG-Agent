"use client";
import { useState, useEffect, useMemo, useRef } from "react";
import { X, Download, ChevronLeft, ChevronRight, FileText, Table2, Presentation, ExternalLink, Maximize2, Minimize2, Loader2, Copy, Check } from "lucide-react";
import { withToken, previewFile } from "@/lib/api";
import { useStore } from "@/lib/store";
import { highlightToLines, langFromFilename } from "@/lib/highlight";

export interface ArtifactItem {
  type: "pptx" | "docx" | "xlsx" | "html" | "image" | "code";
  filename: string;
  download_url: string;
  preview_url?: string;     // For HTML/image preview
  pages?: number;           // For PPTX page count
  outline?: string[];       // For PPTX slide titles
  size?: number;
}

interface Props {
  artifact: ArtifactItem | null;
  onClose: () => void;
}

const TYPE_META: Record<string, { icon: React.ElementType; label: string; color: string }> = {
  pptx: { icon: Presentation, label: "演示文稿", color: "#d97706" },
  docx: { icon: FileText, label: "Word 文档", color: "#2563eb" },
  xlsx: { icon: Table2, label: "Excel 表格", color: "#059669" },
  html: { icon: ExternalLink, label: "HTML 预览", color: "#7c3aed" },
  image: { icon: FileText, label: "图片", color: "#ec4899" },
  code: { icon: FileText, label: "代码文件", color: "#06b6d4" },
};

export function ArtifactPanel({ artifact, onClose }: Props) {
  const [currentPage, setCurrentPage] = useState(1);
  const [expanded, setExpanded] = useState(false);
  const draft = useStore(s => s.artifactDraft);
  const isCode = artifact?.type === "code";
  const [previewHtml, setPreviewHtml] = useState<string | null>(null);
  const [codeContent, setCodeContent] = useState<string | null>(null);
  const [codeLang, setCodeLang] = useState<string>("text");
  const [codeLoading, setCodeLoading] = useState(false);

  useEffect(() => {
    setCurrentPage(1);
    setPreviewHtml(null);
    setCodeContent(null);
    // For HTML artifacts, fetch content
    if (artifact?.type === "html" && artifact.preview_url) {
      fetch(artifact.preview_url)
        .then(r => r.text())
        .then(setPreviewHtml)
        .catch(() => setPreviewHtml("<p>预览加载失败</p>"));
    }
    // For code/text files, fetch the actual file content to show in panel (对标 Claude 右侧看代码)
    if (artifact?.type === "code" && artifact.filename && !artifact.download_url) {
      return;  // V55: 草稿直播模式（file_delta），内容来自 store.artifactDraft，无需取数
    }
    if (artifact?.type === "code" && artifact.filename) {
      setCodeLoading(true);
      // 从 download_url 解析 convId
      const convId = artifact.download_url.includes("/conversations/") ? artifact.download_url.split("/conversations/")[1].split("/")[0] : undefined;
      previewFile(artifact.filename, convId)
        .then(r => { setCodeContent(r.content || "（文件为空或无法预览）"); setCodeLang(r.language || "text"); })
        .catch(() => setCodeContent("（预览加载失败，可点下载查看）"))
        .finally(() => setCodeLoading(false));
    }
  }, [artifact]);

  // V86: Esc 关闭右栏（对标 Claude；输入框/文本域聚焦时不抢按键）
  useEffect(() => {
    if (!artifact) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [artifact, onClose]);

  if (!artifact) return null;

  const meta = TYPE_META[artifact.type] || TYPE_META.code;
  const Icon = meta.icon;
  const totalPages = artifact.pages || 1;

  return (
    <div className={`flex flex-col h-full w-full min-w-0 ${isCode ? "ap-code-root" : ""} ${expanded ? "fixed inset-0 z-50" : ""}`}
      style={{ background: isCode ? undefined : "var(--bg-primary)",
               borderLeft: expanded ? "none" : "1px solid var(--border)" }}>

      {/* Header（代码型走暗色一体化主题，与内容统一——此前白头配暗身割裂）
          V86: titlebar-safe 给 Windows 原生窗口控件条让位，关闭按钮此前被它盖住 */}
      <div className={`flex items-center gap-2 px-4 py-3 flex-shrink-0 titlebar-safe ${isCode ? "ap-code-head" : ""}`}
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: `${meta.color}15` }}>
          <Icon size={14} style={{ color: meta.color }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>
            {artifact.filename}
          </div>
          <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            {meta.label}{totalPages > 1 ? ` · ${totalPages} 页` : ""}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <a href={withToken(artifact.download_url)} download
            className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-secondary)]"
            title="下载">
            <Download size={14} style={{ color: "var(--text-tertiary)" }} />
          </a>
          <button onClick={() => setExpanded(!expanded)}
            className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-secondary)]"
            title={expanded ? "缩小" : "全屏"}>
            {expanded ? <Minimize2 size={14} style={{ color: "var(--text-tertiary)" }} /> : <Maximize2 size={14} style={{ color: "var(--text-tertiary)" }} />}
          </button>
          <button onClick={onClose}
            className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-secondary)]"
            title="关闭">
            <X size={14} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>
      </div>

      {/* Preview area — code 类型铺满整个面板（无白边），其他类型保留内边距 */}
      <div className={`flex-1 overflow-auto ${artifact.type === "code" ? "p-0" : "p-4"}`}>
        {artifact.type === "pptx" && (
          <PptxPreview
            filename={artifact.filename}
            downloadUrl={artifact.download_url}
            outline={artifact.outline}
            totalPages={totalPages}
            currentPage={currentPage}
          />
        )}

        {artifact.type === "docx" && (
          <DocxPreview downloadUrl={artifact.download_url} />
        )}

        {artifact.type === "xlsx" && (
          <XlsxPreview downloadUrl={artifact.download_url} />
        )}

        {artifact.type === "code" && !artifact.download_url && (
          <CodePane content={draft && draft.filename === artifact.filename ? draft.content : ""}
            lang={langFromFilename(artifact.filename)} filename={artifact.filename} follow />
        )}
        {artifact.type === "code" && !!artifact.download_url && (
          codeLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 size={20} className="animate-spin" style={{ color: "var(--text-tertiary)" }} />
              <span className="ml-2 text-[13px]" style={{ color: "var(--text-secondary)" }}>加载代码…</span>
            </div>
          ) : (
            <CodePane content={codeContent || ""} lang={codeLang} filename={artifact.filename} />
          )
        )}

        {artifact.type === "html" && previewHtml && (
          <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
            <iframe
              srcDoc={previewHtml}
              sandbox="allow-scripts"
              className="w-full"
              style={{ height: expanded ? "calc(100vh - 120px)" : 400, border: "none" }}
            />
          </div>
        )}

        {!["pptx", "docx", "xlsx", "html", "code"].includes(artifact.type) && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <Icon size={48} style={{ color: "var(--text-tertiary)", opacity: 0.3 }} />
            <p className="mt-4 text-[13px]" style={{ color: "var(--text-secondary)" }}>
              暂不支持此格式的在线预览
            </p>
            <a href={withToken(artifact.download_url)} download
              className="mt-3 inline-flex items-center gap-2 px-4 py-2 rounded-lg text-[12px] font-medium"
              style={{ background: "var(--accent)", color: "#fff" }}>
              <Download size={14} /> 下载文件
            </a>
          </div>
        )}
      </div>

      {/* Footer — page navigation for PPTX */}
      {artifact.type === "pptx" && totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 px-4 py-2.5 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          <button onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
            disabled={currentPage <= 1}
            className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-secondary)] disabled:opacity-30">
            <ChevronLeft size={16} style={{ color: "var(--text-secondary)" }} />
          </button>
          <span className="text-[12px] font-mono" style={{ color: "var(--text-secondary)" }}>
            {currentPage} / {totalPages}
          </span>
          <button onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
            disabled={currentPage >= totalPages}
            className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-secondary)] disabled:opacity-30">
            <ChevronRight size={16} style={{ color: "var(--text-secondary)" }} />
          </button>
        </div>
      )}

      {/* Outline sidebar for PPTX */}
      {artifact.type === "pptx" && artifact.outline && artifact.outline.length > 0 && (
        <div className="px-4 pb-3 flex-shrink-0">
          <div className="text-[10px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: "var(--text-tertiary)" }}>
            大纲
          </div>
          <div className="space-y-0.5 max-h-[200px] overflow-y-auto">
            {artifact.outline.map((title, i) => (
              <button key={i}
                onClick={() => setCurrentPage(i + 1)}
                className="w-full text-left px-2 py-1 rounded-md text-[11px] truncate transition-colors"
                style={{
                  background: currentPage === i + 1 ? "var(--accent-light)" : "transparent",
                  color: currentPage === i + 1 ? "var(--accent)" : "var(--text-secondary)",
                }}>
                {i + 1}. {title}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// ── Sub-previews ──

/** V49: 右侧面板代码视图 — 行号 + 零依赖语法高亮 + 自动换行 + 复制，铺满面板（对标 Claude artifact） */
function CodePane({ content, lang, filename, follow }: { content: string; lang: string; filename: string; follow?: boolean }) {
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  // V55: 直播草稿模式跟随滚动（像看人现场写代码）
  useEffect(() => {
    if (follow && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [content, follow]);
  const effLang = (!lang || lang === "text") ? langFromFilename(filename) : lang;
  const hlLines = useMemo(() => highlightToLines(content, effLang), [content, effLang]);

  const copyAll = async () => {
    try { await navigator.clipboard.writeText(content); }
    catch {
      const ta = document.createElement("textarea");
      ta.value = content; ta.style.position = "fixed"; ta.style.left = "-9999px";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch { /* no-op */ }
      document.body.removeChild(ta);
    }
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="cp-root">
      <div className="cp-toolbar">
        <span className="cp-lang">{effLang}</span>
        <span className="cp-meta">{hlLines.length} 行 · {content.length.toLocaleString()} 字符</span>
        <button className="cp-toolbar-btn" onClick={copyAll}>
          {copied ? <><Check size={12} className="text-green-500" /> 已复制</> : <><Copy size={12} /> 复制</>}
        </button>
      </div>
      <div className="cp-scroll" ref={scrollRef}>
        {hlLines.map((html, i) => (
          <div key={i} className="cp-line">
            <span className="cp-ln">{i + 1}</span>
            {/* 高亮器输出已全量转义（scripts/check_highlight.mjs 锁住该不变量），安全 */}
            <span className="cp-code" dangerouslySetInnerHTML={{ __html: html || "\u200b" }} />
          </div>
        ))}
      </div>
    </div>
  );
}

function PptxPreview({ filename, downloadUrl, outline, totalPages, currentPage }: {
  filename: string; downloadUrl: string; outline?: string[]; totalPages: number; currentPage: number;
}) {
  const [slides, setSlides] = useState<string[] | null>(null);
  useEffect(() => {
    const fn = decodeURIComponent(downloadUrl.split("/").pop() || filename || "");
    const convId = downloadUrl.includes("/conversations/") ? downloadUrl.split("/conversations/")[1].split("/")[0] : undefined;
    if (!fn) return;
    previewFile(fn, convId)
      .then((r: any) => { if (r?.rich?.kind === "slides") setSlides(r.rich.slides || []); })
      .catch(() => {});
  }, [downloadUrl]);
  const body = slides ? (slides[currentPage - 1] || "（本页无文本）") : (outline?.[currentPage - 1] || "");
  return (
    <div className="flex flex-col items-center">
      <div className="w-full rounded-xl mb-4 p-6 text-left"
        style={{ background: "#fff", color: "#1a1a1a", border: "1px solid var(--border)", minHeight: 200, maxHeight: 500, overflowY: "auto", whiteSpace: "pre-wrap", fontSize: 13.5, lineHeight: 1.7 }}>
        {slides ? body : (
          <div className="text-center" style={{ color: "var(--text-tertiary)" }}>
            <Presentation size={40} style={{ color: "var(--accent)", opacity: 0.4 }} className="mx-auto mb-2" />
            <div className="text-[12px]">加载第 {currentPage} 页…</div>
          </div>
        )}
      </div>
      <div className="text-[11px] mb-3" style={{ color: "var(--text-tertiary)" }}>{filename} · 第 {currentPage} / {totalPages} 页</div>
      <a href={withToken(downloadUrl)} download
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-[13px] font-medium transition-all hover:shadow-md"
        style={{ background: "var(--accent)", color: "#fff" }}>
        <Download size={15} /> 下载 PPTX
      </a>
    </div>
  );
}

function DocxPreview({ downloadUrl }: { downloadUrl: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // 关键修复：会话文件预览必须走会话端点（previewFile→/api/conversations/{conv}/files/{name}/preview），
    // 之前错用了全局 /api/files/{name}/preview，会话文件查不到 → 一直「预览不可用」。
    const filename = decodeURIComponent(downloadUrl.split("/").pop() || "");
    const convId = downloadUrl.includes("/conversations/") ? downloadUrl.split("/conversations/")[1].split("/")[0] : undefined;
    if (!filename) { setLoading(false); return; }
    previewFile(filename, convId)
      .then((r: any) => { setHtml(r?.rich?.kind === "html" ? (r.rich.html || "") : null); setLoading(false); })
      .catch(() => setLoading(false));
  }, [downloadUrl]);

  if (loading) return (
    <div className="space-y-3 py-8">
      <div className="skeleton-shimmer h-4 w-[80%] mx-auto" />
      <div className="skeleton-shimmer h-4 w-[65%] mx-auto" />
      <div className="skeleton-shimmer h-4 w-[70%] mx-auto" />
    </div>
  );

  if (html) return (
    <div>
      <div className="rounded-lg overflow-hidden mb-4" style={{ border: "1px solid var(--border)", maxHeight: 500, overflowY: "auto" }}>
        <div dangerouslySetInnerHTML={{ __html: html }} />
      </div>
      <div className="text-center">
        <a href={withToken(downloadUrl)} download
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-[13px] font-medium"
          style={{ background: "#2563eb", color: "#fff" }}>
          <Download size={15} /> 下载 DOCX
        </a>
      </div>
    </div>
  );

  return (
    <div className="flex flex-col items-center py-8">
      <FileText size={48} style={{ color: "#2563eb", opacity: 0.4 }} className="mb-3" />
      <p className="text-[13px] mb-4" style={{ color: "var(--text-secondary)" }}>
        DOCX 预览不可用
      </p>
      <a href={withToken(downloadUrl)} download
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-[13px] font-medium"
        style={{ background: "#2563eb", color: "#fff" }}>
        <Download size={15} /> 下载 DOCX
      </a>
    </div>
  );
}

function XlsxPreview({ downloadUrl }: { downloadUrl: string }) {
  const [data, setData] = useState<{ sheets: Array<{ name: string; headers: string[]; rows: string[][]; total_rows: number }> } | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeSheet, setActiveSheet] = useState(0);

  useEffect(() => {
    const filename = decodeURIComponent(downloadUrl.split("/").pop() || "");
    const convId = downloadUrl.includes("/conversations/") ? downloadUrl.split("/conversations/")[1].split("/")[0] : undefined;
    if (!filename) { setLoading(false); return; }
    previewFile(filename, convId)
      .then((r: any) => { if (r?.rich?.kind === "sheets") setData({ sheets: r.rich.sheets || [] }); setLoading(false); })
      .catch(() => setLoading(false));
  }, [downloadUrl]);

  if (loading) return (
    <div className="space-y-2 py-8">
      <div className="skeleton-shimmer h-8 w-full" />
      <div className="skeleton-shimmer h-6 w-full" />
      <div className="skeleton-shimmer h-6 w-full" />
      <div className="skeleton-shimmer h-6 w-[80%]" />
    </div>
  );

  if (data?.sheets && data.sheets.length > 0) {
    const sheet = data.sheets[activeSheet] || data.sheets[0];
    return (
      <div>
        {/* Sheet tabs */}
        {data.sheets.length > 1 && (
          <div className="flex gap-1 mb-3 overflow-x-auto">
            {data.sheets.map((s, i) => (
              <button key={i} onClick={() => setActiveSheet(i)}
                className="px-3 py-1 rounded-lg text-[11px] font-medium transition-colors flex-shrink-0"
                style={{
                  background: i === activeSheet ? "var(--accent-light)" : "var(--bg-tertiary)",
                  color: i === activeSheet ? "var(--accent)" : "var(--text-tertiary)",
                }}>
                {s.name}
              </button>
            ))}
          </div>
        )}
        {/* Table */}
        <div className="rounded-lg overflow-auto" style={{ border: "1px solid var(--border)", maxHeight: 400 }}>
          <table className="w-full text-[12px]" style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {sheet.headers.map((h, i) => (
                  <th key={i} className="text-left px-3 py-2 font-semibold whitespace-nowrap sticky top-0"
                    style={{ background: "var(--bg-secondary)", borderBottom: "2px solid var(--border)", color: "var(--text-secondary)" }}>
                    {h || `列${i + 1}`}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.rows.slice(0, 50).map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-3 py-1.5" style={{ borderBottom: "1px solid var(--border-light)", color: "var(--text-primary)" }}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between mt-2">
          <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            {sheet.total_rows} 行 × {sheet.headers.length} 列
            {sheet.rows.length < sheet.total_rows ? ` (显示前 ${sheet.rows.length} 行)` : ""}
          </span>
          <a href={withToken(downloadUrl)} download
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium"
            style={{ background: "#059669", color: "#fff" }}>
            <Download size={13} /> 下载 XLSX
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center py-8">
      <Table2 size={48} style={{ color: "#059669", opacity: 0.4 }} className="mb-3" />
      <p className="text-[13px] mb-4" style={{ color: "var(--text-secondary)" }}>
        无法预览此表格
      </p>
      <a href={withToken(downloadUrl)} download
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-[13px] font-medium"
        style={{ background: "#059669", color: "#fff" }}>
        <Download size={15} /> 下载 XLSX
      </a>
    </div>
  );
}
