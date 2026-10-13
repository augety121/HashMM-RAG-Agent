/**
 * ContentParser v20 — parse markdown-like content into typed blocks.
 * Replaces regex-based renderMsg() with structured block parsing.
 */

export interface Block {
  type: "text" | "code" | "table" | "math_block" | "math_inline" | "image" | "diff" | "mermaid";
  content: string;
  meta?: { language?: string; filename?: string; lineCount?: number; [key: string]: string | number | undefined; };
}

export function parseContentBlocks(content: string): Block[] {
  const blocks: Block[] = [];
  const lines = content.split('\n');
  let i = 0;
  let textBuf: string[] = [];

  const flushText = () => {
    if (textBuf.length > 0) {
      blocks.push({ type: "text", content: textBuf.join('\n') });
      textBuf = [];
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // Code block: ```lang
    if (line.match(/^```(\w*)/)) {
      flushText();
      const lang = (line.match(/^```(\w*)/) || [])[1] || "";
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      const code = codeLines.join('\n');
      
      if (lang === 'mermaid') {
        blocks.push({ type: "mermaid", content: code });
      } else if (lang === 'diff') {
        blocks.push({ type: "diff", content: code });
      } else {
        // Extract filename from first line comment
        const fnMatch = code.match(/^[#\/]+\s*filename:\s*(\S+)/m);
        blocks.push({
          type: "code",
          content: code,
          meta: { language: lang || "text", filename: fnMatch?.[1] || "", lineCount: codeLines.length }
        });
      }
      continue;
    }

    // Block math: $$...$$
    if (line.trim().startsWith('$$')) {
      flushText();
      const mathLines: string[] = [line.replace('$$', '').trim()];
      if (!line.trim().endsWith('$$') || line.trim() === '$$') {
        i++;
        while (i < lines.length && !lines[i].trim().endsWith('$$')) {
          mathLines.push(lines[i]);
          i++;
        }
        if (i < lines.length) {
          mathLines.push(lines[i].replace('$$', '').trim());
        }
      }
      blocks.push({ type: "math_block", content: mathLines.join('\n').trim() });
      i++;
      continue;
    }

    // Table detection: | --- |
    if (line.trim().startsWith('|') && i + 1 < lines.length && lines[i + 1]?.match(/^\|[\s\-:]+\|/)) {
      flushText();
      const tableLines: string[] = [line];
      i++;
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i]);
        i++;
      }
      blocks.push({ type: "table", content: tableLines.join('\n') });
      continue;
    }

    // Regular text line
    textBuf.push(line);
    i++;
  }

  flushText();
  return blocks;
}
