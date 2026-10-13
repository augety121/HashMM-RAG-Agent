/** lib/desktop.ts — 桌面端能力桥（V64）。
 *
 * Marvis 架构同款思路：UI 是同一套 Web 代码，桌面壳通过 preload 注入 JS 桥
 * （window.hashmmDesktop / hashmmLocal / hashmmTerm）。本模块做安全封装：
 * - 浏览器环境：所有 API 为 null，UI 不渲染桌面专属入口（web/桌面同一套代码）。
 * - 桌面环境：检测到桥即解锁「文件 / 终端 / 用量」页签。
 */

export type LocalItem = { name: string; path: string; dir: boolean; size: number; mtime?: number };

export type DesktopPrompt = {
  id: string;
  source: string;
  taskId: string;
  requestedAt: number;
  kind: "privacy" | "warning" | "danger" | "info" | "update";
  eyebrow: string;
  title: string;
  message: string;
  detail: string;
  target: string;
  boundary: string;
  buttons: Array<{ id: string; label: string; tone: "primary" | "secondary" | "danger" }>;
  cancelId: string;
  defaultId: string;
};

export type DesktopApprovalRecord = {
  schema: "hashmm.desktop-approval.v1";
  id: string;
  status: "pending" | "resolved" | "interrupted";
  prompt: DesktopPrompt;
  requestedAt: number;
  resolvedAt: number;
  decision: string;
  reason: string;
};

export type DesktopApprovalSnapshot = {
  schema: "hashmm.desktop-approval-snapshot.v1";
  taskId: string;
  activeId: string;
  queuedIds: string[];
  items: DesktopApprovalRecord[];
};

type DesktopApi = {
  isDesktop: boolean;
  appVersion: () => Promise<string>;
  getConfig: () => Promise<any>;
  probe: (url: string) => Promise<{ ok: boolean; ms: number; detail?: any; kind?: string }>;
  getPreset: () => Promise<{ preset: string }>;
  setPreset: (preset: string) => Promise<{ ok: boolean; preset?: string; error?: string }>;
  connect: (cfg: { url: string; token?: string }) => Promise<any>;
  reset: () => Promise<boolean>;
  openExternal?: (url: string) => Promise<{ ok: boolean; error?: string }>;
  workCanvasCacheGet?: (namespace: string, runId: string) => Promise<{
    ok: boolean; hit?: boolean; etag?: string; data?: unknown; error?: string;
  }>;
  workCanvasCachePut?: (namespace: string, runId: string, etag: string, data: unknown) => Promise<{
    ok: boolean; stored?: boolean; error?: string;
  }>;
  workCanvasCacheRemove?: (namespace: string, runId: string) => Promise<{
    ok: boolean; removed?: boolean; error?: string;
  }>;
  authSessionSave?: (refreshToken: string, subject?: string) => Promise<{
    ok: boolean; stored?: boolean; error?: string;
  }>;
  authSessionRefresh?: () => Promise<{
    ok: boolean; status?: "accepted" | "rejected" | "unavailable" | "superseded";
    token?: string; error?: string;
  }>;
  authSessionClear?: () => Promise<{ ok: boolean; cleared?: boolean; error?: string }>;
  onNav?: (cb: (view: string) => void) => () => void;
  setOverlay?: (dark: boolean) => Promise<boolean>;
  goLocal?: () => Promise<boolean>;
  packStatus?: (id: string) => Promise<{ installed: boolean; dir: string }>;
  packInstall?: (id: string) => Promise<{ ok: boolean; dir?: string; error?: string }>;
  onPackProgress?: (cb: (d: { id: string; pct: number }) => void) => () => void;
  onConfirmClose?: (cb: () => void) => () => void;
  closeChoice?: (choice: "tray" | "quit" | "cancel", remember: boolean) => void;
  onDesktopPrompt?: (cb: (prompt: DesktopPrompt) => void) => () => void;
  onDesktopApprovalChanged?: (cb: (snapshot: DesktopApprovalSnapshot) => void) => () => void;
  listDesktopApprovals?: () => Promise<DesktopApprovalSnapshot>;
  presentDesktopPrompt?: (id: string) => Promise<{ ok: boolean; error?: string }>;
  desktopPromptReady?: () => void;
  resolveDesktopPrompt?: (id: string, decision: string) => void;
};

type LocalApi = {
  roots: () => Promise<{ ok: boolean; roots: { name: string; path: string }[] }>;
  list: (dir: string) => Promise<{ ok: boolean; dir: string; items: LocalItem[]; error?: string }>;
  read: (file: string) => Promise<{ ok: boolean; kind?: "text" | "image"; text?: string; dataUrl?: string; ext?: string; error?: string }>;
  write: (file: string, text: string) => Promise<{ ok: boolean; bytes?: number; error?: string }>;
  gitLog: (cwd?: string, limit?: number) => Promise<{ ok: boolean; commits?: any[]; cwd?: string; error?: string }>;
  gitInspect: (cwd?: string) => Promise<RepoInspection>;
  gitWorktreeList?: (cwd?: string, options?: { keep_limit?: number; auto_cleanup?: boolean }) => Promise<WorktreeListResult>;
  gitWorktreeCreate?: (cwd: string | undefined, options: WorktreeCreateOptions) => Promise<WorktreeMutationResult>;
  gitWorktreeActivate?: (cwd: string | undefined, target: string) => Promise<WorktreeMutationResult & { dir?: string }>;
  gitWorktreeBranch?: (cwd: string | undefined, target: string, branch: string) => Promise<WorktreeMutationResult>;
  gitWorktreePermanent?: (cwd: string | undefined, target: string, permanent: boolean) => Promise<WorktreeMutationResult>;
  gitWorktreeRemove?: (cwd: string | undefined, target: string, expectedId: string) => Promise<WorktreeMutationResult>;
  gitWorkspaceLeaseAcquire?: () => Promise<{ ok: boolean; token?: string; workspace?: string; code?: string; error?: string }>;
  gitWorkspaceLeaseRelease?: (token: string) => Promise<{ ok: boolean; released?: boolean; code?: string; error?: string }>;
  search: (dir: string, q: string) => Promise<{ ok: boolean; items: LocalItem[]; error?: string }>;
  grep?: (dir: string, q: string) => Promise<{ ok: boolean; items: (LocalItem & { matches?: { line: number; text: string }[] })[]; error?: string }>;
  recent?: (dir: string) => Promise<{ ok: boolean; items: LocalItem[]; error?: string }>;
  agentUsage: () => Promise<{ claude: any; codex: any } | null>;
};

export type RepoStatusFile = { path: string; original_path?: string; index: string; worktree: string; untracked?: boolean; conflicted?: boolean };
export type RepoDiffChunk = { file: string; diff: string };
export type RepoInstructionSource = { path: string; relative: string; scope: "global" | "project"; bytes: number; content: string };
export type RepoInspection = {
  ok: boolean; error?: string; is_git?: boolean; cwd?: string; root?: string; branch?: string; head?: string;
  files?: RepoStatusFile[];
  unstaged?: { text: string; truncated: boolean; bytes: number; chunks: RepoDiffChunk[] };
  staged?: { text: string; truncated: boolean; bytes: number; chunks: RepoDiffChunk[] };
  instructions?: { sources: RepoInstructionSource[]; combined: string; bytes: number; max_bytes: number; truncated: boolean };
  plan?: { path: string; relative: string; content: string; bytes: number; truncated: boolean } | null;
};

export type ManagedWorktree = {
  id?: string; path: string; head: string; branch: string; detached: boolean; bare: boolean;
  locked: boolean; lock_reason?: string; prunable: boolean; exists: boolean; current: boolean; active: boolean;
  managed: boolean; permanent: boolean; base_ref?: string; base_commit?: string; created_at?: string; last_used_at?: string;
  local_changes_applied: boolean; copied_files: number; dirty: boolean; changed_files: number; status_error?: string;
  ignored_files?: number; ignored_risk_files?: number; ignored_risk_paths?: string[]; unique_detached_commits: boolean;
};
export type WorktreeListResult = {
  ok: boolean; code?: string; error?: string; repo_root?: string; repo_common?: string; managed_root?: string;
  keep_limit?: number; auto_cleanup?: boolean; items?: ManagedWorktree[];
};
export type WorktreeCreateOptions = {
  base_ref?: string; label?: string; include_local_changes?: boolean; copy_ignored?: boolean; permanent?: boolean;
  keep_limit?: number; auto_cleanup?: boolean;
};
export type WorktreeMutationResult = {
  ok: boolean; code?: string; error?: string; id?: string; path?: string; dir?: string; removed?: string;
  branch?: string; detached?: boolean; permanent?: boolean; local_changes_applied?: boolean; copied_files?: string[];
  cleanup_removed?: string[];
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

export type ProjectDesktopApi = {
  pickSourceFolders: () => Promise<{ ok: boolean; canceled?: boolean; paths?: string[]; error?: string }>;
  activateSource: (sourcePath: string) => Promise<{ ok: boolean; dir?: string; error?: string }>;
};

/** Local-only project folders. Paths never cross the server API boundary. */
export function getProjectDesktop(): ProjectDesktopApi | null {
  const bridge = w().hashmmProject;
  return bridge
    && typeof bridge.pickSourceFolders === "function"
    && typeof bridge.activateSource === "function"
    ? bridge
    : null;
}

export type CheckpointApi = {
  setTask: (id: string) => Promise<{ ok: boolean; taskId: string }>;
  list: (taskId?: string) => Promise<{ ok: boolean; taskId?: string; items?: unknown[] }>;
  rewind: (taskId: string | undefined, checkpointId: string) => Promise<{ ok: boolean; error?: string; canceled?: boolean }>;
  clear: (taskId?: string) => Promise<{ ok: boolean }>;
};
export function getCheckpoint(): CheckpointApi | null { return w().hashmmCheckpoint || null; }

/** V86: Computer Use 桥（截屏等）。V99 加 GUI 控制工具与动作回放。 */
export type CUApi = {
  capture: (opts?: { hideSelf?: boolean }) => Promise<{ ok: boolean; dataUrl?: string; error?: string }>;
  precapture?: () => Promise<{ ok: boolean }>;
  tools?: (vision?: boolean, control?: boolean) => Promise<unknown[]>;
  exec?: (name: string, args: Record<string, unknown>, taskId?: string) => Promise<unknown>;
  replay?: (n?: number) => Promise<{ ok: boolean; events: { ts: number; type: string; summary: string; ok: boolean; error: string }[]; text: string }>;
  replayClear?: () => Promise<{ ok: boolean }>;
  getSafety?: () => Promise<{ ok: boolean; level: "normal" | "readonly" | "strict" }>;
  setSafety?: (level: "normal" | "readonly" | "strict") => Promise<{ ok: boolean; level: string }>;
  getFileDir?: () => Promise<{ ok: boolean; dir: string; custom?: boolean }>;
  setFileDir?: (dir: string) => Promise<{ ok: boolean; dir: string }>;
  pickFileDir?: () => Promise<{ ok: boolean; dir?: string; canceled?: boolean; error?: string }>;
};
export function getCU(): CUApi | null { return w().hashmmCU || null; }

/** V339: isolated Electron browser + shared evidence stream. */
export type BrowserTraceEvent = {
  seq: number; ts: number; phase: "start" | "done" | "error" | "session"; action: string;
  url?: string; title?: string; image?: string; index?: number; keys?: string; dir?: string;
  error?: string; note?: string; blocked?: boolean; vw?: number; vh?: number;
  screenshot_present?: boolean;
  elements?: Array<{ i: number; text?: string; tag?: string; type?: string; role?: string; rect?: { x: number; y: number; w: number; h: number } }>;
};
export type BrowserPolicy = { version: number; allow: string[]; block: string[]; updated_at: number };
export type EmbeddedBrowserBounds = { x: number; y: number; width: number; height: number };
export type BrowserEvidenceLocator = {
  selector?: string; exact?: string; prefix?: string; suffix?: string;
  rect?: { x?: number; y?: number; width?: number; height?: number };
  fingerprint?: {
    tag?: string; role?: string; aria_label?: string; ancestor_path?: string[];
  };
};
export type EmbeddedBrowserState = {
  ok: boolean; ready?: boolean; owner?: string; engine?: string; event?: string;
  url?: string; title?: string; loading?: boolean; canBack?: boolean; canForward?: boolean;
  error?: string; errorCode?: number; blockedUrl?: string; bounds?: EmbeddedBrowserBounds;
  hasDocument?: boolean; preservedDocument?: boolean;
  selection?: { text?: string; title?: string; url?: string; locator?: BrowserEvidenceLocator; browser_session_id?: string; selection_hash?: string };
  located?: { found?: boolean; matched?: boolean; confidence?: number; match_reason?: string; text?: string; title?: string; url?: string };
};
export type BrowserApi = {
  state: () => Promise<{ ok: boolean; engine: string; session: { active: boolean; visible: boolean; url: string; title: string }; policy: BrowserPolicy; events: BrowserTraceEvent[]; shortcut: string }>;
  openCockpit: () => Promise<{ ok: boolean; reused?: boolean; error?: string }>;
  showControlled: () => Promise<{ ok: boolean }>;
  authorizeNavigation: (url: string) => Promise<{ ok: boolean; url?: string; host?: string; error?: string }>;
  embeddedMount: (owner: string, bounds: EmbeddedBrowserBounds) => Promise<EmbeddedBrowserState>;
  embeddedBounds: (owner: string, bounds: EmbeddedBrowserBounds) => Promise<EmbeddedBrowserState>;
  embeddedNavigate: (owner: string, url: string) => Promise<EmbeddedBrowserState>;
  embeddedCommand: (owner: string, command: "back" | "forward" | "reload" | "stop" | "selection" | "locate", options?: { locator?: BrowserEvidenceLocator }) => Promise<EmbeddedBrowserState>;
  embeddedUnmount: (owner: string) => Promise<{ ok: boolean; stale?: boolean; error?: string }>;
  embeddedState: () => Promise<EmbeddedBrowserState>;
  openExternal: (url: string) => Promise<{ ok: boolean; browser?: "chrome" | "system"; fallback?: boolean; error?: string }>;
  clearTrace: () => Promise<{ ok: boolean }>;
  setPolicy: (host: string, decision: "allow" | "block" | "remove") => Promise<{ ok: boolean; policy?: BrowserPolicy; error?: string }>;
  evaluate: (events: BrowserTraceEvent[], expected: string | string[], mode: "exact" | "subsequence" | "any_order") => { pass: boolean; mode: string; expected: string[]; actual: string[]; matched: number; missing: string[]; summary: string };
  datasetCase: (input: { goal: string; expected: string | string[]; mode: string; events: BrowserTraceEvent[]; result?: string }) => unknown;
  onEvent: (cb: (event: BrowserTraceEvent) => void) => () => void;
  onEmbeddedEvent: (cb: (event: EmbeddedBrowserState) => void) => () => void;
};
export function getBrowser(): BrowserApi | null { return w().hashmmBrowser || null; }

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
export type ApplicationHarnessReceipt = {
  schema: "hashmm.application-harness.v1";
  sessionId: string;
  application: "system-office";
  provider: "system-file-association";
  resource: { name: string; kind: string };
  state: "active" | "changed" | "observed" | "committed";
  revision: number;
  capabilities: Array<"open" | "observe_revision" | "read_revision" | "commit_revision">;
  integrity: {
    contentAddressed: true;
    explicitCommit: true;
    arbitraryFileAccess: false;
    arbitraryCommandExecution: false;
  };
};
export type FilesApi = {
  getSaveConfig: () => Promise<{ saveMode: "ask" | "fixed"; saveDir: string }>;
  chooseSaveDir: () => Promise<{ ok: boolean; saveDir?: string }>;
  setSaveMode: (mode: "ask" | "fixed") => Promise<boolean>;
  save: (filename: string, content: string, isDataUrl?: boolean) => Promise<{ ok: boolean; path?: string; error?: string; canceled?: boolean }>;
  revealInFolder: (p: string) => Promise<boolean>;
  openPath: (p: string) => Promise<boolean>;
  officeHandoffOpen?: (payload: { filename: string; content: string; isDataUrl: true }) => Promise<{ ok: boolean; id?: string; filename?: string; provider?: string; revision?: number; sha256?: string; harness?: ApplicationHarnessReceipt; error?: string }>;
  officeHandoffStatus?: (id: string) => Promise<{ ok: boolean; changed?: boolean; filename?: string; revision?: number; sha256?: string; harness?: ApplicationHarnessReceipt; error?: string }>;
  officeHandoffRead?: (id: string) => Promise<{ ok: boolean; filename?: string; size?: number; revision?: number; sha256?: string; dataUrl?: string; harness?: ApplicationHarnessReceipt; error?: string }>;
  officeHandoffAcknowledge?: (id: string, sha256: string) => Promise<{ ok: boolean; conflict?: boolean; revision?: number; sha256?: string; harness?: ApplicationHarnessReceipt; error?: string }>;
};
export function getFiles(): FilesApi | null { return w().hashmmFiles || null; }

/** V101: 后端数据位置（解析文档/知识库落盘位置；默认安装位置，可改）。仅桌面端。 */
export type BackendDataApi = {
  getDataDir: () => Promise<{
    ok: boolean;
    schema: "hashmm.project-vault-status.v1";
    dataDir: string;
    desiredDir: string;
    effectiveDir: string | null;
    source: "configured" | "installation_default";
    isDefault: boolean;
    defaultDir: string;
    exists: boolean;
    writable: boolean;
    availability: "ready" | "not_initialized" | "permission_denied";
    localBackendRunning: boolean;
    restartRequired: boolean;
    error?: string;
  }>;
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
  status: () => Promise<{ ok: boolean; running: boolean; port?: number; clients?: number; paired?: number; hostConnected?: boolean; webrtcActive?: number; addrs?: string[]; privacy?: boolean; privacyMode?: "remote-content-shield"; physicalScreenBlanked?: false; quality?: { schema: "hashmm.remote-quality.v1"; source: "lan" | "account"; sampledAt: number; ageMs: number; stale: boolean; generation: number; tierIndex: number; tierName: string; direction: "up" | "down" | "hold"; reason: string; autoAdjusted: boolean; health: { grade: "good" | "fair" | "poor" | "unknown"; label: string }; metrics: { rttMs: number | null; packetLossPct: number | null; availableOutgoingBitrateKbps: number | null; framesPerSecond: number | null } } | null }>;
  issueCode: () => Promise<{ ok: boolean; code?: string; expiresAt?: number; error?: string }>;
  currentCode: () => Promise<{ ok: boolean; code?: string | null; expiresAt?: number; remainingMs?: number }>;
  listTrustedDevices?: () => Promise<{ ok: boolean; devices: { deviceId: string; createdAt: number; lastUsedAt: number }[]; error?: string }>;
  revokeTrustedDevice?: (deviceId: string) => Promise<{ ok: boolean; error?: string }>;
  setIce?: (iceServers: { urls: string; username?: string; credential?: string }[]) => Promise<{ ok: boolean; error?: string }>;
  getIce?: () => Promise<{ ok: boolean; iceServers: { urls: string; username?: string; credential?: string }[] }>;
  manualOffer?: () => Promise<{ ok: boolean; code?: string; error?: string }>;
  manualAnswer?: (code: string) => Promise<{ ok: boolean; error?: string }>;
  viewerHtml?: () => Promise<{ ok: boolean; html?: string; error?: string }>;
    startAccountHost?: (token: string) => Promise<{ ok: boolean; registered?: boolean; state?: string; signal?: string; error?: string }>;
  updateAccountToken?: (token: string) => void;
  stopAccountHost?: () => Promise<{ ok: boolean }>;
    accountHostStatus?: () => Promise<{ ok: boolean; on: boolean; registered?: boolean; state?: string; detail?: string; deviceId?: string; signal?: string; attemptId?: string; traceId?: string; errorCode?: string; configRevision?: string; security?: Record<string, unknown> | null; updatedAt?: number }>;
  openAccountViewer?: (opts?: { token?: string; target?: string }) => Promise<{ ok: boolean; error?: string }>;
  networkBridgeStatus?: () => Promise<{
    ok: boolean;
    schema?: "hashmm.remote-network-bridge.v1" | "hashmm.remote-network-bridge.v2";
    overlayReady?: boolean;
    adapters?: { name: string; address: string; cidr: string; mac: string }[];
    clients?: {
      rdp: { available: boolean; path: string };
      moonlight: { available: boolean; path: string };
      easytier: { available: boolean; path: string };
    };
    mode?: "external-overlay-ready" | "native-webrtc";
    routingPolicy?: string[];
    easyTier?: { integration: string; installed: boolean; active: boolean; bundled: false; autoStart: false; secretCustody: string; licenseBoundary: string };
    disclosure?: string;
    error?: string;
  }>;
  openDirectViewer?: (opts: { target: string; port?: number }) => Promise<{ ok: boolean; target?: string; port?: number; error?: string }>;
  launchRdp?: (opts: { target: string; port?: number }) => Promise<{ ok: boolean; error?: string }>;
  launchMoonlight?: (opts: { target: string; app?: string }) => Promise<{ ok: boolean; error?: string }>;
  openNetworkGuide?: () => Promise<{ ok?: boolean; error?: string }>;
  // V103.51 对标 UU 远程的扩展能力
  wake?: (mac: string, opts?: { port?: number; address?: string; count?: number }) => Promise<{ ok: boolean; sent?: number; bytes?: number; error?: string }>;
  listWolTargets?: () => Promise<{ ok: boolean; targets: { id: string; name: string; mac: string; address?: string }[] }>;
  saveWolTarget?: (t: { id?: string; name: string; mac: string; address?: string }) => Promise<{ ok: boolean; id?: string; targets?: { id: string; name: string; mac: string; address?: string }[]; error?: string }>;
  removeWolTarget?: (id: string) => Promise<{ ok: boolean; targets?: { id: string; name: string; mac: string; address?: string }[] }>;
  listMonitors?: () => Promise<{ ok: boolean; monitors?: { id: number; index: number; primary: boolean; width: number; height: number; scaleFactor: number; label: string }[]; error?: string }>;
  setRemoteQuality?: (q: { fps?: number; jpeg?: number }) => Promise<{ ok: boolean; fps?: number; jpeg?: number; error?: string }>;
  setPrivacy?: (on: boolean) => Promise<{ ok: boolean; privacy?: boolean; privacyMode?: "remote-content-shield"; physicalScreenBlanked?: false; error?: string }>;
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
