"use client";
import { useState } from "react";
import { FileCode, ChevronDown } from "lucide-react";

interface Props {
  diff: string;
  filename?: string;
}

interface DiffLine {
  type: "add" | "remove" | "context" | "range" | "header";
  content: string;
  lineOld?: number;
  lineNew?: number;
}

function parseDiffLines(diff: string): DiffLine[] {
  const rawLines = diff.split("\n");
  const result: DiffLine[] = [];
  let oldLine = 0;
  let newLine = 0;

  for (const line of rawLines) {
    if (line.startsWith("@@")) {
      const match = line.match(/@@ -(\d+)/);
      if (match) {
        oldLine = parseInt(match[1], 10);
        const matchNew = line.match(/\+(\d+)/);
        if (matchNew) newLine = parseInt(matchNew[1], 10);
      }
      result.push({ type: "range", content: line });
    } else if (line.startsWith("---") || line.startsWith("+++")) {
      result.push({ type: "header", content: line });
    } else if (line.startsWith("+")) {
      result.push({ type: "add", content: line.slice(1), lineNew: newLine });
      newLine++;
    } else if (line.startsWith("-")) {
      result.push({ type: "remove", content: line.slice(1), lineOld: oldLine });
      oldLine++;
    } else {
      result.push({ type: "context", content: line.startsWith(" ") ? line.slice(1) : line, lineOld: oldLine, lineNew: newLine });
      oldLine++;
      newLine++;
    }
  }
  return result;
}

export function DiffBlock({ diff, filename }: Props) {
  const [open, setOpen] = useState(true);
  const lines = parseDiffLines(diff);
  const added = lines.filter((l) => l.type === "add").length;
  const removed = lines.filter((l) => l.type === "remove").length;

  return (
    <div className="diff-viewer my-3">
      <button
        className="diff-viewer-header"
        onClick={() => setOpen(!open)}
      >
        <FileCode size={14} className="diff-viewer-icon" />
        <span className="diff-viewer-filename">{filename || "文件变更"}</span>
        <span className="diff-viewer-stats">
          {added > 0 && <span className="diff-stat-add">+{added}</span>}
          {removed > 0 && <span className="diff-stat-del">-{removed}</span>}
        </span>
        <ChevronDown
          size={14}
          className={`diff-viewer-chevron ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="diff-viewer-body">
          {lines
            .filter((l) => l.type !== "header")
            .map((line, i) => {
              if (line.type === "range") {
                return (
                  <div key={i} className="diff-line diff-line-range">
                    <span className="diff-line-num" />
                    <span className="diff-line-num" />
                    <span className="diff-line-marker" />
                    <span className="diff-line-content">{line.content}</span>
                  </div>
                );
              }
              return (
                <div
                  key={i}
                  className={`diff-line diff-line-${line.type}`}
                >
                  <span className="diff-line-num">
                    {line.type !== "add" ? line.lineOld ?? "" : ""}
                  </span>
                  <span className="diff-line-num">
                    {line.type !== "remove" ? line.lineNew ?? "" : ""}
                  </span>
                  <span className="diff-line-marker">
                    {line.type === "add"
                      ? "+"
                      : line.type === "remove"
                        ? "-"
                        : " "}
                  </span>
                  <span className="diff-line-content">{line.content}</span>
                </div>
              );
            })}
        </div>
      )}
    </div>
  );
}
