"use client";
/** components/desktop/WorkbenchView.tsx — FanBox 式工作台（V98 新增）。
 *
 *  vibe coding 驾驶舱：左侧浏览本机文件，右侧内嵌真终端跑 agent，
 *  agent 每写一个文件左侧卡片实时点亮（find files → run agents → see what changed）。
 *
 *  联动链路：
 *  1. FileBrowser 浏览到哪个目录 → fs:watchSet 监听该目录（main.js fs.watch）
 *  2. agent 在终端写文件 → 主进程发 fs:changed{dir,filename}
 *  3. 本组件收到 → ①命中文件卡品牌紫描边点亮 4s（子目录变更点亮子目录卡）
 *                  ②顶部"最近变更"条滚动 ③当前目录静默重列（新文件冒出来）
 *                  ④若正预览该文件 → 自动重读（看着 agent 改你的代码）
 *  4. 右侧 TerminalView 与"终端"页签共享同一 PTY（main.js 同 id 复用+回放）
 *
 *  分栏可拖拽，宽度持久化 localStorage。非桌面端（web）优雅降级为提示页。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getLocal, getTerm, type LocalItem } from "@/lib/desktop";
import { X, FileText, Activity, MonitorX, FolderCog, MessageSquare, Globe2, Users, Monitor } from "lucide-react";
import { useStore } from "@/lib/store";
import { openBrowserInInspector } from "@/lib/browserInspector";
import { FileBrowser } from "./FileBrowser";
import { TerminalView } from "./TerminalView";
import { EditorPane } from "./EditorPane";
import { GitGraphView } from "./GitGraphView";
import { RepoChangesView } from "./RepoChangesView";
import { CockpitAgent } from "./CockpitAgent";
import { joinPath } from "./util";

const SPLIT_KEY = "hashmm.workbench.split";
const LIT_MS = 4000;          // 卡片点亮时长
type Change = { path: string; name: string; ts: number };

export function WorkbenchView() {
  const L = getLocal();
  const T = getTerm();
  const desktopOk = !!(L && T);
  const [cockpit, setCockpit] = useState(true);   // V103.1: 右侧驾驶舱(终端+agent)可折叠 → 文件区全宽（合并"本机文件"后兼顾纯浏览）
  const [cockpitTab, setCockpitTab] = useState<"changes" | "term" | "commits">("changes");
  const [gitOpened, setGitOpened] = useState(true);                        // 默认展示变更/Review
  const enterChat = useCallback((mode: "browser" | "canvas" | "team" | "computer" | "") => {
    useStore.getState().set({
      desktopView: null,
      adminOpen: false,
      setOpen: false,
      pendingRunMode: mode,
    });
    if (mode === "browser") {
      openBrowserInInspector(useStore.getState().browserPanel?.url || "https://www.bing.com/");
    }
  }, []);

  // ── 分栏宽度（拖拽 + 持久化） ──
  const [leftW, setLeftW] = useState(() => {
    try { const v = parseInt(localStorage.getItem(SPLIT_KEY) || "", 10); if (v >= 260 && v <= 720) return v; } catch {}
    return 380;
  });
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const onDividerDown = (e: React.PointerEvent) => {
    dragRef.current = { startX: e.clientX, startW: leftW };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onDividerMove = (e: React.PointerEvent) => {
    if (!dragRef.current) return;
    const w = Math.min(720, Math.max(260, dragRef.current.startW + e.clientX - dragRef.current.startX));
    setLeftW(w);
  };
  const onDividerUp = () => {
    if (!dragRef.current) return;
    dragRef.current = null;
    try { localStorage.setItem(SPLIT_KEY, String(leftW)); } catch {}
  };

  // ── 变更联动状态 ──
  const [changed, setChanged] = useState<Record<string, number>>({});
  const [recent, setRecent] = useState<Change[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const dirRef = useRef("");

  // ── 工作区目录（V173：Agent 终端/写文件/文件区的默认根目录，用户可选，系统目录被拒）──
  const [wsDir, setWsDir] = useState<string>("");
  const [wsKey, setWsKey] = useState(0);   // 改工作区后自增 → 重挂 FileBrowser，切到新目录
  useEffect(() => {
    const ws = (typeof window !== "undefined" ? (window as any).hashmmWorkspace : null);
    ws?.get?.().then((r: any) => { if (r && r.ok) setWsDir(r.dir); }).catch(() => {});
  }, []);
  const chooseWorkspace = useCallback(async () => {
    const ws = (typeof window !== "undefined" ? (window as any).hashmmWorkspace : null);
    if (!ws?.choose) return;
    const r = await ws.choose().catch(() => null);
    if (r && r.ok && r.dir) { setWsDir(r.dir); setWsKey((k) => k + 1); }
  }, []);
  const activateWorkspace = useCallback((dir: string) => {
    setWsDir(dir);
    setWsKey((key) => key + 1);
    setReloadKey((key) => key + 1);
    setSel(null); selRef.current = null; setPv(null); setEditing(false);
  }, []);
  const relistT = useRef<any>(null);
  const fadeT = useRef<any>(null);
  const watchable = !!(T && T.watchSet && T.onFsChanged);

  const onDirChange = useCallback((dir: string) => {
    dirRef.current = dir;
    if (T?.watchSet) T.watchSet([dir]).catch(() => {});
  }, [T]);

  // ── 预览（底部抽屉，变更自动重读） ──
  const [sel, setSel] = useState<LocalItem | null>(null);
  const [editing, setEditing] = useState(false);   // V103.7: 预览区切换为 Monaco 编辑器
  const selRef = useRef<LocalItem | null>(null);
  const [pv, setPv] = useState<{ kind?: string; text?: string; dataUrl?: string; error?: string } | null>(null);
  const [pvLive, setPvLive] = useState(false);   // 刚被 agent 改过的脉冲标记
  const rereadT = useRef<any>(null);

  const readInto = useCallback(async (it: LocalItem) => {
    if (!L) return;
    const r = await L.read(it.path);
    if (!r || !r.ok) { setPv({ error: (r && r.error) || "无法预览" }); return; }
    setPv(r);
  }, [L]);
  const preview = useCallback((it: LocalItem) => {
    setSel(it); selRef.current = it; setPv(null); setPvLive(false); setEditing(false);
    readInto(it);
  }, [readInto]);
  const closePreview = () => { setSel(null); selRef.current = null; setPv(null); setEditing(false); };

  // ── fs:changed 订阅 ──
  useEffect(() => {
    if (!T?.onFsChanged) return;
    const off = T.onFsChanged(m => {
      const ts = Date.now();
      const rel = m.filename || "";
      const full = rel ? joinPath(m.dir, rel) : m.dir;
      const firstSeg = rel.split(/[\\/]/).filter(Boolean)[0] || "";
      setChanged(prev => {
        const next = { ...prev, [full]: ts };
        if (firstSeg) next[joinPath(m.dir, firstSeg)] = ts;   // 子目录里变更 → 点亮子目录卡
        return next;
      });
      const name = rel.split(/[\\/]/).filter(Boolean).pop() || rel || m.dir;
      setRecent(prev => [{ path: full, name, ts }, ...prev.filter(c => c.path !== full)].slice(0, 30));
      // 当前目录静默重列（debounce，agent 连环写不抖）
      clearTimeout(relistT.current);
      relistT.current = setTimeout(() => setReloadKey(k => k + 1), 400);
      // 点亮 4s 后淡出：到点再触发一次渲染
      clearTimeout(fadeT.current);
      fadeT.current = setTimeout(() => setChanged(prev => ({ ...prev })), LIT_MS + 200);
      // 正预览的文件被改 → 自动重读（live）
      const cur = selRef.current;
      if (cur && cur.path === full) {
        clearTimeout(rereadT.current);
        rereadT.current = setTimeout(() => {
          readInto(cur); setPvLive(true);
          setTimeout(() => setPvLive(false), 2000);
        }, 300);
      }
    });
    return () => {
      off();
      clearTimeout(relistT.current); clearTimeout(fadeT.current); clearTimeout(rereadT.current);
      T.watchSet?.([]).catch(() => {});   // 离开工作台停表，不留监听
    };
  }, [T, readInto]);

  if (!desktopOk) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2" style={{ color: "var(--text-tertiary)" }}>
        <MonitorX size={30} strokeWidth={1.5} />
        <div className="text-[14px] font-semibold" style={{ color: "var(--text-secondary)" }}>工作台需要桌面端</div>
        <div className="text-[12px]">文件 + 终端同屏联动依赖本机能力，请在 HashMM 桌面应用里使用。</div>
      </div>
    );
  }

  const latest = recent[0];
  const fresh = recent.filter(c => Date.now() - c.ts < 60000).length;

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      <div className="workbench-capabilities h-12 px-3 flex items-center gap-2 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-primary)" }}>
        <div className="min-w-0 mr-auto">
          <div className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>当前工作</div>
          <div className="text-[10px] truncate" style={{ color: "var(--text-tertiary)" }}>文件、任务结果和 Chat 能力在同一个工作区继续</div>
        </div>
        {([
          ["", MessageSquare, "回到对话", "继续当前会话"],
          ["browser", Globe2, "浏览网页", "在右侧打开网页并交给 Browser Use"],
          ["canvas", FileText, "工作画布", "在当前会话创建或打开画布"],
          ["team", Users, "多智能体", "在当前会话预览分工并启动团队"],
          ["computer", Monitor, "电脑操作", "在当前会话启用 Computer Use"],
        ] as const).map(([mode, Icon, label, title]) => (
          <button key={mode || "chat"} onClick={() => enterChat(mode)} title={title}
            className="h-8 px-2.5 rounded-lg inline-flex items-center gap-1.5 text-[10.5px] font-medium hover:bg-[var(--bg-tertiary)]"
            style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
            <Icon size={13} /><span className="workspace-capability-label">{label}</span>
          </button>
        ))}
      </div>
      <div className="flex flex-1 min-h-0">
      {/* ── 左：文件区（驾驶舱收起时全宽，等价于原"本机文件"纯浏览） ── */}
      <div className={`flex flex-col min-h-0 ${cockpit ? "flex-shrink-0" : "flex-1"}`}
        style={{ width: cockpit ? leftW : undefined, borderRight: cockpit ? "1px solid var(--border)" : "none" }}>
        {/* 最近变更条：agent 写盘的实时心跳 */}
        <div className="flex items-center gap-2 px-3 py-1.5 text-[10.5px] flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)", background: latest && Date.now() - latest.ts < LIT_MS ? "var(--accent-light)" : "var(--bg-secondary)", transition: "background .4s" }}>
          <Activity size={11} style={{ color: latest ? "var(--accent)" : "var(--text-tertiary)" }} className="flex-shrink-0" />
          {latest ? (
            <button className="flex-1 min-w-0 flex items-center gap-1.5 text-left hover:underline"
              onClick={() => preview({ name: latest.name, path: latest.path, dir: false, size: 0 } as LocalItem)}
              title={latest.path}>
              <span className="font-mono truncate" style={{ color: "var(--accent)" }}>{latest.name}</span>
              <span style={{ color: "var(--text-tertiary)" }}>{new Date(latest.ts).toLocaleTimeString("zh-CN", { hour12: false })}</span>
            </button>
          ) : (
            <span className="flex-1" style={{ color: "var(--text-tertiary)" }}>
              {watchable ? "终端里 agent 一写文件，这里就会点亮" : "此版本不支持文件监听"}
            </span>
          )}
          {fresh > 1 && <span className="px-1.5 rounded-full font-mono flex-shrink-0" style={{ background: "var(--accent)", color: "#fff" }}>{fresh}</span>}
          {recent.length > 0 && (
            <button onClick={() => { setRecent([]); setChanged({}); }} className="flex-shrink-0 hover:opacity-70" title="清空变更记录">
              <X size={11} style={{ color: "var(--text-tertiary)" }} />
            </button>
          )}
          <button onClick={() => setCockpit(c => !c)} className="flex-shrink-0 px-1.5 rounded hover:opacity-70 font-medium"
            style={{ color: "var(--text-tertiary)" }}
            title={cockpit ? "收起右侧驾驶舱，文件区全宽（纯浏览）" : "展开右侧驾驶舱（终端 + agent 联动）"}>
            {cockpit ? "▸ 收起驾驶舱" : "◂ 驾驶舱"}
          </button>
        </div>
        {/* 工作区目录条：当前 Agent 工作目录 + 更换（系统盘根 / 系统目录会被拒绝） */}
        <div className="flex items-center gap-1.5 px-3 py-1.5 text-[10.5px] flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
          <FolderCog size={11} style={{ color: "var(--text-tertiary)" }} className="flex-shrink-0" />
          <span className="flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>工作区</span>
          <span className="flex-1 min-w-0 font-mono truncate" style={{ color: "var(--text-secondary)" }} title={wsDir}>{wsDir || "（默认）"}</span>
          <button onClick={chooseWorkspace} className="flex-shrink-0 px-2 py-0.5 rounded-md hover:bg-[var(--bg-tertiary)] font-medium"
            style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
            title="选择 Agent 的工作目录（终端、写文件、文件区的默认根目录）。系统盘根目录与系统目录不可选。">更换</button>
        </div>
        {/* 浏览列（变更点亮 + 静默重列） */}
        <div className="flex-1 min-h-0 flex flex-col" style={{ height: sel ? "55%" : undefined }}>
          <FileBrowser key={"fb-" + wsKey} selPath={sel?.path} onSelect={preview} onDirChange={onDirChange}
            changed={changed} reloadKey={reloadKey} />
        </div>
        {/* 底部预览抽屉（被 agent 改了自动刷） */}
        {sel && (
          <div className="flex-shrink-0 flex flex-col min-h-0" style={{ height: "45%", borderTop: "1px solid var(--border)" }}>
            <div className="flex items-center gap-1.5 px-3 py-1.5 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
              <FileText size={11} style={{ color: "var(--text-tertiary)" }} className="flex-shrink-0" />
              <span className="flex-1 text-[10.5px] font-mono truncate" style={{ color: "var(--text-secondary)" }} title={sel.path}>{sel.name}</span>
              {pvLive && <span className="text-[10px] px-1.5 rounded-full flex-shrink-0" style={{ background: "var(--accent)", color: "#fff" }}>已更新</span>}
              {pv?.kind === "text" && (
                <button onClick={() => setEditing(e => !e)} title={editing ? "切回预览" : "编辑此文件（Monaco，可 Ctrl+S 存盘）"}
                  className="flex-shrink-0 text-[10px] px-2 py-0.5 rounded-md transition-colors hover:bg-[var(--bg-tertiary)]"
                  style={{ border: "1px solid var(--border)", color: editing ? "var(--accent)" : "var(--text-secondary)" }}>
                  {editing ? "预览" : "编辑"}
                </button>
              )}
              <button onClick={closePreview} className="flex-shrink-0 hover:opacity-70"><X size={12} style={{ color: "var(--text-tertiary)" }} /></button>
            </div>
            {editing && pv?.kind === "text" ? (
              <div className="flex-1 min-h-0 flex flex-col">
                <EditorPane path={sel.path} onClose={() => setEditing(false)} />
              </div>
            ) : (
              <div className="flex-1 overflow-auto" style={{ boxShadow: pvLive ? "inset 0 0 0 1.5px var(--accent)" : "none", transition: "box-shadow .5s" }}>
                {!pv && <div className="p-4 text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>读取中…</div>}
                {pv?.error && <div className="p-4 text-[11.5px]" style={{ color: "var(--text-tertiary)" }}>无法预览：{pv.error}</div>}
                {pv?.kind === "image" && <img src={pv.dataUrl} alt="" className="max-w-full block mx-auto my-3 rounded-lg" />}
                {pv?.kind === "text" && (
                  <pre className="m-0 p-3 text-[11.5px] leading-relaxed whitespace-pre-wrap break-words font-mono" style={{ color: "var(--text-primary)" }}>{pv.text}</pre>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      {cockpit && (
        <>
          {/* ── 分隔条（可拖拽） ── */}
          <div onPointerDown={onDividerDown} onPointerMove={onDividerMove} onPointerUp={onDividerUp}
            className="flex-shrink-0 cursor-col-resize group" style={{ width: 5, marginLeft: -3, zIndex: 5 }}>
            <div className="w-[1.5px] h-full mx-auto transition-colors group-hover:bg-[var(--accent)]" style={{ background: "transparent" }} />
          </div>
          {/* ── 右：HashMM 自己动手（复用 cu.ts 循环）+ 终端/Git图（与"终端"页签共享同一会话） ── */}
          <div className="flex-1 min-w-0 flex flex-col">
            <CockpitAgent workspaceDir={wsDir} />
            <div className="flex items-center gap-1 px-2 pt-1 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
              {([["changes", "变更 / Review"], ["term", "终端"], ["commits", "提交图"]] as const).map(([v, label]) => (
                <button key={v} onClick={() => { setCockpitTab(v); if (v !== "term") setGitOpened(true); }}
                  className="px-2.5 py-1 text-[11.5px] rounded-md transition-colors"
                  style={{ background: cockpitTab === v ? "var(--bg-tertiary)" : "transparent", color: cockpitTab === v ? "var(--text-primary)" : "var(--text-tertiary)" }}>{label}</button>
              ))}
            </div>
            <div className="flex-1 min-h-0 relative">
              <div className="absolute inset-0 flex flex-col" style={{ display: cockpitTab === "term" ? "flex" : "none" }}><TerminalView key={`term-${wsKey}`} /></div>
              {gitOpened && <div className="absolute inset-0 flex flex-col" style={{ display: cockpitTab === "changes" ? "flex" : "none" }}><RepoChangesView cwd={wsDir} onWorkspaceChange={activateWorkspace} /></div>}
              {gitOpened && <div className="absolute inset-0 flex flex-col" style={{ display: cockpitTab === "commits" ? "flex" : "none" }}><GitGraphView workspaceDir={wsDir} /></div>}
            </div>
          </div>
        </>
      )}
      </div>
    </div>
  );
}
