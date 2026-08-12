"use client";
import { FileTreeView } from "./FileTreeView";
import { useState, useEffect } from "react";
import { listConvFiles, onConvFileListUpdated, withToken } from "@/lib/api";
import { Download, FileText, FileSpreadsheet, Image, FileCode, File, Eye, X, FolderOpen } from "lucide-react";

interface ConvFile {
  filename: string;
  download_url: string;
  size?: number;
  size_str?: string;
}

const ICONS: Record<string, typeof FileText> = {
  pptx: FileSpreadsheet, xlsx: FileSpreadsheet,
  docx: FileText, md: FileText, txt: FileText,
  py: FileCode, js: FileCode, ts: FileCode, java: FileCode, cpp: FileCode,
  png: Image, jpg: Image, jpeg: Image, gif: Image,
};

export function ConvFilePanel({ convId, onPreview }: { convId: string | null; onPreview?: (f: ConvFile) => void }) {
  const [files, setFiles] = useState<ConvFile[]>([]);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (!convId) { setFiles([]); return; }
    listConvFiles(convId).then(d => setFiles(d.files || [])).catch(() => setFiles([]));
    return onConvFileListUpdated(convId, d => setFiles(d.files || []));
  }, [convId]);

  // Also listen for file events via custom event
  useEffect(() => {
    const handler = () => {
      if (convId) listConvFiles(convId).then(d => setFiles(d.files || [])).catch(() => {});
    };
    window.addEventListener("conv-file-created", handler);
    return () => window.removeEventListener("conv-file-created", handler);
  }, [convId]);

  if (!files.length) return null;

  const getIcon = (fname: string) => {
    const ext = fname.split(".").pop()?.toLowerCase() || "";
    const Icon = ICONS[ext] || File;
    return <Icon size={14} />;
  };

  return (
    <div className="mt-2 rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
      <button onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-[12px] font-medium"
        style={{ color: "var(--text-secondary)" }}>
        <FolderOpen size={13} />
        {files.length} 个文件
        <span className="ml-auto text-[10px]" style={{ color: "var(--text-tertiary)" }}>{open ? "收起" : "展开"}</span>
      </button>
      {open && (
        <div className="px-2 pb-2 space-y-1">
          {files.map((f, i) => {
            const ext = f.filename.split(".").pop()?.toLowerCase() || "";
            const previewable = ["md", "txt", "py", "js", "ts", "json", "csv"].includes(ext);
            return (
              <div key={i} className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]"
                style={{ color: "var(--text-primary)" }}>
                <span style={{ color: "var(--accent)" }}>{getIcon(f.filename)}</span>
                <span className="flex-1 truncate">{f.filename}</span>
                {f.size_str && <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{f.size_str}</span>}
                {previewable && onPreview && (
                  <button onClick={() => onPreview(f)} className="p-1 rounded hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
                    <Eye size={12} />
                  </button>
                )}
                <a href={withToken(f.download_url)} download className="p-1 rounded hover:bg-[var(--bg-tertiary)]" style={{ color: "var(--text-tertiary)" }}>
                  <Download size={12} />
                </a>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
