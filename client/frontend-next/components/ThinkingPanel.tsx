"use client";
import { useState, useEffect, useRef } from "react";
import { ChevronDown } from "lucide-react";

interface Props {
  content: string;
  streaming?: boolean;
  defaultOpen?: boolean;
}

export function ThinkingPanel({ content, streaming = false, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen || streaming);
  const contentRef = useRef<HTMLDivElement>(null);

  // Auto-expand when streaming, auto-collapse when done
  useEffect(() => {
    if (streaming) {
      setOpen(true);
    }
  }, [streaming]);

  // Auto-collapse 1s after streaming ends
  useEffect(() => {
    if (!streaming && content && open) {
      const timer = setTimeout(() => setOpen(false), 1200);
      return () => clearTimeout(timer);
    }
  }, [streaming, content, open]);

  // Auto-scroll to bottom during streaming
  useEffect(() => {
    if (streaming && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [content, streaming]);

  if (!content) return null;

  return (
    <div className="thinking-panel mb-3">
      <button
        onClick={() => setOpen(!open)}
        className="thinking-panel-header"
      >
        <span className="thinking-panel-title">
          {streaming ? "正在思考" : "思考过程"}
          {streaming && <span className="thinking-dots"><span>.</span><span>.</span><span>.</span></span>}
        </span>
        <ChevronDown
          size={14}
          className={`thinking-panel-chevron ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div
          ref={contentRef}
          className="thinking-panel-content"
        >
          {content}
        </div>
      )}
    </div>
  );
}
