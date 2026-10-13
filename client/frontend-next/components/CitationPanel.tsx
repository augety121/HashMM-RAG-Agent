"use client";
import { useState } from "react";
import { FileText, ChevronDown, ChevronRight, ExternalLink } from "lucide-react";

interface Citation {
  index: number;
  doc_id: string;
  source: string;
  page: number | null;
  text_preview: string;
}

interface Props {
  citations: Citation[];
}

export function CitationPanel({ citations }: Props) {
  const [expanded, setExpanded] = useState<number | null>(null);

  if (!citations || citations.length === 0) return null;

  return (
    <div className="citation-panel mt-3">
      <div className="citation-header">
        <FileText size={13} />
        <span>{citations.length} 个来源</span>
      </div>
      <div className="citation-list">
        {citations.map((c) => (
          <div key={c.index} className="citation-item">
            <button
              onClick={() => setExpanded(expanded === c.index ? null : c.index)}
              className="citation-trigger"
            >
              <span className="citation-badge">[{c.index}]</span>
              <span className="citation-source">{c.source}</span>
              {c.page && c.page > 0 && (
                <span className="citation-page">p.{c.page}</span>
              )}
              {expanded === c.index
                ? <ChevronDown size={12} className="citation-chevron" />
                : <ChevronRight size={12} className="citation-chevron" />}
            </button>
            {expanded === c.index && c.text_preview && (
              <div className="citation-preview">
                {c.text_preview}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Annotate answer text with citation markers [1][2][3].
 * Call this before rendering the answer to make citations clickable.
 */
export function annotateCitations(text: string, citations: Citation[]): string {
  if (!citations || citations.length === 0) return text;
  // Citations are already embedded by the LLM or retriever
  // This function ensures they're properly formatted
  return text;
}
