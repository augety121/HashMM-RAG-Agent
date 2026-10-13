"use client";
import { useState, useEffect } from "react";
import { ChevronRight, ChevronDown, File, Folder, FolderOpen, Download, Eye, RefreshCw,
         FileCode, FileText, FileSpreadsheet, Image as ImageIcon, Presentation, Braces, Globe, Palette } from "lucide-react";

interface FileInfo {
  path: string;
  size: number;
  ext: string;
  lines: number | null;
}

interface Props {
  convId: string;
  onFileClick?: (path: string) => void;
}

// V86: emoji 功能图标全面换 lucide（与 AgentLog/FileCard 同一套视觉语言）
const ICONS: Record<string, { Icon: React.ElementType; color: string }> = {
  ".py": { Icon: FileCode, color: "#3776ab" }, ".js": { Icon: FileCode, color: "#d97706" },
  ".ts": { Icon: FileCode, color: "#2563eb" }, ".java": { Icon: FileCode, color: "#b45309" },
  ".cpp": { Icon: FileCode, color: "#0891b2" }, ".c": { Icon: FileCode, color: "#0891b2" },
  ".go": { Icon: FileCode, color: "#06b6d4" }, ".rs": { Icon: FileCode, color: "#ea580c" },
  ".html": { Icon: Globe, color: "#e34c26" }, ".css": { Icon: Palette, color: "#7c3aed" },
  ".json": { Icon: Braces, color: "#6b7280" }, ".md": { Icon: FileText, color: "#6b7280" },
  ".txt": { Icon: FileText, color: "#6b7280" }, ".pptx": { Icon: Presentation, color: "#d97706" },
  ".docx": { Icon: FileText, color: "#2563eb" }, ".xlsx": { Icon: FileSpreadsheet, color: "#059669" },
  ".pdf": { Icon: FileText, color: "#ef4444" }, ".png": { Icon: ImageIcon, color: "#8b5cf6" },
  ".jpg": { Icon: ImageIcon, color: "#8b5cf6" }, ".csv": { Icon: FileSpreadsheet, color: "#059669" },
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}

interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  size: number;
  ext: string;
  lines: number | null;
  children: TreeNode[];
}

function buildTree(files: FileInfo[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", isDir: true, size: 0, ext: "", lines: null, children: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let current = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isLast = i === parts.length - 1;
      if (isLast) {
        current.children.push({
          name: part, path: f.path, isDir: false,
          size: f.size, ext: f.ext, lines: f.lines, children: []
        });
      } else {
        let dir = current.children.find(c => c.isDir && c.name === part);
        if (!dir) {
          dir = { name: part, path: parts.slice(0, i + 1).join("/"), isDir: true, size: 0, ext: "", lines: null, children: [] };
          current.children.push(dir);
        }
        current = dir;
      }
    }
  }
  // Sort: dirs first, then files alphabetically
  const sortNodes = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    nodes.forEach(n => { if (n.isDir) sortNodes(n.children); });
  };
  sortNodes(root.children);
  return root.children;
}

function TreeItem({ node, depth, onFileClick }: { node: TreeNode; depth: number; onFileClick?: (path: string) => void }) {
  const [open, setOpen] = useState(depth < 2);
  const meta = node.isDir
    ? { Icon: open ? FolderOpen : Folder, color: "#d97706" }
    : (ICONS[node.ext] || { Icon: File, color: "var(--text-tertiary)" });
  const indent = depth * 16;

  return (
    <>
      <div
        className="flex items-center gap-1.5 py-0.5 px-2 hover:bg-[var(--bg-secondary)] rounded cursor-pointer text-[12px] transition-colors"
        style={{ paddingLeft: indent + 8 }}
        onClick={() => node.isDir ? setOpen(!open) : onFileClick?.(node.path)}
      >
        {node.isDir && (open ? <ChevronDown size={12} /> : <ChevronRight size={12} />)}
        <meta.Icon size={13} style={{ color: meta.color, flexShrink: 0 }} />
        <span className="flex-1 truncate" style={{ color: node.isDir ? "var(--text-primary)" : "var(--text-secondary)" }}>
          {node.name}
        </span>
        {!node.isDir && (
          <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            {node.lines ? `${node.lines}行` : formatSize(node.size)}
          </span>
        )}
      </div>
      {node.isDir && open && node.children.map((child, i) => (
        <TreeItem key={i} node={child} depth={depth + 1} onFileClick={onFileClick} />
      ))}
    </>
  );
}

export function FileTreeView({ convId, onFileClick }: Props) {
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [projectType, setProjectType] = useState("");
  const [totalLines, setTotalLines] = useState(0);

  const refresh = () => {
    setLoading(true);
    fetch(`/api/conversations/${convId}/tree`)
      .then(r => r.json())
      .then(data => {
        setFiles(data.project?.files || []);
        setProjectType(data.project?.project_type || "");
        setTotalLines(data.project?.total_lines || 0);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { if (convId) refresh(); }, [convId]);

  const tree = buildTree(files);

  if (!files.length && !loading) return null;

  return (
    <div className="border-t" style={{ borderColor: "var(--border)" }}>
      <div className="flex items-center justify-between px-3 py-2">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
          <Folder size={12} style={{ color: "#d97706" }} /> 工作区 {projectType && `(${projectType})`} · {files.length} 文件 {totalLines > 0 && `· ${totalLines.toLocaleString()} 行`}
        </span>
        <button onClick={refresh} className="p-1 rounded hover:bg-[var(--bg-secondary)] transition-colors">
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} style={{ color: "var(--text-tertiary)" }} />
        </button>
      </div>
      <div className="max-h-[300px] overflow-y-auto pb-2">
        {tree.map((node, i) => (
          <TreeItem key={i} node={node} depth={0} onFileClick={onFileClick} />
        ))}
      </div>
    </div>
  );
}
