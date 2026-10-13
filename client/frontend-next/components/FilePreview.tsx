"use client";
import { useState, useEffect, useRef } from "react";
import { previewFile } from "@/lib/api";
import { X, Download, Copy, Check, FileCode, FileText, File as FileIcon } from "lucide-react";

interface Props {
  filename: string;
  downloadUrl: string;
  convId?: string;
  onClose: () => void;
}

export function FilePreview({ filename, downloadUrl, convId, onClose }: Props) {
  const [data, setData] = useState<{
    content: string | null; language?: string; lines?: number;
    size: number; binary?: boolean; ext: string;
    rich?: {
      kind: string; html?: string;
      sheets?: Array<{ name: string; headers: string[]; rows: (string | number | null)[][]; total_rows?: number }>;
      slides?: string[]; pages?: number;
    } | null;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const codeRef = useRef<HTMLElement>(null);

  // v27: Extract convId from downloadUrl if not provided
  const resolvedConvId = convId || downloadUrl?.match(/conversations\/([^/]+)/)?.[1];

  useEffect(() => {
    setLoading(true);
    previewFile(filename, resolvedConvId)
      .then(d => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [filename, resolvedConvId]);

  // Highlight with Prism.js after content loads
  useEffect(() => {
    if (data?.content && codeRef.current && typeof (window as any).Prism !== "undefined") {
      try { (window as any).Prism.highlightElement(codeRef.current); } catch (_e) {}
    }
  }, [data]);

  function copyContent() {
    if (!data?.content) return;
    const ta = document.createElement("textarea");
    ta.value = data.content;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed"; ta.style.left = "-9999px";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch (_e) {}
    document.body.removeChild(ta);
  }

  const ext = filename.split(".").pop()?.toLowerCase() || "";
  const isCode = ["py","js","ts","java","cpp","c","go","rs","rb","php","sh","html","css","yaml","json","sql","kt","swift","m","r"].includes(ext);
  const isMd = ["md","markdown"].includes(ext);
  const rich = data?.rich;
  const token = typeof window !== "undefined" ? (localStorage.getItem("hmm_token") || "") : "";
  const pdfSrc = downloadUrl + (downloadUrl.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.5)" }} onClick={onClose}>
      <div className={`w-full ${rich ? "max-w-4xl" : "max-w-2xl"} max-h-[85vh] flex flex-col rounded-2xl overflow-hidden anim-fade-up mx-4`}
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
        onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          {isCode ? <FileCode size={16} style={{ color: "#059669" }} /> :
           isMd ? <FileText size={16} style={{ color: "#2563eb" }} /> :
           <FileIcon size={16} style={{ color: "var(--text-tertiary)" }} />}
          <span className="font-medium text-sm flex-1 truncate" style={{ color: "var(--text-primary)" }}>{filename}</span>
          {data && <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
            {data.lines ? `${data.lines} 行` : ""} · {data.size > 1024 ? `${(data.size/1024).toFixed(1)}K` : `${data.size}B`}
          </span>}
          <button onClick={copyContent} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" title="复制">
            {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} style={{ color: "var(--text-tertiary)" }} />}
          </button>
          <a href={downloadUrl} download className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" title="下载">
            <Download size={14} style={{ color: "var(--text-tertiary)" }} />
          </a>
          <button onClick={onClose} className="p-1.5 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]">
            <X size={14} style={{ color: "var(--text-tertiary)" }} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="flex items-center justify-center py-12">
              <div className="w-6 h-6 border-2 rounded-full animate-spin" style={{ borderColor: "var(--border)", borderTopColor: "var(--accent)" }} />
            </div>
          )}
          {!loading && rich?.kind === "pdf" && (
            <iframe src={pdfSrc} title={filename} className="w-full" style={{ height: "74vh", border: "none", background: "#fff" }} />
          )}
          {!loading && rich?.kind === "html" && (
            <div className="filepreview-doc p-6 bg-white text-black text-[13.5px] leading-relaxed"
                 style={{ overflowX: "auto" }}
                 dangerouslySetInnerHTML={{ __html: rich.html || "" }} />
          )}
          {!loading && rich?.kind === "sheets" && (
            <div className="p-3 flex flex-col gap-5">
              {(rich.sheets || []).map((sh, si) => (
                <div key={si}>
                  <div className="text-[12px] font-semibold mb-1.5" style={{ color: "var(--text-secondary)" }}>
                    {sh.name}{sh.total_rows != null ? ` · ${sh.total_rows} 行` : ""}
                  </div>
                  <div className="overflow-x-auto rounded-lg" style={{ border: "1px solid var(--border)" }}>
                    <table className="text-[12px] w-full" style={{ borderCollapse: "collapse" }}>
                      {sh.headers && sh.headers.length > 0 && (
                        <thead>
                          <tr>{sh.headers.map((h, hi) => (
                            <th key={hi} className="px-2.5 py-1.5 text-left font-semibold whitespace-nowrap"
                                style={{ background: "var(--bg-secondary)", borderBottom: "1px solid var(--border)", color: "var(--text-primary)" }}>{String(h ?? "")}</th>
                          ))}</tr>
                        </thead>
                      )}
                      <tbody>
                        {(sh.rows || []).map((r, ri) => (
                          <tr key={ri}>{r.map((c, ci) => (
                            <td key={ci} className="px-2.5 py-1.5 whitespace-nowrap" style={{ borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" }}>{c == null ? "" : String(c)}</td>
                          ))}</tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          )}
          {!loading && rich?.kind === "slides" && (
            <div className="p-4 flex flex-col gap-3">
              {(rich.slides || []).map((s, i) => (
                <div key={i} className="rounded-xl p-4" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                  <div className="text-[10px] font-mono mb-2" style={{ color: "var(--text-tertiary)" }}>第 {i + 1} 页 / 共 {rich.pages ?? (rich.slides ? rich.slides.length : 0)} 页</div>
                  <div className="text-[13px] whitespace-pre-wrap" style={{ color: "var(--text-primary)" }}>{s || "（空白页）"}</div>
                </div>
              ))}
            </div>
          )}
          {!loading && data?.binary && !rich && (
            <div className="flex flex-col items-center justify-center py-12 gap-3">
              <FileIcon size={32} style={{ color: "var(--text-tertiary)" }} />
              <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>二进制文件，无法预览</p>
              <a href={downloadUrl} download className="px-4 py-2 rounded-lg text-sm font-medium text-white" style={{ background: "var(--accent)" }}>
                下载文件
              </a>
            </div>
          )}
          {!loading && data?.content && (
            <div className="relative">
              {isCode ? (
                <pre className="p-4 text-[12px] leading-relaxed overflow-x-auto" style={{ background: "var(--bg-secondary)", margin: 0 }}>
                  <code ref={codeRef} className={`language-${data.language || ext}`}>
                    {data.content}
                  </code>
                </pre>
              ) : (
                <pre className="p-4 text-[13px] leading-relaxed whitespace-pre-wrap" style={{ color: "var(--text-primary)" }}>
                  {data.content}
                </pre>
              )}
            </div>
          )}
          {!loading && !data && (
            <div className="flex items-center justify-center py-12">
              <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>加载失败</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
