"use client";
import { useMemo } from "react";
import { parseContentBlocks, Block } from "@/lib/contentParser";
import { renderMsg } from "@/lib/render";
import { CodeBlock } from "./CodeBlock";
import { DiffBlock } from "./DiffBlock";

interface Props {
  content: string;
  convId?: string;
}

/** Unified message renderer — replaces dangerouslySetInnerHTML with typed Block components. */
export function MessageRenderer({ content, convId: propConvId }: Props) {
  // v27: Extract convId from URL if not provided via props
  const convId = propConvId || (typeof window !== 'undefined' 
    ? window.location.pathname.match(/\/chat\/([^/]+)/)?.[1] 
    : undefined);
  const blocks = useMemo(() => {
    try {
      return parseContentBlocks(content);
    } catch (_e) {
      // Fallback: entire content as one text block
      return [{ type: "text" as const, content }];
    }
  }, [content]);

  return (
    <div className="msg-content-v2">
      {blocks.map((block, i) => (
        <BlockRenderer key={i} block={block} convId={convId} />
      ))}
    </div>
  );
}

function BlockRenderer({ block, convId }: { block: Block; convId?: string }) {
  switch (block.type) {
    case "code":
      return (
        <CodeBlock
          code={block.content}
          language={String(block.meta?.language || "text")}
          filename={block.meta?.filename ? String(block.meta.filename) : undefined}
          lineCount={Number(block.meta?.lineCount || block.content.split("\n").length)}
          convId={convId}
        />
      );

    case "diff":
      return <DiffBlock diff={block.content} />;

    case "mermaid":
      // Mermaid needs external lib — fallback to code display
      return (
        <div className="cb-wrap my-3">
          <div className="cb-head"><span className="cb-lang">MERMAID</span></div>
          <pre className="cb-code"><code>{block.content}</code></pre>
        </div>
      );

    case "table":
      // Tables: render via renderMsg (it handles markdown tables)
      return (
        <div
          className="msg-table-block"
          dangerouslySetInnerHTML={{ __html: renderMsg(block.content) }}
        />
      );

    case "math_block":
      return (
        <div
          className="msg-math-block"
          dangerouslySetInnerHTML={{ __html: renderMsg(`$$${block.content}$$`) }}
        />
      );

    case "text":
    default:
      // Text blocks: use renderMsg for inline formatting (bold, links, inline code, LaTeX)
      if (!block.content.trim()) return null;
      return (
        <div
          className="msg-text-block"
          dangerouslySetInnerHTML={{ __html: renderMsg(block.content) }}
        />
      );
  }
}
