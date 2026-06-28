"use client";
/** components/desktop/TerminalView.tsx — 内嵌真终端（V98 自 DesktopPanel 拆出）。
 *
 *  xterm + node-pty。V98 起 main.js 对同 id spawn 改为"复用 + 回放"：
 *  终端视图和工作台共享 TID 这一个 PTY 会话——切走再回来、或在工作台里
 *  打开同一会话，输出回放接续，绝不双开 shell。
 *
 *  V103.5（借鉴 Ridge 的分屏终端）：把单终端逻辑抽成自包含的 <TermPane id=.../>，
 *  默认仍是单格（id=TID，行为与之前完全一致）；点"分屏"再加一格右侧独立 shell
 *  （id=TID2），中间可拖拽分隔条。每格各自独立、互不影响——分屏即使有问题，
 *  也不影响单格默认路径。后端 term:spawn/resize/kill 早已按 id 支持多会话。 */
import { useEffect, useRef, useState } from "react";
import { getTerm } from "@/lib/desktop";
import { Play, Eraser, RefreshCw, Columns2, Rows2, X } from "lucide-react";
import { TID, loadScript, loadCss } from "./util";

declare global { interface Window { Terminal?: any; FitAddon?: any } }

/** 单个终端面板：完整自包含（工具条 + 状态 + xterm + PTY 会话），按 id 区分会话。 */
function TermPane({ id, onClose, onSplit }: { id: string; onClose?: () => void; onSplit?: (dir: "h" | "v") => void }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<any>(null);
  const aliveRef = useRef(false);
  const [state, setState] = useState("加载中…");
  const [running, setRunning] = useState(false);
  const [flash, setFlash] = useState("");
  const T = getTerm();

  useEffect(() => {
    let disposed = false;
    const offs: (() => void)[] = [];
    (async () => {
      if (!T || !T.available) { setState("终端不可用（node-pty 未编译，desktop/ 跑 npm run rebuild）"); return; }
      try {
        loadCss("https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css");
        await loadScript("https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js");
        await loadScript("https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js");
      } catch { setState("xterm 加载失败（网络）"); return; }
      if (disposed || !hostRef.current) return;
      const W = window as any;
      const term = new W.Terminal({
        fontSize: 13, fontFamily: 'Consolas,"Cascadia Code",Menlo,monospace',
        theme: { background: "#0B0B0E", foreground: "#E4E4E7", cursor: "#FAFAFA" },
        cursorBlink: true, scrollback: 5000,
      });
      const fit = new W.FitAddon.FitAddon();
      term.loadAddon(fit);
      term.open(hostRef.current);
      fit.fit();
      termRef.current = term;

      const r = await T.spawn({ id, cwd: null, cols: term.cols, rows: term.rows });
      if (!r || !r.ok) { term.writeln("\x1b[31m启动失败: " + ((r && r.error) || "未知") + "\x1b[0m"); setState("启动失败"); return; }
      aliveRef.current = true;
      if (r.reused) {
        setState((r.shell || "shell") + " · 会话接续");
        if (r.replay) term.write(r.replay);
      } else {
        setState((r.shell || "shell") + " · " + (r.cwd || ""));
        term.writeln("\x1b[90m" + (r.shell || "shell") + " · " + (r.cwd || "") + "\x1b[0m");
        term.writeln("\x1b[90m点上方按钮直接拉起 Claude Code / Codex。\x1b[0m");
      }

      offs.push(T.onData(m => { if (m.id === id) term.write(m.data); }));
      offs.push(T.onExit(m => { if (m.id === id) { aliveRef.current = false; term.writeln("\r\n\x1b[33m[会话结束]\x1b[0m"); setState("会话结束"); } }));
      term.onData((d: string) => { if (aliveRef.current) T.input(id, d); });
      term.onResize(({ cols, rows }: any) => { if (aliveRef.current) T.resize(id, cols, rows); });
      const ro = new ResizeObserver(() => { try { fit.fit(); } catch {} });
      ro.observe(hostRef.current);
      offs.push(() => ro.disconnect());

      const iv = setInterval(async () => {
        if (!aliveRef.current) { setRunning(false); return; }
        try {
          const p = await T.proc(id);
          const proc = ((p && p.proc) || "").toLowerCase();
          if (proc.includes("claude")) { setState("● Claude Code 运行中"); setRunning(true); }
          else if (proc.includes("codex")) { setState("● Codex 运行中"); setRunning(true); }
          else { setState(proc || "shell"); setRunning(false); }
        } catch {}
      }, 1500);
      offs.push(() => clearInterval(iv));
      if (T.onFsChanged) {
        let ft: any = null;
        offs.push(T.onFsChanged(m => {
          setFlash("⟳ 文件已变更" + (m.filename ? "：" + m.filename : ""));
          clearTimeout(ft); ft = setTimeout(() => setFlash(""), 2500);
        }));
      }
    })();
    return () => { disposed = true; offs.forEach(f => { try { f(); } catch {} }); };
    // 注意：不 kill PTY——切走页签回来会话还在（驾驶舱语义，main.js 复用+回放兜底）
  }, [T, id]);

  const run = (agent: string) => { const t = getTerm(); if (t) { t.runAgent(id, agent); termRef.current?.focus(); } };

  return (
    <div className="flex flex-1 min-h-0 min-w-0 flex-col">
      <div className="flex items-center gap-2 px-4 py-2 overflow-x-auto" style={{ borderBottom: "1px solid var(--border)" }}>
        <span className="text-[11px] font-mono truncate min-w-0" style={{ color: running ? "var(--success, #16A34A)" : "var(--text-tertiary)" }}>{state}</span>
        <span className="text-[10px] font-mono ml-auto mr-1 transition-opacity flex-shrink-0" style={{ color: "var(--accent)", opacity: flash ? 1 : 0 }}>{flash}</span>
        <div className="flex items-center gap-2 flex-shrink-0">
        <button onClick={() => run("claude")} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-medium text-white" style={{ background: "var(--accent)" }}><Play size={11} /> Claude Code</button>
        <button onClick={() => run("codex")} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] font-medium text-white" style={{ background: "var(--accent)" }}><Play size={11} /> Codex</button>
        <button onClick={() => termRef.current?.clear()} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><Eraser size={11} /> 清屏</button>
        <button onClick={async () => { const t = getTerm(); if (!t) return; t.kill(id); termRef.current?.clear(); await new Promise(r => setTimeout(r, 250)); location.reload(); }}
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><RefreshCw size={11} /> 重启会话</button>
        {onSplit && (<>
          <button onClick={() => onSplit("h")} title="左右分屏（各跑独立 shell）" className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><Columns2 size={11} /> 左右</button>
          <button onClick={() => onSplit("v")} title="上下分屏（各跑独立 shell）" className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[12px] transition-colors hover:bg-[var(--bg-tertiary)]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><Rows2 size={11} /> 上下</button>
        </>)}
        {onClose && (
          <button onClick={onClose} title="关闭此分屏" className="flex items-center justify-center w-7 h-7 rounded-lg transition-colors hover:bg-[var(--bg-tertiary)]" style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}><X size={13} /></button>
        )}
        </div>
      </div>
      <div className="flex-1 min-h-0 p-2" style={{ background: "#0B0B0E" }}>
        <div ref={hostRef} className="w-full h-full" />
      </div>
    </div>
  );
}

type Sess = { key: string; name: string; base: string; dir: "" | "h" | "v" };

export function TerminalView() {
  const [sessions, setSessions] = useState<Sess[]>([{ key: "s0", name: "终端 1", base: TID, dir: "" }]);
  const [active, setActive] = useState(0);
  const [ratio, setRatio] = useState(0.5);
  const [editing, setEditing] = useState<number | null>(null);   // 正在重命名的标签
  const seqRef = useRef(1);
  const dragRef = useRef(false);
  const dragWrapRef = useRef<HTMLElement | null>(null);          // 拖拽时记录活动会话的分屏容器

  // 分隔条拖拽（纯 React）——方向从容器的 data-dir 读取，取 x 或 y
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const wrap = dragWrapRef.current;
      if (!dragRef.current || !wrap) return;
      const vert = wrap.getAttribute("data-dir") === "v";
      const rect = wrap.getBoundingClientRect();
      const f = vert ? (e.clientY - rect.top) / rect.height : (e.clientX - rect.left) / rect.width;
      setRatio(Math.min(0.8, Math.max(0.2, f)));
    };
    const onUp = () => { dragRef.current = false; dragWrapRef.current = null; document.body.style.cursor = ""; document.body.style.userSelect = ""; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, []);

  const setDir = (i: number, d: "" | "h" | "v") => setSessions(ss => ss.map((s, j) => (j === i ? { ...s, dir: d } : s)));
  const addSession = () => {
    const n = ++seqRef.current;
    setSessions(ss => [...ss, { key: "s" + n + "-" + Date.now(), name: "终端 " + (ss.length + 1), base: TID + "-s" + n, dir: "" }]);
    setActive(sessions.length);   // 旧长度=新会话索引，切到新建会话
  };
  const closeSession = (i: number) => {
    if (sessions.length <= 1) return;
    const t = sessions[i]; const term = getTerm();
    // 关闭=终止该会话的 shell；但首个会话的 base 是 TID（与工作台/驾驶舱共享），不杀，仅从标签移除
    try { if (t.base !== TID) term?.kill(t.base); term?.kill(t.base + "-split2"); } catch (_e) {}
    setSessions(ss => ss.filter((_, j) => j !== i));
    setActive(a => (i <= a ? Math.max(0, a - 1) : a));
  };
  const rename = (i: number, name: string) => setSessions(ss => ss.map((s, j) => (j === i ? { ...s, name: name || s.name } : s)));

  const renderBody = (s: Sess) => {
    if (!s.dir) {
      return <div className="flex flex-1 min-h-0"><TermPane id={s.base} onSplit={(d) => setDir(active, d)} /></div>;
    }
    const vert = s.dir === "v", base2 = s.base + "-split2";
    const sz1 = `calc(${ratio * 100}% - 3px)`, sz2 = `calc(${(1 - ratio) * 100}% - 3px)`;
    return (
      <div data-dir={s.dir} className={`flex flex-1 min-h-0 min-w-0 ${vert ? "flex-col" : "flex-row"}`}>
        <div className="flex min-h-0 min-w-0" style={vert ? { height: sz1 } : { width: sz1 }}><TermPane id={s.base} /></div>
        <div onMouseDown={(e) => { dragRef.current = true; dragWrapRef.current = (e.currentTarget as HTMLElement).parentElement; document.body.style.cursor = vert ? "row-resize" : "col-resize"; document.body.style.userSelect = "none"; }}
          title={vert ? "拖动调整上下高度" : "拖动调整左右宽度"}
          className="flex-shrink-0 transition-colors bg-[var(--border)] hover:bg-[var(--accent)]"
          style={vert ? { height: 6, cursor: "row-resize" } : { width: 6, cursor: "col-resize" }} />
        <div className="flex min-h-0 min-w-0" style={vert ? { height: sz2 } : { width: sz2 }}><TermPane id={base2} onClose={() => setDir(active, "")} /></div>
      </div>
    );
  };

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      {/* tmux 式会话标签栏：多命名会话，单击切换 / ＋新建 / ×关闭 / 双击重命名 */}
      <div className="flex items-center gap-1 px-2 pt-1.5 overflow-x-auto flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        {sessions.map((s, i) => (
          <div key={s.key} onClick={() => setActive(i)} onDoubleClick={() => setEditing(i)}
            className="group flex items-center gap-1 px-2.5 py-1 rounded-t-lg text-[11.5px] cursor-pointer whitespace-nowrap flex-shrink-0"
            style={{ background: i === active ? "var(--bg-secondary)" : "transparent", color: i === active ? "var(--text-primary)" : "var(--text-tertiary)", border: i === active ? "1px solid var(--border)" : "1px solid transparent", borderBottom: "none" }}>
            {editing === i ? (
              <input autoFocus defaultValue={s.name}
                onBlur={(e) => { rename(i, e.target.value.trim()); setEditing(null); }}
                onKeyDown={(e) => { const v = (e.target as HTMLInputElement).value.trim(); if (e.key === "Enter") { rename(i, v); setEditing(null); } else if (e.key === "Escape") setEditing(null); }}
                onClick={(e) => e.stopPropagation()}
                className="bg-transparent outline-none w-16 text-[11.5px]" style={{ color: "var(--text-primary)", borderBottom: "1px solid var(--accent)" }} />
            ) : <span>{s.name}</span>}
            {sessions.length > 1 && (
              <button onClick={(e) => { e.stopPropagation(); closeSession(i); }} title="关闭会话"
                className="opacity-0 group-hover:opacity-60 hover:!opacity-100 flex items-center"><X size={11} /></button>
            )}
          </div>
        ))}
        <button onClick={addSession} title="新建终端会话（独立 shell）"
          className="px-2 py-0.5 text-[15px] leading-none rounded-md hover:bg-[var(--bg-tertiary)] flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>＋</button>
      </div>
      {/* 会话全部挂载、按 display 切换（保留各自 xterm/PTY，不重建、不丢现场） */}
      <div className="flex flex-1 min-h-0 relative">
        {sessions.map((s, i) => (
          <div key={s.key} className="absolute inset-0 flex flex-col" style={{ display: i === active ? "flex" : "none" }}>
            {renderBody(s)}
          </div>
        ))}
      </div>
    </div>
  );
}
