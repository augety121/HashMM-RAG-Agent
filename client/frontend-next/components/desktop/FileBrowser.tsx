"use client";
/** components/desktop/FileBrowser.tsx — 可复用本机文件浏览列（V98 模块化）。
 *
 *  从原 FilesView 左列抽出：位置(roots) / 面包屑导航 / 文件名·内容·最近修改
 *  三模式搜索 / 文件列表。FilesView（全屏浏览+预览）和 WorkbenchView
 *  （FanBox 式文件+终端同屏）共用这一个实现。
 *
 *  工作台联动钩子：
 *  - onDirChange(dir)  浏览目录变化 → 父级据此 watchSet 监听该目录
 *  - changed           path → 时间戳，命中卡片描边点亮（agent 写文件实时反馈）
 *  - reloadKey         父级收到 fs:changed 后自增 → 当前目录静默重列（新文件冒出来）
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { getLocal, type LocalItem } from "@/lib/desktop";
import { Folder, FileText, Search, HardDrive } from "lucide-react";
import { fmtSize } from "./util";

export function FileBrowser({
  selPath, onSelect, onDirChange, changed, reloadKey,
}: {
  selPath?: string | null;
  onSelect: (it: LocalItem) => void;
  onDirChange?: (dir: string) => void;
  changed?: Record<string, number>;
  reloadKey?: number;
}) {
  const L = getLocal();
  const [roots, setRoots] = useState<{ name: string; path: string }[]>([]);
  const [dir, setDir] = useState("");
  const [items, setItems] = useState<LocalItem[]>([]);
  const [crumbTitle, setCrumbTitle] = useState("");
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"name" | "grep" | "recent">("name");
  const timer = useRef<any>(null);

  const openDir = useCallback(async (d: string) => {
    if (!L) return;
    const r = await L.list(d);
    if (r && r.ok) { setDir(r.dir); setItems(r.items); setCrumbTitle(""); onDirChange?.(r.dir); }
  }, [L, onDirChange]);

  useEffect(() => {
    if (!L) return;
    L.roots().then(r => {
      if (r && r.ok) { setRoots(r.roots); if (r.roots[0]) openDir(r.roots[0].path); }
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [L]);

  // 工作台收到 fs:changed → 静默重列当前目录（不打断搜索态）
  useEffect(() => {
    if (!reloadKey || !L || !dir || q.trim().length >= 2) return;
    L.list(dir).then(r => { if (r && r.ok && r.dir === dir) setItems(r.items); }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  const onSearch = (val: string, m?: "name" | "grep") => {
    setQ(val);
    const useMode = m || mode;
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      if (!L) return;
      if (val.trim().length < 2) { if (dir) openDir(dir); return; }
      const r = useMode === "grep" && L.grep
        ? await L.grep(dir, val.trim())
        : await L.search(dir, val.trim());
      if (r && r.ok) {
        setItems(r.items);
        setCrumbTitle(`${useMode === "grep" ? "内容" : "文件名"}搜索 "${val.trim()}" · ${r.items.length} 个结果`);
      }
    }, 350);
  };

  const showRecent = async () => {
    if (!L || !L.recent) return;
    setMode("recent");
    const r = await L.recent(dir);
    if (r && r.ok) { setItems(r.items); setCrumbTitle(`最近 72h 修改 · ${r.items.length} 个文件`); }
  };

  const crumbs = dir.split(/[\\/]/).filter(Boolean);
  const sep = dir.includes("\\") ? "\\" : "/";
  const now = Date.now();

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="px-3 pt-3 pb-1 text-[10px] font-semibold tracking-wider uppercase" style={{ color: "var(--text-tertiary)" }}>位置</div>
      <div className="px-2">
        {roots.map(r => (
          <button key={r.path} onClick={() => { setQ(""); openDir(r.path); }}
            className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[12.5px] transition-colors hover:bg-[var(--bg-tertiary)]"
            style={{ color: "var(--text-secondary)" }}>
            <HardDrive size={13} /> <span className="truncate">{r.name}</span>
          </button>
        ))}
      </div>
      <div className="px-3 pt-3 pb-1 text-[10px] font-semibold tracking-wider uppercase" style={{ color: "var(--text-tertiary)" }}>文件</div>
      <div className="px-3 pb-1">
        <div className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg" style={{ background: "var(--bg-tertiary)" }}>
          <Search size={12} style={{ color: "var(--text-tertiary)" }} />
          <input value={q} onChange={e => onSearch(e.target.value)}
            placeholder={mode === "grep" ? "搜索文件内容…" : "搜索文件名…"}
            className="flex-1 bg-transparent outline-none text-[12px]" style={{ color: "var(--text-primary)" }} />
        </div>
        <div className="flex gap-1 mt-1.5">
          {([["name", "文件名"], ["grep", "内容"], ["recent", "最近修改"]] as const).map(([m, label]) => (
            <button key={m}
              onClick={() => { if (m === "recent") { showRecent(); } else { setMode(m); if (q.trim().length >= 2) onSearch(q, m); } }}
              className="px-2 py-0.5 rounded-md text-[10px] transition-colors"
              style={{
                background: mode === m ? "var(--accent-light)" : "transparent",
                color: mode === m ? "var(--accent)" : "var(--text-tertiary)",
                border: "1px solid " + (mode === m ? "var(--accent)" : "var(--border)"),
              }}>{label}</button>
          ))}
        </div>
      </div>
      <div className="px-3 py-1 text-[10px] font-mono truncate" style={{ color: "var(--text-tertiary)" }} title={dir}>
        {crumbTitle || crumbs.map((p, i) => (
          <span key={i} className="cursor-pointer hover:underline"
            onClick={() => openDir((dir.startsWith("/") ? "/" : "") + crumbs.slice(0, i + 1).join(sep))}>
            {i ? " / " : ""}{p}
          </span>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {items.map(it => {
          const ts = changed?.[it.path];
          const lit = ts != null && now - ts < 4000;   // 4s 内变更 → 品牌紫描边点亮
          return (
            <button key={it.path} onClick={() => it.dir ? (setQ(""), openDir(it.path)) : onSelect(it)}
              className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[12.5px] transition-all hover:bg-[var(--bg-tertiary)] ${selPath === it.path ? "bg-[var(--accent-light)]" : ""}`}
              style={{
                color: selPath === it.path ? "var(--accent)" : "var(--text-secondary)",
                boxShadow: lit ? "inset 0 0 0 1.5px var(--accent)" : "none",
                background: lit && selPath !== it.path ? "var(--accent-light)" : undefined,
              }} title={it.path}>
              {it.dir ? <Folder size={13} className="flex-shrink-0" /> : <FileText size={13} className="flex-shrink-0" />}
              <span className="flex-1 truncate text-left">{it.name}</span>
              {lit && <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: "var(--accent)" }} />}
              {!it.dir && <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>{fmtSize(it.size)}</span>}
            </button>
          );
        })}
        {!items.length && <div className="text-center py-6 text-[11px]" style={{ color: "var(--text-tertiary)" }}>空</div>}
      </div>
    </div>
  );
}
