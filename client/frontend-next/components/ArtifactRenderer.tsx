"use client";
import { useEffect, useRef, useState, useCallback } from "react";
import { Maximize2, Minimize2, Download, Copy, Check, ExternalLink } from "lucide-react";

interface Props {
  code: string;
  type: "html" | "svg" | "react";
  title?: string;
}

export function ArtifactRenderer({ code, type, title }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [iframeHeight, setIframeHeight] = useState(300);

  const renderHTML = useCallback(() => {
    if (type !== "html" || !iframeRef.current) return;
    const doc = iframeRef.current.contentDocument;
    if (!doc) return;
    doc.open();
    doc.write(`<!DOCTYPE html><html><head><meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css">
      <style>
        body { font-family: -apple-system, 'Noto Sans SC', sans-serif; margin: 0; padding: 16px; color: #1e293b; }
        * { box-sizing: border-box; }
      </style>
    </head><body>${code}</body>
    <script>
      // Auto-resize
      new ResizeObserver(() => {
        window.parent.postMessage({ type: 'artifact-height', height: document.body.scrollHeight + 32 }, '*');
      }).observe(document.body);
    </script></html>`);
    doc.close();
  }, [code, type]);

  useEffect(() => { renderHTML(); }, [renderHTML]);

  // Listen for iframe height messages
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (e.data?.type === "artifact-height" && typeof e.data.height === "number") {
        setIframeHeight(Math.min(Math.max(e.data.height, 100), 600));
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const download = () => {
    const ext = type === "svg" ? "svg" : "html";
    const blob = new Blob([code], { type: type === "svg" ? "image/svg+xml" : "text/html" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${title || "artifact"}.${ext}`; a.click();
    URL.revokeObjectURL(url);
  };

  const copyCode = () => {
    navigator.clipboard?.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const openInNewTab = () => {
    const blob = new Blob([code], { type: "text/html" });
    window.open(URL.createObjectURL(blob), "_blank");
  };

  const typeLabel = type === "svg" ? "SVG 图形" : type === "react" ? "React 组件" : "HTML 预览";

  return (
    <div className={`artifact-wrap my-3 ${expanded ? "artifact-expanded" : ""}`}>
      <div className="artifact-header">
        <span className="artifact-label">{typeLabel}{title ? ` — ${title}` : ""}</span>
        <div className="flex gap-1">
          <button onClick={copyCode} className="artifact-btn" title="复制代码">
            {copied ? <Check size={13} className="text-green-500" /> : <Copy size={13} />}
          </button>
          <button onClick={download} className="artifact-btn" title="下载"><Download size={13} /></button>
          {type === "html" && (
            <button onClick={openInNewTab} className="artifact-btn" title="新窗口打开"><ExternalLink size={13} /></button>
          )}
          <button onClick={() => setExpanded(!expanded)} className="artifact-btn" title={expanded ? "缩小" : "全屏"}>
            {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          </button>
        </div>
      </div>
      {type === "svg" ? (
        <div className="artifact-svg-content" dangerouslySetInnerHTML={{ __html: code }} />
      ) : (
        <iframe
          ref={iframeRef}
          sandbox="allow-scripts"
          className="artifact-iframe"
          style={{ height: expanded ? "80vh" : iframeHeight }}
        />
      )}
    </div>
  );
}
