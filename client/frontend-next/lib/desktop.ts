/** lib/desktop.ts — 桌面端能力桥（V64）。
 *
 * Marvis 架构同款思路：UI 是同一套 Web 代码，桌面壳通过 preload 注入 JS 桥
 * （window.hashmmDesktop / hashmmLocal / hashmmTerm）。本模块做安全封装：
 * - 浏览器环境：所有 API 为 null，UI 不渲染桌面专属入口（web/桌面同一套代码）。
 * - 桌面环境：检测到桥即解锁「文件 / 终端 / 用量」页签。
 */

export type LocalItem = { name: string; path: string; dir: boolean; size: number; mtime?: number };

type DesktopApi = {
  isDesktop: boolean;
  appVersion: () => Promise<string>;
  getConfig: () => Promise<any>;
  probe: (url: string) => Promise<{ ok: boolean; ms: number; detail?: any; kind?: string }>;
  getPreset: () => Promise<{ preset: string }>;
  setPreset: (preset: string) => Promise<{ ok: boolean; preset?: string; error?: string }>;
  connect: (cfg: { url: string; token?: string }) => Promise<any>;
  reset: () => Promise<boolean>;
  onNav?: (cb: (view: string) => void) => () => void;
  setOverlay?: (dark: boolean) => Promise<boolean>;
  goLocal?: () => Promise<boolean>;
  packStatus?: (id: string) => Promise<{ installed: boolean; dir: string }>;
  packInstall?: (id: string) => Promise<{ ok: boolean; dir?: string; error?: string }>;
  onPackProgress?: (cb: (d: { id: string; pct: number }) => void) => () => void;
  onConfirmClose?: (cb: () => void) => () => void;
  closeChoice?: (choice: "tray" | "quit" | "cancel", remember: boolean) => void;
};

type LocalApi = {
  roots: () => Promise<{ ok: boolean; roots: { name: string; path: string }[] }>;
  list: (dir: string) => Promise<{ ok: boolean; dir: string; items: LocalItem[]; error?: string }>;
  read: (file: string) => Promise<{ ok: boolean; kind?: "text" | "image"; text?: string; dataUrl?: string; ext?: string; error?: string }>;
  write: (file: string, text: string) => Promise<{ ok: boolean; bytes?: number; error?: string }>;
  gitLog: (cwd?: string, limit?: number) => Promise<{ ok: boolean; commits?: any[]; cwd?: string; error?: string }>;
  search: (dir: string, q: string) => Promise<{ ok: boolean; items: LocalItem[]; error?: string }>;
  grep?: (dir: string, q: string) => Promise<{ ok: boolean; items: (LocalItem & { matches?: { line: number; text: string }[] })[]; error?: string }>;
  recent?: (dir: string) => Promise<{ ok: boolean; items: LocalItem[]; error?: string }>;
  agentUsage: () => Promise<{ claude: any; codex: any } | null>;
};

type TermApi = {
  available: boolean;
  spawn: (opts: { id: string; cwd?: string | null; cols: number; rows: number }) => Promise<{ ok: boolean; cwd?: string; shell?: string; error?: string; reused?: boolean; replay?: string }>;
  input: (id: string, data: string) => void;
  resize: (id: string, cols: number, rows: number) => void;
  kill: (id: string) => void;
  proc: (id: string) => Promise<{ ok: boolean; proc?: string }>;
  runAgent: (id: string, agent: string) => Promise<{ ok: boolean; error?: string }>;
  onData: (cb: (m: { id: string; data: string }) => void) => () => void;
  onExit: (cb: (m: { id: string; exitCode: number }) => void) => () => void;
  onFsChanged?: (cb: (m: { dir: string; filename: string | null }) => void) => () => void;
  watchSet?: (dirs: string[]) => Promise<any>;
};

function w(): any { return typeof window !== "undefined" ? (window as any) : {}; }

/** 是否运行在 HashMM 桌面端里（preload 桥已注入） */
export function isDesktop(): boolean {
  return !!(w().hashmmDesktop && w().hashmmDesktop.isDesktop);
}

export const desktop: DesktopApi | null = typeof window !== "undefined" && w().hashmmDesktop ? w().hashmmDesktop : null;
export const local: LocalApi | null = typeof window !== "undefined" && w().hashmmLocal ? w().hashmmLocal : null;
export const term: TermApi | null = typeof window !== "undefined" && w().hashmmTerm ? w().hashmmTerm : null;

/** 惰性取（preload 注入时序兜底） */
export function getDesktop(): DesktopApi | null { return w().hashmmDesktop || null; }
export function getLocal(): LocalApi | null { return w().hashmmLocal || null; }
export function getTerm(): TermApi | null { return w().hashmmTerm || null; }

/** V86: Computer Use 桥（截屏等）。V99 加 GUI 控制工具与动作回放。 */
export type CUApi = {
  capture: (opts?: { hideSelf?: boolean }) => Promise<{ ok: boolean; dataUrl?: string; error?: string }>;
  precapture?: () => Promise<{ ok: boolean }>;
  tools?: (vision?: boolean, control?: boolean) => Promise<unknown[]>;
  exec?: (name: string, args: Record<string, unknown>) => Promise<unknown>;
  replay?: (n?: number) => Promise<{ ok: boolean; events: { ts: number; type: string; summary: string; ok: boolean; error: string }[]; text: string }>;
  replayClear?: () => Promise<{ ok: boolean }>;
  getSafety?: () => Promise<{ ok: boolean; level: "normal" | "readonly" | "strict" }>;
  setSafety?: (level: "normal" | "readonly" | "strict") => Promise<{ ok: boolean; level: string }>;
  getFileDir?: () => Promise<{ ok: boolean; dir: string; custom?: boolean }>;
  setFileDir?: (dir: string) => Promise<{ ok: boolean; dir: string }>;
  pickFileDir?: () => Promise<{ ok: boolean; dir?: string; canceled?: boolean; error?: string }>;
};
export function getCU(): CUApi | null { return w().hashmmCU || null; }

/** V98: 本地语义增强桥（V77 起 preload 已注入，本版前端首次消费）。
 *  设备检测 → 下载 bge-small-zh INT8 → 开关"为本地后端提供嵌入服务"。 */
export type SemanticApi = {
  deviceCheck: () => Promise<{ ok: boolean; memGB: number; cores: number; advice?: string; ortInstalled: boolean; modelDownloaded: boolean; enabled: boolean }>;
  downloadModel: () => Promise<{ ok: boolean; error?: string }>;
  enable: () => Promise<{ ok: boolean; reason?: string }>;
  disable: () => Promise<{ ok: boolean }>;
  reindex: () => Promise<{ ok: boolean; error?: string; stat?: unknown }>;
  getServe?: () => Promise<{ ok: boolean; on: boolean }>;
  setServe?: (on: boolean) => Promise<{ ok: boolean; on?: boolean; needRestart?: boolean; error?: string }>;
  onProgress: (cb: (m: { stage: string; pct: number }) => void) => () => void;
};
export function getSemantic(): SemanticApi | null { return w().hashmmSemantic || null; }

/** V96: 文件保存桥（微信式下载位置：固定文件夹 / 每次询问 / 重名加序号）。 */
export type FilesApi = {
  getSaveConfig: () => Promise<{ saveMode: "ask" | "fixed"; saveDir: string }>;
  chooseSaveDir: () => Promise<{ ok: boolean; saveDir?: string }>;
  setSaveMode: (mode: "ask" | "fixed") => Promise<boolean>;
  save: (filename: string, content: string, isDataUrl?: boolean) => Promise<{ ok: boolean; path?: string; error?: string; canceled?: boolean }>;
  revealInFolder: (p: string) => Promise<boolean>;
  openPath: (p: string) => Promise<boolean>;
};
export function getFiles(): FilesApi | null { return w().hashmmFiles || null; }

/** V101: 后端数据位置（解析文档/知识库落盘位置；默认安装位置，可改）。仅桌面端。 */
export type BackendDataApi = {
  getDataDir: () => Promise<{ ok: boolean; dataDir: string; isDefault: boolean; defaultDir: string }>;
  chooseDataDir: () => Promise<{ ok: boolean; dataDir?: string; needRestart?: boolean; canceled?: boolean; error?: string }>;
  resetDataDir: () => Promise<{ ok: boolean; dataDir: string; needRestart?: boolean }>;
};
export function getBackendData(): BackendDataApi | null {
  const b = w().hashmmBackend;
  return b && typeof b.getDataDir === "function" ? b : null;
}

/** V103.15: 远程传输（远程桌面）宿主桥。零依赖 WS 服务端，启动即给配对码；
 *  另一台设备用查看端连 ws://<本机IP>:<port>，输入 6 位码后开始看屏/控制。 */
export type RemoteApi = {
  start: (opts?: { port?: number; host?: string }) => Promise<{ ok: boolean; port?: number; code?: string; expiresAt?: number; addrs?: string[]; error?: string }>;
  stop: () => Promise<{ ok: boolean }>;
  status: () => Promise<{ ok: boolean; running: boolean; port?: number; clients?: number; paired?: number; hostConnected?: boolean; webrtcActive?: number; addrs?: string[] }>;
  issueCode: () => Promise<{ ok: boolean; code?: string; expiresAt?: number; error?: string }>;
  currentCode: () => Promise<{ ok: boolean; code?: string | null; expiresAt?: number; remainingMs?: number }>;
  setIce?: (iceServers: { urls: string; username?: string; credential?: string }[]) => Promise<{ ok: boolean; error?: string }>;
  getIce?: () => Promise<{ ok: boolean; iceServers: { urls: string; username?: string; credential?: string }[] }>;
  manualOffer?: () => Promise<{ ok: boolean; code?: string; error?: string }>;
  manualAnswer?: (code: string) => Promise<{ ok: boolean; error?: string }>;
  viewerHtml?: () => Promise<{ ok: boolean; html?: string; error?: string }>;
  startAccountHost?: (token: string) => Promise<{ ok: boolean; signal?: string; error?: string }>;
  updateAccountToken?: (token: string) => void;
  stopAccountHost?: () => Promise<{ ok: boolean }>;
  accountHostStatus?: () => Promise<{ ok: boolean; on: boolean; signal?: string }>;
  openAccountViewer?: (opts?: { token?: string; target?: string }) => Promise<{ ok: boolean; error?: string }>;
  // V103.51 对标 UU 远程的扩展能力
  wake?: (mac: string, opts?: { port?: number; address?: string; count?: number }) => Promise<{ ok: boolean; sent?: number; bytes?: number; error?: string }>;
  listWolTargets?: () => Promise<{ ok: boolean; targets: { id: string; name: string; mac: string; address?: string }[] }>;
  saveWolTarget?: (t: { id?: string; name: string; mac: string; address?: string }) => Promise<{ ok: boolean; id?: string; targets?: { id: string; name: string; mac: string; address?: string }[]; error?: string }>;
  removeWolTarget?: (id: string) => Promise<{ ok: boolean; targets?: { id: string; name: string; mac: string; address?: string }[] }>;
  listMonitors?: () => Promise<{ ok: boolean; monitors?: { id: number; index: number; primary: boolean; width: number; height: number; scaleFactor: number; label: string }[]; error?: string }>;
  setRemoteQuality?: (q: { fps?: number; jpeg?: number }) => Promise<{ ok: boolean; fps?: number; jpeg?: number; error?: string }>;
  setPrivacy?: (on: boolean) => Promise<{ ok: boolean; privacy?: boolean; error?: string }>;
};
export function getRemote(): RemoteApi | null { return w().hashmmRemote || null; }

/** 统一保存：桌面端走可配置位置 + 提示；web 回退浏览器下载。 */
export async function saveFile(filename: string, content: string, opts?: { isDataUrl?: boolean }): Promise<{ ok: boolean; path?: string; canceled?: boolean }> {
  const files = getFiles();
  if (files) {
    const r = await files.save(filename, content, opts?.isDataUrl);
    return r;
  }
  // web 回退
  let href: string;
  if (opts?.isDataUrl) { href = content; }
  else { href = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" })); }
  const a = document.createElement("a"); a.href = href; a.download = filename; a.click();
  if (!opts?.isDataUrl) setTimeout(() => URL.revokeObjectURL(href), 1000);
  return { ok: true };
}
