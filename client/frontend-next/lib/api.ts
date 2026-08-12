import type { ChatResponse, Stats, ModelConfig, User, KB, AuditLog, Source, GroundingLedger, TraceStep, ToolStep, FileInfo, StreamDoneData, ConversationMeta, Message, TaskEvidenceGraph, CausalWorkGraph, ExecutionReceipt, ExecutionFrontier, CompletionGate, WorkFeed, WorkRun, WorkControlAction, WorkCommandResponse, WorkCanvas, WorkDecisionResponse, WorkArtifactAnnotation, WorkExecutionLease, WorkExecutionDevice } from "./types";
import { useStore, setTokens } from "./store";
import { createConnectivityTracker } from "./connectivityState";
import type { FeatureContext } from "./chatContext";
import { getDesktop, type BrowserEvidenceLocator } from "./desktop";
import {
  enqueueWorkCommand,
  drainWorkOutbox,
  type WorkOutboxEntry,
  type WorkOutboxDrainResult,
} from "./workOutbox";

function headers(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const token = useStore.getState().token;
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

/** Authorization header only (for raw fetch / non-JSON requests). */
export function authHeaders(): Record<string, string> {
  const token = useStore.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 下载某次评测的详细可分析报告(Markdown)。带 Bearer 鉴权，拿到文本后触发浏览器下载；
 *  报告同时已落盘到服务器 data/eval_runs/{runId}.report.md。 */
export async function downloadEvalReport(runId: string): Promise<void> {
  const res = await fetch(`/api/admin/eval/runs/${encodeURIComponent(runId)}/report`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const md = await res.text();
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `eval-report-${runId}.md`;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

/** 给下载链接 <a href> 拼上 token query 参数。
 * <a download> 直接跳转不会带 Authorization header，后端鉴权会失败(404对话不存在)，
 * 而后端 get_current_user 支持 ?token= query 参数，所以这里把 token 拼到 URL 上。 */
export function withToken(url: string): string {
  if (!url) return url;
  const token = useStore.getState().token;
  if (!token) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
}

/** 深度检索（Self-RAG）：模型驱动多跳 + deepseek 作答 + 自我批判 + 自适应再检索 + 忠实度门控。
 *  同源相对路径，带 Bearer（后端该端点也允许匿名）。后端无模型时返回 degraded:true。 */
export async function deepSearch(
  query: string,
  opts?: { top_k?: number; max_hops?: number; max_rounds?: number },
): Promise<{
  answer: string; initial_answer?: string; sources?: Source[]; grounded?: boolean;
  confidence?: number | null; retrieval_top_score?: number | null; rounds?: number;
  trace?: TraceStep[]; answer_by?: string | null; degraded?: boolean; mode?: "full" | "lite" | "off";
}> {
  const r = await fetch("/api/deepsearch", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      query,
      top_k: opts?.top_k ?? 5,
      max_hops: opts?.max_hops ?? 3,
      max_rounds: opts?.max_rounds ?? 2,
    }),
  });
  if (!r.ok) throw new Error(`深度检索失败 (${r.status})`);
  return await r.json();
}

// ── v17 Phase 65: silent token refresh ──────────────────────────────────
// Access tokens are short-lived; a long-lived refresh token transparently
// renews them. We refresh (a) proactively when the access token is about to
// expire, and (b) reactively on a 401, retrying the original request once.
function _decodeExp(token: string): number | null {
  try {
    const payload = token.split(".")[1];
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return typeof json.exp === "number" ? json.exp : null;
  } catch { return null; }
}

// ── V269 离线模式内核 ─────────────────────────────────────────────────
// 判定标准：网络级错误（后端进程/隧道整个不在）或壳层代回的 502/503（Electron 桌面端
// 后端不可达时由内置壳层统一应答）→ 后端离线。离线是**功能降级**而不是登录问题：
// 绝不登出、绝不清缓存；每 25s 轻探测 /api/health，一恢复就自动重同步侧栏与统计。
let _reprobeTimer: ReturnType<typeof setInterval> | null = null;
const _backendConnectivity = createConnectivityTracker(2);
function _setBackendOnline(v: boolean) {
  const st = useStore.getState();
  if (st.backendOnline === v) {
    if (!v && st.token && st.authSessionState !== "reauth-required") {
      st.set({ authSessionState: "offline-valid" });
    }
    return;
  }
  st.set({
    backendOnline: v,
    ...(st.token && st.authSessionState !== "reauth-required"
      ? { authSessionState: v ? "authenticated" : "offline-valid" }
      : {}),
  });
  if (v) {
    if (_reprobeTimer) { clearInterval(_reprobeTimer); _reprobeTimer = null; }
    // 恢复在线：静默补同步（侧栏 + 统计），并广播给关心的组件（横幅/提示）
    import("./store").then(m => { const t = useStore.getState().token; if (t) m.syncConversations(t); }).catch(() => {});
    try { stats().then(s => useStore.getState().set({ stats: s })).catch(() => {}); } catch { /* */ }
    try { window.dispatchEvent(new CustomEvent("hmm-backend-online")); } catch { /* */ }
  } else if (!_reprobeTimer && typeof window !== "undefined") {
    _reprobeTimer = setInterval(() => { probeBackend().catch(() => {}); }, 25000);
  }
}

function _reportBackendSuccess() {
  _backendConnectivity.success();
  _setBackendOnline(true);
}

function _reportBackendFailure() {
  if (_backendConnectivity.failure()) _setBackendOnline(false);
}
/** 轻探测后端可达性（离线横幅的「重试连接」也走这里）。可达=非 502/503 的任何 HTTP 应答。 */
export async function probeBackend(): Promise<boolean> {
  try {
    const r = await fetch("/api/health");
    const offline = r.status === 502 || r.status === 503;
    if (offline) _reportBackendFailure(); else _reportBackendSuccess();
    return !offline;
  } catch { _reportBackendFailure(); return false; }
}
/** 构造带标记的"后端未连接"错误：offline=true 供调用方分流（不当 404、不删缓存、不登出）。 */
function _offlineError(): Error {
  _reportBackendFailure();
  const e = new Error("后端未连接，请确认服务已启动后重试") as Error & { offline?: boolean; status?: number };
  e.offline = true;
  return e;
}

// V265: 刷新结果哨兵——区分"离线（后端没起来）"与"真的失效"。
// 离线时返回它，调用方保留登录态、不登出；只有明确失效才登出。
const REFRESH_UNAVAILABLE = "__refresh_unavailable__" as const;
let _refreshInFlight: Promise<string | null> | null = null;

async function _doRefresh(): Promise<string | null> {
  // Single-flight: concurrent callers share one refresh request.
  if (_refreshInFlight) return _refreshInFlight;
  if (useStore.getState().token) {
    useStore.getState().set({ authSessionState: "refreshing" });
  }
  _refreshInFlight = (async () => {
    const bridge = typeof window !== "undefined" ? (window as any).hashmmDesktop : null;
    if (bridge?.authSessionRefresh) {
      const startToken = useStore.getState().token;
      const startUser = String(
        useStore.getState().user?.id || useStore.getState().user?.username || "",
      );
      try {
        const result = await bridge.authSessionRefresh();
        const currentUser = String(
          useStore.getState().user?.id || useStore.getState().user?.username || "",
        );
        if (useStore.getState().token !== startToken || currentUser !== startUser) {
          return useStore.getState().token;
        }
        if (result?.ok && result.status === "accepted" && result.token) {
          setTokens(String(result.token));
          return String(result.token);
        }
        return result?.status === "unavailable" ? REFRESH_UNAVAILABLE : null;
      } catch {
        return REFRESH_UNAVAILABLE;
      }
    }
    const rt = useStore.getState().refreshToken;
    if (!rt) return null;
    // The identity provider is authoritative for refresh-token expiry. A
    // renderer-side age window used to reject otherwise valid persisted
    // sessions and caused the login modal to reopen after a successful login.
    // Keep the local timestamp for diagnostics only; never use it as auth proof.
    const sessionMoved = () => useStore.getState().refreshToken !== rt;
    const commit = (token: string, refreshToken?: string): string => {
      // A password login may finish while an older silent refresh is in flight.
      // Never let that stale response overwrite the newly authenticated account.
      if (sessionMoved()) return useStore.getState().token || token;
      setTokens(token, refreshToken);
      return token;
    };
    let _reached = false;   // V265: 是否真正够到过服务器（用来区分"离线"与"被拒绝"）
    let backendStatus = 0;
    // ① 先问后端（HashMM 自带账号 / 后端已配 Supabase 的情形）
    try {
      const r = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      // V269: Electron 桌面端后端不可达时，内置壳层会代回 502/503——那是壳层在说话，
      // 不是后端。此前这里一律记"可达"，本地账号在离线时会被误判成"真失效"边缘。
      _reached = r.status !== 502 && r.status !== 503;
      backendStatus = r.status;
      if (r.ok) {
        const data = await r.json();
        if (data?.token) {
          return commit(data.token as string, data.refresh_token);
        }
      }
    } catch { /* 后端不可达 → 走 ② */ }
    if (sessionMoved()) return useStore.getState().token;
    // A reachable current backend is authoritative for the session it serves.
    // Retrying the same refresh token directly against Supabase after the
    // backend explicitly returned 400/401 used to rotate the token anyway.
    // Every token rotation re-armed App effects and Electron runner presence,
    // whose requests were rejected by the misconfigured backend, producing an
    // unbounded refresh -> token update -> 401 loop and repeated login modals.
    if (_reached && (backendStatus === 400 || backendStatus === 401 || backendStatus === 403 || backendStatus === 422)) {
      return null;
    }
    if (_reached && (backendStatus === 429 || backendStatus >= 500)) {
      return REFRESH_UNAVAILABLE;
    }

    // ② Only when the backend is genuinely unavailable (or is an old build
    // without the refresh route) may the desktop renew directly with Supabase.
    // This keeps offline sessions usable without allowing two reachable
    // authorities to consume the same rotating refresh credential.
    const legacyBackend = _reached && [404, 405, 501].includes(backendStatus);
    if (_reached && !legacyBackend) return null;
    try {
      const { refreshSupabaseTokenDetailed } = await import("./supabase");
      const s = await refreshSupabaseTokenDetailed(rt);
      if (s.status === "ok") {
        _reached = true;
        return commit(s.access_token, s.refresh_token);
      }
      if (s.status === "unavailable") return REFRESH_UNAVAILABLE;
      if (s.status === "rejected") return null;
    } catch { /* fall through */ }
    if (sessionMoved()) return useStore.getState().token;
    // V265: 两条路都没够到服务器（纯网络问题，后端还没起来 / AutoDL 未连）→
    // 返回特殊标记 OFFLINE，让调用方保留登录态、别登出（后端一起来就能继续用）。
    if (!_reached) return REFRESH_UNAVAILABLE;
    return null;
  })();
  try {
    const result = await _refreshInFlight;
    const current = useStore.getState();
    if (current.token && current.authSessionState !== "reauth-required") {
      current.set({
        authSessionState: result === REFRESH_UNAVAILABLE
          ? "offline-valid"
          : "authenticated",
      });
    }
    return result;
  } finally { _refreshInFlight = null; }
}

export type BackendSessionCompatibility = "accepted" | "rejected" | "unavailable";
export type BackendIdentityCode =
  | "ok"
  | "missing-token"
  | "identity-contract-unavailable"
  | "identity-contract-invalid"
  | "project-mismatch"
  | "backend-rejected"
  | "backend-unavailable";

export type BackendSessionDiagnostic = {
  status: BackendSessionCompatibility;
  code: BackendIdentityCode;
  backendUrl?: string;
  projectRef?: string;
  tokenProjectRef?: string;
  requestId?: string;
};

function tokenProjectRef(token: string): string {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    const host = new URL(String(payload.iss || "")).hostname.toLowerCase();
    return host.endsWith(".supabase.co") ? host.split(".")[0] : "";
  } catch {
    return "";
  }
}

export async function diagnoseBackendSession(token: string): Promise<BackendSessionDiagnostic> {
  if (!token) return { status: "rejected", code: "missing-token" };
  let contract: Record<string, unknown> | null = null;
  let contractRequestId: string | undefined;
  let contractUnavailable = false;
  try {
    const response = await fetch("/api/auth/identity-contract", { cache: "no-store" });
    contractRequestId = response.headers.get("x-request-id") || undefined;
    if (!response.ok) {
      contractUnavailable = true;
    } else {
      contract = await response.json() as Record<string, unknown>;
    }
  } catch {
    contractUnavailable = true;
  }
  const backendUrl = String(contract?.backend_url || "");
  const projectRef = String(contract?.project_ref || "");
  const tokenRef = tokenProjectRef(token);
  const contractValid = contract?.schema === "hashmm.identity-contract.v1"
    && contract?.provider === "supabase"
    && contract?.provider_ready === true;
  // An explicit project mismatch is actionable and must fail before the token
  // is sent any further. A missing/partially rolled-out diagnostic endpoint is
  // not authentication evidence, however: /auth/me remains the authoritative
  // verifier. Rejecting a session solely on optional diagnostic metadata made
  // valid production accounts loop on the login modal during upgrades.
  if (projectRef && tokenRef && projectRef !== tokenRef) {
    return { status: "rejected", code: "project-mismatch", backendUrl, projectRef, tokenProjectRef: tokenRef, requestId: contractRequestId };
  }
  try {
    const response = await fetch("/api/auth/me", {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    const requestId = response.headers.get("x-request-id") || undefined;
    if (response.ok) return { status: "accepted", code: "ok", backendUrl, projectRef, tokenProjectRef: tokenRef, requestId };
    if (response.status === 401 || response.status === 403) {
      return { status: "rejected", code: "backend-rejected", backendUrl, projectRef, tokenProjectRef: tokenRef, requestId };
    }
    if (!contractValid && !contractUnavailable) {
      return { status: "rejected", code: "identity-contract-invalid", backendUrl, projectRef, tokenProjectRef: tokenRef, requestId: requestId || contractRequestId };
    }
    return { status: "unavailable", code: contractUnavailable ? "identity-contract-unavailable" : "backend-unavailable", backendUrl, projectRef, tokenProjectRef: tokenRef, requestId: requestId || contractRequestId };
  } catch {
    if (!contractValid && !contractUnavailable) {
      return { status: "rejected", code: "identity-contract-invalid", backendUrl, projectRef, tokenProjectRef: tokenRef, requestId: contractRequestId };
    }
    return { status: "unavailable", code: contractUnavailable ? "identity-contract-unavailable" : "backend-unavailable", backendUrl, projectRef, tokenProjectRef: tokenRef, requestId: contractRequestId };
  }
}

/**
 * Verify that an online HashMM backend accepts the identity-provider session
 * before committing a password login to global desktop state.
 *
 * Direct Supabase login is still supported while the backend is offline.  A
 * reachable backend returning 401, however, means its identity configuration
 * is missing/mismatched; accepting that session would immediately start a 401
 * refresh storm and reopen the login modal.
 */
export async function validateBackendSession(token: string): Promise<BackendSessionCompatibility> {
  return (await diagnoseBackendSession(token)).status;
}

/** Refresh proactively if the access token expires within `thresholdSec` seconds.
 *  V298：加阈值参数——桌面端保活（需要更早续、600s 窗）也走这条**同一单飞**路径，
 *  绝不再各自直连 Supabase 刷新。Supabase 刷新令牌是一次性轮换的：两处并发用同一个 rt 刷新，
 *  必有一个用到已被消费的 rt → 401 → 全局登出 → "刚登录又叫我登录"。统一单飞即根治。 */
export async function ensureFreshToken(thresholdSec = 60): Promise<void> {
  const token = useStore.getState().token;
  if (!token) return;
  const exp = _decodeExp(token);
  if (exp !== null && exp - Math.floor(Date.now() / 1000) < thresholdSec) {
    await _doRefresh();
  }
}

/**
 * Rebind a request that was created with an Authorization header to the
 * current session token. API call sites commonly build headers before
 * `_fetch()` performs proactive refresh; without rebinding, the first request
 * after refresh still sent the consumed access token and immediately entered
 * a second refresh/logout cycle.
 *
 * Anonymous requests stay anonymous, so login/register endpoints never inherit
 * a bearer token accidentally.
 */
export function bindRequestToCurrentSession(
  init: RequestInit | undefined,
  token: string | null,
): RequestInit | undefined {
  if (!init?.headers) return init;
  if (!(init.headers instanceof Headers) && !Array.isArray(init.headers)) {
    const source = init.headers as Record<string, string>;
    const authKey = Object.keys(source).find(key => key.toLowerCase() === "authorization");
    if (!authKey) return init;
    const headers = { ...source };
    if (token) headers[authKey] = `Bearer ${token}`;
    else delete headers[authKey];
    return { ...init, headers };
  }
  const rebound = new Headers(init.headers);
  if (!rebound.has("Authorization")) return init;
  if (token) rebound.set("Authorization", `Bearer ${token}`);
  else rebound.delete("Authorization");
  return { ...init, headers: rebound };
}

// v12: Request dedup cache — prevents duplicate GET requests within 2s window
const _dedupCache = new Map<string, { promise: Promise<unknown>; ts: number }>();
const DEDUP_TTL = 2000; // 2 seconds

async function fetchWithPolicy(url: string, init: RequestInit | undefined, method: string): Promise<Response> {
  const maxAttempts = method === "GET" ? 2 : 1;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const controller = new AbortController();
    const upstream = init?.signal;
    const relayAbort = () => controller.abort(upstream?.reason);
    if (upstream?.aborted) relayAbort();
    else upstream?.addEventListener("abort", relayAbort, { once: true });
    const timer = setTimeout(() => controller.abort(new Error("request deadline exceeded")), method === "GET" ? 15_000 : 60_000);
    try {
      const response = await fetch(url, { ...(init || {}), signal: controller.signal });
      const retryable = [429, 502, 503, 504].includes(response.status);
      if (!retryable || attempt + 1 >= maxAttempts) return response;
    } catch (error) {
      if (attempt + 1 >= maxAttempts || upstream?.aborted) throw error;
    } finally {
      clearTimeout(timer);
      upstream?.removeEventListener("abort", relayAbort);
    }
    await new Promise(resolve => setTimeout(resolve, 150 * (2 ** attempt) + Math.floor(Math.random() * 100)));
  }
  throw new Error("request policy exhausted");
}

async function _fetch(url: string, init?: RequestInit) {
  // Proactively renew a near-expired access token before sending.
  if (useStore.getState().token) await ensureFreshToken();
  const requestInit = bindRequestToCurrentSession(init, useStore.getState().token);

  // GET requests: dedup within TTL window
  const method = requestInit?.method?.toUpperCase() || "GET";
  // Authentication is part of the request identity. URL-only dedup could
  // otherwise return user A's in-flight GET to user B after a fast account
  // switch in the same renderer process.
  const requestAuth = new Headers(requestInit?.headers).get("Authorization") || "anon";
  const cacheKey = method === "GET" ? `${requestAuth}|${url}` : "";
  if (cacheKey) {
    const cached = _dedupCache.get(cacheKey);
    if (cached && Date.now() - cached.ts < DEDUP_TTL) {
      return cached.promise;
    }
  }

  const promise = (async () => {
    let r: Response;
    try {
      r = await fetchWithPolicy(url, requestInit, method);
    } catch {
      // V265: 后端整个不可达（关机 / AutoDL 未连 / 网络断）——这是网络错误，
      // 绝不是登录失效。保留登录态，抛可读错误让调用方（卡片/页面）自行重试，
      // 不触发 logout。此前这里会一路走到登出，导致"后端关着点卡片被反复要求登录"。
      throw _offlineError();   // V269: 同时点亮全局"离线模式"（横幅 + 自动重探）
    }
    // V269: Electron 桌面端后端不可达时壳层代回 502/503 —— 语义上等同网络错。
    if (r.status === 502 || r.status === 503) throw _offlineError();
    _reportBackendSuccess();
    if (r.status === 401) {
      // V93: 游客态（本次请求根本没带 token）的 401 是正常态——静默失败，
      // 绝不触发 refresh/logout（否则游客浏览期会被 401 风暴反复打断）。
      const hadAuth = new Headers(requestInit?.headers).has("Authorization");
      if (!hadAuth) throw new Error("未登录");
      // Reactive refresh + single retry before giving up.
      const nt = await _doRefresh();
      // V265: 刷新时后端离线 → 保留登录态，抛可重试错误，绝不登出。
      if (nt === REFRESH_UNAVAILABLE) throw new Error("身份续期暂时不可用，已保留登录状态，请稍后重试");
      if (nt) {
        const retryInit = bindRequestToCurrentSession(requestInit, nt) as RequestInit;
        try {
          r = await fetch(url, retryInit);
        } catch {
          throw _offlineError();
        }
        if (r.status === 502 || r.status === 503) throw _offlineError();
        _reportBackendSuccess();
      }
      if (r.status === 401) {
        // 只有"够到了服务器、带着有效刷新也换不出新 token"才是真失效 → 登出。
        // V270 保险丝：全局已判离线时收到的"401 死刑"一律不采信（可能是中间层伪 401）——
        // 离线场景绝不登出，宁可让这次请求以离线错误失败。
        if (useStore.getState().backendOnline === false) throw _offlineError();
        useStore.getState().requireReauth();
        throw new Error("登录已过期，请重新登录");
      }
    }
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      // V269: 错误带上 HTTP 状态码——会话加载等调用方据此区分"真 404（该删）"与
      // "连不上（绝不能删本地历史）"。
      const err = new Error(body?.error?.message || body?.message || body?.detail || `HTTP ${r.status}`) as Error & { status?: number; code?: string };
      err.status = r.status;
      err.code = body?.error?.code;
      throw err;
    }
    _setBackendOnline(true);   // V269: 任一真实后端应答成功 → 在线（含离线恢复的自动重同步）
    const data = await r.json();
    // V103.91 写操作(非 GET)成功后清空 GET 去重缓存：否则"刚 POST 新增/修改，紧接着重新拉列表"
    // 会命中 2 秒内的旧缓存 → 看不到刚加的内容（记忆中心/定时任务等"加了刷新没东西"的根因）。
    if (method !== "GET") _dedupCache.clear();
    return data;
  })();

  if (cacheKey) {
    _dedupCache.set(cacheKey, { promise, ts: Date.now() });
    // Auto-cleanup
    setTimeout(() => _dedupCache.delete(cacheKey), DEDUP_TTL + 100);
  }

  return promise;
}

/** Server-side logout: revoke all tokens, then clear local state. */
export async function logout() {
  const token = useStore.getState().token;
  try {
    if (token) {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      });
    }
  } catch { /* best effort — still clear locally */ }
  useStore.getState().logout();
}

// ── Auth ──
export async function login(username: string, password: string) {
  return _fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
}
export async function register(username: string, password: string, display_name: string = "") {
  return _fetch("/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password, display_name }) });
}

export async function changeMyPassword(newPassword: string): Promise<{ ok: true; token: string; refresh_token?: string }> {
  const result = await _fetch("/api/auth/password", {
    method: "POST", headers: headers(), body: JSON.stringify({ new_password: newPassword }),
  }) as { ok: true; token: string; refresh_token?: string };
  // The server revokes every previous session before returning this pair.  The
  // current desktop must commit the replacement atomically or it logs itself
  // out immediately after a successful password change.
  setTokens(result.token, result.refresh_token);
  return result;
}

export type MyProfile = {
  id: string; username: string; display_name: string; role: string;
  identity_source: "local" | "supabase";
};
export async function getMyAccountProfile(): Promise<MyProfile> {
  return _fetch("/api/me/profile", { headers: headers() });
}
export async function updateMyAccountProfile(displayName: string): Promise<{ ok: true; profile: { id: string; display_name: string } }> {
  return _fetch("/api/me/profile", {
    method: "PATCH", headers: headers(), body: JSON.stringify({ display_name: displayName }),
  });
}

export type AccountSetting = {
  key: string; scope: "user"; desired_value?: unknown; effective_value: unknown; source: string;
  editable: boolean; managed_by: string; revision: number; updated_at: number;
  restart_required: boolean; availability?: string; description: string;
};
export type AccountSettingsResponse = { schema: "hashmm.user-settings.v2"; settings: AccountSetting[] };
export async function listMyAccountSettings(): Promise<AccountSettingsResponse> {
  return _fetch("/api/me/settings", { headers: headers() });
}
export async function updateMyAccountSettings(changes: Array<{ key: string; value: unknown; revision: number }>): Promise<
  AccountSettingsResponse & { ok: true; receipt: { id: string; status: "applied"; keys: string[] } }
> {
  return _fetch("/api/me/settings", {
    method: "PATCH", headers: headers(), body: JSON.stringify({ changes }),
  });
}

// ── Chat ──
export async function chat(message: string, sid?: string | null, fileContext?: string | null): Promise<ChatResponse> {
  return _fetch("/api/chat", { method: "POST", headers: headers(), body: JSON.stringify({ message, session_id: sid, file_context: fileContext || undefined }) });
}

export type ConversationExecutionResult = {
  ok: boolean;
  status: "done" | "error";
  output: string;
  error?: string | null;
  files?: Array<Record<string, unknown>>;
  metrics?: Record<string, unknown>;
};

/** Explicit, owner-checked code run. Uses the shared refresh/retry path so a
 * long-lived Chat cannot fail merely because its access token rotated. */
export async function executeConversationCode(convId: string, code: string): Promise<ConversationExecutionResult> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/execute`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ code }),
  }) as Promise<ConversationExecutionResult>;
}

/** Record a human decision only. The backend executes nothing here; the next
 * guarded Agent turn atomically consumes the exact approved fingerprint. */
export async function decideToolApproval(
  convId: string,
  requestId: string,
  decision: "approve" | "decline",
): Promise<{ ok: boolean; approval_request: import("./types").ToolApprovalRequest }> {
  return _fetch(
    `/api/conversations/${encodeURIComponent(convId)}/tool-approvals/${encodeURIComponent(requestId)}`,
    { method: "POST", headers: headers(), body: JSON.stringify({ decision }) },
  );
}

export type FeedbackReason =
  | "incorrect" | "unsupported" | "retrieval_miss" | "wrong_tool"
  | "incomplete" | "instruction_miss" | "unsafe" | "too_slow" | "other";

export interface FeedbackCase {
  schema: "hashmm.feedback-case.v1";
  id: string;
  conversation_id: string;
  message_id: string;
  rating: "up" | "down" | "";
  reason_code: FeedbackReason | "";
  reason_label: string;
  comment: string;
  query: string;
  answer: string;
  status: "pending" | "positive" | "withdrawn" | "approved" | "dismissed";
  reference_answer: string;
  eval_case_id: string;
  created_at: number;
  updated_at: number;
}

export async function submitMessageFeedback(
  convId: string,
  messageId: string,
  rating: "up" | "down" | "clear",
  reasonCode: FeedbackReason | "" = "",
  comment = "",
): Promise<{ ok: boolean; feedback_case: FeedbackCase; reason_options: Record<string, string> }> {
  return _fetch(
    `/api/conversations/${encodeURIComponent(convId)}/messages/${encodeURIComponent(messageId)}/review`,
    { method: "POST", headers: headers(), body: JSON.stringify({ rating, reason_code: reasonCode, comment }) },
  );
}

export interface StreamCallbacks {
  /** Renderer-owned cancellation signal. It never replaces the server-side
   * interrupt endpoint; it only aborts the local SSE reader immediately. */
  shouldStop?: () => boolean;
  onCancelled?: () => void;
  onTurnStarted?: (turn: ActiveTurn) => void;
  onTurnState?: (turn: ActiveTurn) => void;
  onSteerApplied?: (data: { entry_id?: string; message_id?: string; status: string }) => void;
  onTrace: (steps: TraceStep[]) => void;
  onPlan?: (plan: { goal: string; steps: { n: number; action: string; acceptance: string }[] }) => void;
  onTaskContract?: (contract: import("./types").TaskContract) => void;
  onToken: (token: string) => void;
  onThinking: (data: { content?: string }) => void;
  onTodo?: (data: {
    items: Array<{ text: string; status: "pending" | "doing" | "done" }>;
    manifest_id?: string;
    revision?: number;
  }) => void;  // V701: versioned task manifest; stale snapshots must not replace newer progress
  onDelta?: (data: { t: string }) => void;            // V54: 真·流式直播增量（预览）
  onDeltaCommit?: (data: { as: "narrate" | "answer" }) => void;  // V54: 直播段归属裁决
  onFileDelta?: (data: { filename: string; t: string }) => void;  // V55: 右栏逐字写代码
  onCtx?: (data: { chars: number; budget: number }) => void;      // V55: 上下文用量表
  onStepStart: (step: { tool: string; detail: string; id?: string; args?: Record<string, unknown> }) => void;
  onStepDone: (step: { tool: string; status: string; detail: string; duration_ms?: number; id?: string; hooks?: import("./types").HookRun[]; receipt?: import("./types").ExecutionReceipt }) => void;
  onFile: (file: FileInfo) => void;
  onIteration: (data: { current: number; max: number }) => void;
  onProgress?: (data: { stage: string; pct: number; msg: string }) => void;
  onSources?: (sources: Source[]) => void;  // v9.0: pre-sent sources before streaming
  onClarify?: (data: { question: string; options: string[] }) => void;  // V103.27: 主动澄清可点选项
  onInputRequest?: (data: import("./types").InputRequest) => void;
  onApprovalRequest?: (data: import("./types").ToolApprovalRequest) => void;
  onOrchestration?: (data: { strategy?: string; members: Array<{ id: string; step?: number; role_label?: string; task?: string }> }) => void;  // V103.30: 子 agent 编排 DAG
  onSubagent?: (data: { id: string; status: string; step?: number; total?: number; description?: string; elapsed_ms?: number; preview?: string }) => void;  // V103.30: 子 agent 实时状态
  onDone: (data: StreamDoneData) => void;
  onError: (error: string) => void;
}

export interface ActiveTurn {
  turn_id: string;
  conversation_id: string;
  goal: string;
  started_at: number;
  status: "running" | "interrupting";
  mode: string;
  steerable: boolean;
  pending_steers: number;
}

export async function getActiveTurn(convId: string): Promise<{ active: boolean; turn: ActiveTurn | null }> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/active-turn`, { headers: headers() });
}

export async function steerActiveTurn(
  convId: string,
  turnId: string,
  content: string,
  clientMessageId: string,
  attachments: Array<{ filename: string; size: number; sha256: string; download_url: string }> = [],
): Promise<{ accepted: boolean; duplicate: boolean; turn_id: string; message_id: string; queued_at?: number }> {
  return _fetch(
    `/api/conversations/${encodeURIComponent(convId)}/turns/${encodeURIComponent(turnId)}/steer`,
    {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        content,
        client_message_id: clientMessageId,
        ...(attachments.length ? { attachments } : {}),
      }),
    },
  );
}

export async function interruptActiveTurn(
  convId: string, turnId: string,
): Promise<{ accepted: boolean; turn_id: string; status: string }> {
  return _fetch(
    `/api/conversations/${encodeURIComponent(convId)}/turns/${encodeURIComponent(turnId)}/interrupt`,
    { method: "POST", headers: headers() },
  );
}

export async function chatStream(message: string, sid: string | null, fileContext: string | null, cb: StreamCallbacks) {
  const MAX_RETRIES = 3;
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ message, session_id: sid, file_context: fileContext || undefined, custom_prompt: useStore.getState().customPrompt || undefined }),
      });
      if (!res.ok) {
        if (res.status === 502 || res.status === 503) {   // V269: 壳层代离线 → 不重试、友好提示
          _setBackendOnline(false);
          cb.onError("后端未连接，请确认服务已启动后重试"); return;
        }
        if (res.status === 401) {
          // 先静默续期再重试，只有续期也失败（refresh token 真失效）才登出——避免动不动就要求重新登录。
          const nt = await _doRefresh();
          if (nt === REFRESH_UNAVAILABLE) { cb.onError("身份续期暂时不可用，登录状态已保留，请稍后重试"); return; }
          if (nt) { cb.onError("登录已自动刷新，请重新发送"); return; }
          if (useStore.getState().backendOnline === false) { cb.onError("后端未连接，请确认服务已启动后重试"); return; }   // V270 保险丝
        useStore.getState().requireReauth(); cb.onError("登录已过期"); return;
        }
        if (attempt < MAX_RETRIES) { await new Promise(r => setTimeout(r, 1000 * attempt)); continue; }
        cb.onError(`HTTP ${res.status}`);
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) { cb.onError("No stream reader"); return; }

      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const event of events) {
          if (!event.trim()) continue;
          const lines = event.split("\n");
          let eventType = "", data = "";
          for (const line of lines) {
            if (line.startsWith("event: ")) eventType = line.slice(7);
            else if (line.startsWith("data: ")) data = line.slice(6);
          }
          if (!data) continue;
          try {
            const parsed = JSON.parse(data);
            if (eventType === "turn_started") cb.onTurnStarted?.(parsed);
            else if (eventType === "turn_state") cb.onTurnState?.(parsed);
            else if (eventType === "steer_applied") cb.onSteerApplied?.(parsed);
            else if (eventType === "trace") cb.onTrace(parsed.steps || []);
            else if (eventType === "plan") cb.onPlan?.(parsed);
            else if (eventType === "task_contract") cb.onTaskContract?.(parsed);
            else if (eventType === "token") cb.onToken(parsed.content || "");
            else if (eventType === "thinking") cb.onThinking(parsed);
            else if (eventType === "todo") cb.onTodo?.(parsed);
            else if (eventType === "delta") cb.onDelta?.(parsed);
            else if (eventType === "delta_commit") cb.onDeltaCommit?.(parsed);
            else if (eventType === "file_delta") cb.onFileDelta?.(parsed);
            else if (eventType === "ctx") cb.onCtx?.(parsed);
            else if (eventType === "step_start") cb.onStepStart(parsed);
            else if (eventType === "step_done") cb.onStepDone(parsed);
            else if (eventType === "file") cb.onFile(parsed);
            else if (eventType === "iteration") cb.onIteration(parsed);
            else if (eventType === "input_request") cb.onInputRequest?.(parsed);
            else if (eventType === "approval_request") cb.onApprovalRequest?.(parsed);
            else if (eventType === "clarify") cb.onClarify?.(parsed);
            else if (eventType === "orchestration") cb.onOrchestration?.(parsed);
            else if (eventType === "subagent") cb.onSubagent?.(parsed);
            else if (eventType === "done") cb.onDone(parsed);
          } catch {}
        }
      }
      return; // Success — exit retry loop
    } catch (e: unknown) {
      if (attempt < MAX_RETRIES) { await new Promise(r => setTimeout(r, 1000 * attempt)); continue; }
      cb.onError(e instanceof Error ? e.message : "网络错误，请重试");
    }
  }
}

export async function upload(file: File): Promise<{ filename: string; text: string }> {
  const fd = new FormData(); fd.append("file", file);
  const h: Record<string, string> = {};
  const token = useStore.getState().token;
  if (token) h["Authorization"] = `Bearer ${token}`;
  const r = await fetch("/api/upload", { method: "POST", headers: h, body: fd });
  return r.json();
}

export type ConversationResourceSummary = {
  resource_id?: string;
  resource_revision?: number;
  sha256?: string;
  filename: string;
  detected_type?: string;
  parse_state: "ready" | "needs_ocr" | "unsupported" | "encrypted" | "corrupted" | "missing" | "error" | string;
  page_count?: number;
  readable_pages?: number;
  readable_ratio?: number;
  accounted_pages?: number;
  failed_pages?: number[];
  blank_pages?: number[];
  parser_used?: string;
  text_chars?: number;
  warnings?: string[];
};

/** Replace/add a binary file inside an owned conversation workspace. */
export async function uploadConversationFile(convId: string, file: File): Promise<{ ok: boolean; filename: string; size: number; sha256: string; download_url: string; resource?: ConversationResourceSummary }> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  const r = await fetch(`/api/conversations/${encodeURIComponent(convId)}/upload`, {
    method: "POST", headers: authHeaders(), body: fd,
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || body.message || `HTTP ${r.status}`);
  }
  return r.json();
}

/** Owner-scoped upload with real byte progress and cancellation.
 *
 * XHR is intentionally limited to this binary path: fetch upload progress is
 * not portable across the Electron/WebView versions shipped by HashMM.  The
 * response contract remains identical to `uploadConversationFile`.
 */
export function uploadConversationFileWithProgress(
  convId: string,
  file: File,
  onProgress: (uploadedBytes: number, totalBytes: number) => void,
  signal?: AbortSignal,
): Promise<{ ok: boolean; filename: string; size: number; sha256: string; download_url: string; resource?: ConversationResourceSummary }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const url = `/api/conversations/${encodeURIComponent(convId)}/upload`;
    xhr.open("POST", url, true);
    const auth = authHeaders().Authorization;
    if (auth) xhr.setRequestHeader("Authorization", auth);
    xhr.upload.onprogress = event => {
      onProgress(event.loaded, event.lengthComputable ? event.total : file.size);
    };
    xhr.onerror = () => reject(new Error("附件上传网络中断"));
    xhr.ontimeout = () => reject(new Error("附件上传超时"));
    xhr.onabort = () => reject(new DOMException("附件上传已取消", "AbortError"));
    xhr.onload = () => {
      let body: Record<string, unknown> = {};
      try { body = JSON.parse(xhr.responseText || "{}"); } catch { /* handled below */ }
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(String(body.detail || body.message || `HTTP ${xhr.status}`)));
        return;
      }
      onProgress(file.size, file.size);
      resolve(body as unknown as {
        ok: boolean; filename: string; size: number; sha256: string;
        download_url: string; resource?: ConversationResourceSummary;
      });
    };
    const abort = () => xhr.abort();
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener("abort", abort, { once: true });
    const fd = new FormData();
    fd.append("file", file, file.name);
    xhr.send(fd);
  });
}

// ── Data ──
export async function stats(): Promise<Stats> { return _fetch("/api/corpus/stats"); }
export async function getMetrics() { return _fetch("/api/metrics"); }
// 记忆中心（V103.23）：跨会话用户记忆的读取与管理（后端 routes/user_memory.py）。
export async function listMemory(limit = 50) { return _fetch(`/api/memory?limit=${limit}`, { headers: headers() }); }
export async function deleteMemory(id: string) { return _fetch(`/api/memory/${encodeURIComponent(id)}`, { method: "DELETE", headers: headers() }); }
export async function memoryProfile() { return _fetch("/api/memory/profile", { headers: headers() }); }
// 自我进化（V103.23）：经验回放（后端 routes/evolution.py）。技能列表复用项目已有的 listEvolutionSkills。
export async function listEpisodes(limit = 30) { return _fetch(`/api/evolution/episodes?limit=${limit}`, { headers: headers() }); }
// 质量看板（V103.23）：可观测聚合快照（后端 observability.dashboard_snapshot）+ KG 规模。
export async function metricsDashboard() { return _fetch("/api/metrics/dashboard", { headers: headers() }); }
export async function kgStats() { return _fetch("/api/kg/stats", { headers: headers() }); }
// 权限审计（V103.25）：工具调用审计流 + deny-first 治理（后端 admin.py，仅管理员）。
export async function toolAudit(limit = 50) { return _fetch(`/api/admin/audit/tools?limit=${limit}`, { headers: headers() }); }
export async function governanceStatus() { return _fetch("/api/admin/governance", { headers: headers() }); }
// 定时任务/主动服务（V103.27）：列出/运行/启停（后端 admin.py，仅管理员）。
export async function listScheduled() { return _fetch("/api/admin/scheduled", { headers: headers() }); }
export async function runScheduled(id: string) { return _fetch(`/api/admin/scheduled/${encodeURIComponent(id)}/run`, { method: "POST", headers: headers() }); }
export async function toggleScheduled(id: string, enabled: boolean) { return _fetch(`/api/admin/scheduled/${encodeURIComponent(id)}/toggle`, { method: "POST", headers: headers(), body: JSON.stringify({ enabled }) }); }
export async function deleteScheduled(id: string) { return _fetch(`/api/admin/scheduled/${encodeURIComponent(id)}`, { method: "DELETE", headers: headers() }); }
// 模型路由/角色→后端（V103.28）：读取/保存任务→本地·云端 路由表（仅管理员）。
export async function getLlmRouting() { return _fetch("/api/admin/llm-routing", { headers: headers() }); }
export async function testLlmRouting(task: string) { return _fetch("/api/admin/llm-routing/test", { method: "POST", headers: headers(), body: JSON.stringify({ task }) }); }
export async function saveLlmRouting(routing: Record<string, string>) { return _fetch("/api/admin/llm-routing", { method: "PUT", headers: headers(), body: JSON.stringify({ routing }) }); }
// Agent 运行轨迹（V103.29）：读取最近 run_record 遥测（仅管理员）。
export async function listRuns(limit = 50) { return _fetch(`/api/admin/runs?limit=${limit}`, { headers: headers() }); }
// 当前账号的持久化运行轨迹：来自 Chat message.run_manifest，普通用户可读自己的。
export async function listMyRuns(limit = 50) { return _fetch(`/api/runs/mine?limit=${limit}`, { headers: headers() }); }
// 自发现（V103.31）：按需跑一次主动扫描（仅管理员）。
export async function runDiscovery() { return _fetch("/api/admin/discovery", { headers: headers() }); }
// 发现 → 一键处理（V103.33，loop 闭环 handoff）：构建社区 / 创建定时任务。
export async function rebuildCommunities() { return _fetch("/api/kg/rebuild-communities", { method: "POST", headers: headers() }); }
export async function createScheduledTask(body: Record<string, unknown>) { return _fetch("/api/admin/scheduled", { method: "POST", headers: headers(), body: JSON.stringify(body) }); }
// V103.34：手动写记忆（教它记住）。重建索引复用既有 rebuildIndex。
export async function addMemory(body: { category?: string; key: string; value: string }) { return _fetch("/api/memory", { method: "POST", headers: headers(), body: JSON.stringify(body) }); }
// V254 断链修复：此前打到 /api/sessions/{id}——后端从未有过这个路由（404），
// 侧栏删除只清了本地、服务端会话还在，刷新即"复活"。真实端点一直是 /api/conversations/{id}。
export async function deleteSession(sid: string) { return _fetch(`/api/conversations/${sid}`, { method: "DELETE", headers: headers() }); }
/* ── V273 CC 式回退：对话截断 + 会话工作区文件按快照还原（资料 13.3.5） ── */
export async function rewindConversation(convId: string, assistantIndex: number):
  Promise<{ ok: boolean; removed: number; files_restored: boolean; note?: string }> {
  return _fetch(`/api/conversations/${convId}/rewind`, {
    method: "POST", headers: headers(), body: JSON.stringify({ assistant_index: assistantIndex }),
  });
}

export async function archiveConversation(convId: string, archived: boolean) {   // V239 归档/还原（复用 PATCH 透传）
  return _fetch(`/api/conversations/${convId}`, { method: "PATCH", headers: headers(), body: JSON.stringify({ archived: archived ? 1 : 0 }) });
}
export async function listArchivedConversations(): Promise<{ conversations: { id: string; title: string; created_at?: number; updated_at?: number }[] }> {
  return _fetch(`/api/conversations?archived=1`, { headers: headers() });
}

/** 把旧身份（默认 anonymous）名下的对话过户到当前登录账号。返回 {moved}。 */
export async function migrateConversations(from = "anonymous") {
  return _fetch(`/api/conversations/migrate`, { method: "POST", headers: headers(), body: JSON.stringify({ from }) });
}
export async function migratePreview() {
  return _fetch(`/api/conversations/migrate/preview`, { headers: headers() });
}

// ── Admin: Users ──
export async function listUsers(): Promise<User[]> { return _fetch("/api/admin/users", { headers: headers() }); }
export async function createUser(username: string, password: string, display_name: string) {
  return _fetch("/api/admin/users", { method: "POST", headers: headers(), body: JSON.stringify({ username, password, display_name }) });
}
export async function updateUser(id: string, data: Partial<User>) {
  return _fetch(`/api/admin/users/${id}`, { method: "PUT", headers: headers(), body: JSON.stringify(data) });
}
export async function deleteUser(id: string) {
  return _fetch(`/api/admin/users/${id}`, { method: "DELETE", headers: headers() });
}
export async function forceLogoutUser(id: string) {
  // v17 Phase 68: revoke all of a user's tokens (admin force-logout).
  return _fetch(`/api/admin/users/${id}/force-logout`, { method: "POST", headers: headers() });
}

// ── Admin: Models ──
export async function listModels(): Promise<ModelConfig[]> {
  const { normalizeModelList } = await import("./modelApiBoundary");
  return normalizeModelList(await _fetch("/api/admin/models", { headers: headers() }));
}
export async function createModel(data: Partial<ModelConfig>) {
  return _fetch("/api/admin/models", { method: "POST", headers: headers(), body: JSON.stringify(data) });
}
export async function updateModel(id: string, data: Partial<ModelConfig>) {
  return _fetch(`/api/admin/models/${id}`, { method: "PUT", headers: headers(), body: JSON.stringify(data) });
}
export async function deleteModel(id: string) {
  return _fetch(`/api/admin/models/${id}`, { method: "DELETE", headers: headers() });
}
export async function setDefaultModel(id: string) {
  return _fetch(`/api/admin/models/${id}/default`, { method: "POST", headers: headers() });
}
export async function testModel(data: Partial<ModelConfig>) {
  return _fetch("/api/admin/models/test", { method: "POST", headers: headers(), body: JSON.stringify(data) });
}
export async function discoverProviderModels(data: Partial<ModelConfig>): Promise<{
  ok: boolean; supported: boolean; provider: string; models: string[];
  count?: number; message: string; code?: string;
}> {
  return _fetch("/api/admin/models/discover", { method: "POST", headers: headers(), body: JSON.stringify(data) });
}
export async function getModelPresets() { return _fetch("/api/admin/models/presets", { headers: headers() }); }
export async function getModelProviders(): Promise<{ providers: import("./types").ProviderSpec[] }> {
  const { normalizeProviderResponse } = await import("./modelApiBoundary");
  return normalizeProviderResponse(await _fetch("/api/admin/models/providers", { headers: headers() }));
}

// ── Admin: KBs ──
export async function listKBs(): Promise<KB[]> { return _fetch("/api/admin/kbs", { headers: headers() }); }
export async function createKB(data: Partial<KB>) {
  return _fetch("/api/admin/kbs", { method: "POST", headers: headers(), body: JSON.stringify(data) });
}
export async function deleteKB(id: string) {
  return _fetch(`/api/admin/kbs/${id}`, { method: "DELETE", headers: headers() });
}

// ── Admin: Logs ──
export async function getAuditLogs(limit = 100, offset = 0): Promise<{ logs: AuditLog[]; total: number }> {
  return _fetch(`/api/admin/logs?limit=${limit}&offset=${offset}`, { headers: headers() });
}

// v15 Phase 9: filtered audit query + CSV export + job monitoring
export interface AuditFilter {
  user_id?: string; action?: string; username?: string;
  start_ts?: number; end_ts?: number; limit?: number; offset?: number;
}
export async function queryAuditLogs(f: AuditFilter): Promise<{ logs: AuditLog[]; total: number }> {
  const qs = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => { if (v !== undefined && v !== "" && v !== 0) qs.set(k, String(v)); });
  return _fetch(`/api/admin/audit/query?${qs.toString()}`, { headers: headers() });
}
export function auditExportUrl(f: AuditFilter): string {
  const qs = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => { if (v !== undefined && v !== "" && v !== 0) qs.set(k, String(v)); });
  return `/api/admin/audit/export?${qs.toString()}`;
}
export async function rebuildIndex() {
  return _fetch("/api/admin/index/rebuild", { method: "POST", headers: headers() });
}
export async function batchIndex(doc_ids?: string[]) {
  return _fetch("/api/admin/docs/batch-index", { method: "POST", headers: headers(),
    body: JSON.stringify({ doc_ids: doc_ids || [] }) });
}
export async function listJobs() { return _fetch("/api/admin/jobs", { headers: headers() }); }
export async function getJob(id: string) { return _fetch(`/api/admin/jobs/${id}`, { headers: headers() }); }

// v15 Phase 8/10: eval panel + usage dashboard
export async function runEvalEnhanced(tag: string, use_judge = true, judge_model = "", comprehensive = false, case_set = "") {
  return _fetch("/api/admin/eval/run-enhanced", { method: "POST", headers: headers(),
    body: JSON.stringify({ tag, use_judge, judge_model, comprehensive, case_set }) });
}
export async function listEvalRuns() { return _fetch("/api/admin/eval/runs", { headers: headers() }); }
export async function listFeedbackCandidates(status = "pending", limit = 100): Promise<{
  cases: FeedbackCase[]; count: number; reason_options: Record<string, string>; policy: string;
}> {
  return _fetch(`/api/admin/eval/feedback-candidates?status=${encodeURIComponent(status)}&limit=${limit}`, { headers: headers() });
}
export async function reviewFeedbackCandidate(candidateId: string, decision: "approve" | "dismiss", referenceAnswer = "") {
  return _fetch(`/api/admin/eval/feedback-candidates/${encodeURIComponent(candidateId)}`, {
    method: "POST", headers: headers(), body: JSON.stringify({ decision, reference_answer: referenceAnswer }),
  });
}
export async function runIrEval(ks: number[] = [1, 3, 5, 10]): Promise<{ n: number; summary: Record<string, number>; per_case: any[] }> {
  return _fetch("/api/admin/eval/ir", { method: "POST", headers: headers(), body: JSON.stringify({ ks }) });
}
export async function runRedteam(): Promise<{ all_passed: boolean; verdict: string; isolation: { passed: boolean; critical_tools_covered: number; critical_tools_total: number }; exfil_guard: { passed: boolean; accuracy: number; true_positive: number; false_negative: number; false_positive: number } }> {
  return _fetch("/api/admin/eval/redteam", { method: "POST", headers: headers() });
}
export async function compareEvalRuns(baseline: string, candidate: string) {
  return _fetch(`/api/admin/eval/compare?baseline=${baseline}&candidate=${candidate}`, { headers: headers() });
}
export async function getUsageSummary(days = 30) {
  return _fetch(`/api/admin/usage/summary?days=${days}`, { headers: headers() });
}

// V820: self-service public API access plane. The full secret exists only in
// PlatformApiKeyCreated and must never be persisted by callers.
export interface PlatformApiKey {
  id: string;
  object: "api_key";
  name: string;
  prefix: string;
  status: "active" | "disabled" | "revoked";
  scopes: string[];
  allowed_models: string[];
  allowed_projects: string[];
  ip_allowlist: string[];
  ip_denylist: string[];
  quota_limit: number | null;
  quota_used: number;
  rpm_limit: number | null;
  concurrent_limit: number | null;
  expires_at: number | null;
  last_used_at: number;
  revision: number;
  created_at: number;
  updated_at: number;
  revoked_at: number;
}

export interface PlatformApiKeyCreated extends PlatformApiKey {
  secret: string;
}

export interface PlatformApiKeyInput {
  name: string;
  scopes: string[];
  allowed_models?: string[];
  allowed_projects?: string[];
  ip_allowlist?: string[];
  ip_denylist?: string[];
  quota_limit?: number | null;
  rpm_limit?: number | null;
  concurrent_limit?: number | null;
  expires_at?: number | null;
}

export async function listPlatformApiKeys(): Promise<PlatformApiKey[]> {
  const value = await _fetch("/api/platform/keys", { headers: headers() }) as { data?: PlatformApiKey[] };
  return Array.isArray(value?.data) ? value.data : [];
}

export async function createPlatformApiKey(input: PlatformApiKeyInput): Promise<PlatformApiKeyCreated> {
  return _fetch("/api/platform/keys", {
    method: "POST", headers: headers(), body: JSON.stringify(input),
  });
}

export async function updatePlatformApiKey(
  id: string,
  revision: number,
  input: Partial<PlatformApiKeyInput> & { status?: "active" | "disabled" },
): Promise<PlatformApiKey> {
  return _fetch(`/api/platform/keys/${encodeURIComponent(id)}`, {
    method: "PATCH", headers: headers(), body: JSON.stringify({ ...input, revision }),
  });
}

export async function revokePlatformApiKey(id: string): Promise<PlatformApiKey> {
  return _fetch(`/api/platform/keys/${encodeURIComponent(id)}`, {
    method: "DELETE", headers: headers(),
  });
}

export async function getPlatformApiKeyUsage(id: string, days = 30): Promise<{
  object: "api_key_usage";
  key: PlatformApiKey;
  days: number;
  totals: { events: number; quantity: number; estimated_cost: number; provider_reported_cost: number };
  data: Array<{ model: string; service: string; unit: string; events: number; quantity: number; estimated_cost: number }>;
}> {
  return _fetch(`/api/platform/keys/${encodeURIComponent(id)}/usage?days=${days}`, { headers: headers() });
}
// V103.90 查当前默认模型所属提供商的 API 余额（目前支持 DeepSeek）
export async function getProviderBalance() {
  return _fetch("/api/admin/provider-balance", { headers: headers() });
}
export async function getQualityDashboard(days = 7) {
  return _fetch(`/api/admin/quality/dashboard?days=${days}`, { headers: headers() });
}


// ── Title generation ──
export async function generateTitle(query: string, answer: string): Promise<string> {
  try { const r = await _fetch("/api/title", { method: "POST", headers: headers(), body: JSON.stringify({ query, answer }) }); return r.title || query.slice(0, 30); } catch { return query.slice(0, 30); }
}

// ── Model list ──
export async function quickListModels(): Promise<Array<{ id: string; name: string; model_name: string; is_default: number }>> {
  try {
    const { normalizeModelList } = await import("./modelApiBoundary");
    return normalizeModelList(await _fetch("/api/admin/models", { headers: headers() }));
  } catch { return []; }
}

// ── Admin: Docs ──
export async function listDocs() { return _fetch("/api/admin/docs", { headers: headers() }); }
export async function uploadDoc(file: File) {
  const fd = new FormData(); fd.append("file", file);
  const h: Record<string, string> = {}; const token = useStore.getState().token; if (token) h["Authorization"] = `Bearer ${token}`;
  const r = await fetch("/api/admin/docs/upload", { method: "POST", headers: h, body: fd });
  if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json();
}

// ── Admin: Skills ──
export async function listSkills() { return _fetch("/api/admin/skills", { headers: headers() }); }
export async function createSkill(skill: { name: string; description: string; triggers: string[]; prompt: string; tools: string[] }) {
  return _fetch("/api/admin/skills", { method: "POST", headers: headers(), body: JSON.stringify(skill) });
}
export async function deleteSkill(name: string) {
  return _fetch(`/api/admin/skills/${encodeURIComponent(name)}`, { method: "DELETE", headers: headers() });
}

// ── Workspace ──
export async function listWorkspaceFiles() {
  return _fetch("/api/workspace", { headers: headers() });
}

// ── File Preview (V361: conversation-scoped + persistent conditional cache) ──
export interface FilePreviewData {
  filename: string; ext: string; size: number; language?: string;
  content: string | null; lines?: number; binary?: boolean; download_url: string;
  error?: string; sha256?: string;
  rich?: { kind: string; html?: string; sheets?: { name: string; headers: string[]; rows: (string | number | null)[][]; total_rows?: number }[]; slides?: string[]; pages?: number } | null;
}

type PreviewCacheRecord = { etag: string; data: FilePreviewData; storedAt: number };
const PREVIEW_CACHE = "hashmm-file-previews-v1";
const PREVIEW_UPDATE_EVENT = "hmm-file-preview-updated";
const _previewMemory = new Map<string, PreviewCacheRecord>();

function previewScope(): string {
  const user = useStore.getState().user;
  return user?.id || user?.username || "anonymous";
}

export function previewFileCacheKey(filename: string, convId?: string): string {
  return `${previewScope()}|${convId || "unknown"}|${filename.split("?")[0].split("#")[0]}`;
}

function previewCacheRequest(key: string): Request | null {
  if (typeof window === "undefined") return null;
  return new Request(`${window.location.origin}/__hashmm_file_preview_cache__/${encodeURIComponent(key)}`);
}

async function readPreviewCache(key: string): Promise<PreviewCacheRecord | null> {
  const memory = _previewMemory.get(key);
  if (memory) return memory;
  try {
    if (typeof caches === "undefined") return null;
    const request = previewCacheRequest(key); if (!request) return null;
    const response = await (await caches.open(PREVIEW_CACHE)).match(request);
    if (!response) return null;
    const record = await response.json() as PreviewCacheRecord;
    if (!record?.data || typeof record.data !== "object") return null;
    _previewMemory.set(key, record);
    return record;
  } catch { return null; }
}

async function writePreviewCache(key: string, record: PreviewCacheRecord): Promise<void> {
  _previewMemory.set(key, record);
  try {
    if (typeof caches === "undefined") return;
    const request = previewCacheRequest(key); if (!request) return;
    await (await caches.open(PREVIEW_CACHE)).put(request, new Response(JSON.stringify(record), {
      headers: { "Content-Type": "application/json", "X-HashMM-Preview-Cache": "1" },
    }));
  } catch { /* Cache Storage 不可用时保留进程内缓存 */ }
}

function announcePreviewUpdate(key: string, data: FilePreviewData): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(PREVIEW_UPDATE_EVENT, { detail: { key, data } }));
}

/** 订阅后台 ETag 校验发现的新版本；返回取消函数。 */
export function onPreviewFileUpdated(filename: string, convId: string | undefined, listener: (data: FilePreviewData) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const key = previewFileCacheKey(filename, convId);
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<{ key?: string; data?: FilePreviewData }>).detail;
    if (detail?.key === key && detail.data) listener(detail.data);
  };
  window.addEventListener(PREVIEW_UPDATE_EVENT, handler);
  return () => window.removeEventListener(PREVIEW_UPDATE_EVENT, handler);
}

/** Remove a cached preview after the user explicitly synchronises a native edit. */
export async function invalidatePreviewFile(filename: string, convId?: string): Promise<void> {
  const key = previewFileCacheKey(filename, convId);
  _previewMemory.delete(key);
  try {
    if (typeof caches === "undefined") return;
    const request = previewCacheRequest(key);
    if (request) await (await caches.open(PREVIEW_CACHE)).delete(request);
  } catch { /* a cache miss must not block the explicit file sync */ }
}

async function fetchPreviewVersion(url: string, key: string, cached: PreviewCacheRecord | null): Promise<FilePreviewData | null> {
  if (useStore.getState().token) await ensureFreshToken();
  const makeHeaders = () => ({ ...authHeaders(), ...(cached?.etag ? { "If-None-Match": cached.etag } : {}) });
  let response = await fetch(url, { headers: makeHeaders() });
  if (response.status === 401 && useStore.getState().token) {
    const renewed = await _doRefresh();
    if (renewed && renewed !== REFRESH_UNAVAILABLE) response = await fetch(url, { headers: makeHeaders() });
  }
  if (response.status === 304 && cached) return null;
  if (response.status === 502 || response.status === 503) throw _offlineError();
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.detail || body.message || `HTTP ${response.status}`) as Error & { status?: number };
    error.status = response.status; throw error;
  }
  _setBackendOnline(true);
  const data = await response.json() as FilePreviewData;
  const etag = response.headers.get("ETag") || `legacy-${data.size || 0}`;
  const record = { etag, data, storedAt: Date.now() };
  await writePreviewCache(key, record);
  if (cached && (cached.etag !== etag || JSON.stringify(cached.data) !== JSON.stringify(data))) announcePreviewUpdate(key, data);
  return data;
}

export async function previewFile(filename: string, convId?: string): Promise<FilePreviewData> {
  // 防御：有时 filename 里混进了 ?token=...（从下载 URL 误取），会让后端文件名超长崩溃。先剥掉查询/锚点。
  filename = filename.split("?")[0].split("#")[0];
  const resolvedConvId = convId || (typeof window !== "undefined" ? window.location.pathname.match(/\/chat\/([^/]+)/)?.[1] : undefined);
  if (!resolvedConvId) throw new Error("无法确定对话 ID");
  const url = `/api/conversations/${resolvedConvId}/files/${encodeURIComponent(filename)}/preview`;
  const key = previewFileCacheKey(filename, resolvedConvId);
  const cached = await readPreviewCache(key);
  if (cached) {
    // 先用持久缓存秒开；后台只发送 If-None-Match。未变化为 304，不重新解析/下载正文。
    void fetchPreviewVersion(url, key, cached).catch(() => {});
    return cached.data;
  }
  const fresh = await fetchPreviewVersion(url, key, null);
  if (!fresh) throw new Error("文件预览缓存不可用");
  return fresh;
}

export async function getMe(): Promise<{ uid?: string; sub?: string; role?: string; auth_provider?: string }> {
  return _fetch(`/api/auth/me`, { headers: headers() });
}

// ── V222 画布 2.0: 会话文件写入 + 就地小问答 ──
export async function saveConvFile(
  convId: string,
  filename: string,
  content: string,
  revision?: { base_sha256: string; lock_session: string },
): Promise<{ ok: boolean; download_url: string; sha256: string }> {
  return _fetch(`/api/conversations/${convId}/files/${encodeURIComponent(filename)}`, {
    method: "PUT", headers: headers(), body: JSON.stringify({ content, ...(revision || {}) }),
  });
}

export interface CanvasBlockPatch {
  op: "replace" | "insert_after";
  block_id: string;
  old_html: string;
  new_html: string;
}

export async function patchCanvasBlocks(
  convId: string,
  filename: string,
  input: {
    base_sha256: string;
    lock_session: string;
    idempotency_key?: string;
    operations: CanvasBlockPatch[];
  },
): Promise<{ ok: boolean; dedup: boolean; sha256: string; base_sha256?: string; applied: number }> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/files/${encodeURIComponent(filename)}/canvas-blocks`, {
    method: "PATCH", headers: headers(), body: JSON.stringify(input),
  });
}

export type EvidenceFreshnessStatus = "current" | "stale" | "unavailable";
export interface BrowserEvidenceRef {
  schema: "hashmm.evidence-ref.v1";
  evidence_id: string;
  conversation_id: string;
  kind: "browser_selection";
  url: string;
  page_title: string;
  text_excerpt: string;
  content_hash: string;
  locator: BrowserEvidenceLocator;
  screenshot_hash?: string;
  browser_session_id?: string;
  captured_at: number;
  trust_level: "untrusted_web";
  freshness: {
    status: EvidenceFreshnessStatus;
    verified_at: number;
    observed_content_hash?: string;
    reason: string;
    verification_count?: number;
  };
}
export interface CanvasEvidenceLink {
  schema: "hashmm.canvas-evidence-link.v1";
  link_id: string;
  evidence_id: string;
  filename: string;
  block_id: string;
  block_hash?: string;
  label?: string;
  linked_at: number;
  relation: "supports";
  evidence: BrowserEvidenceRef;
}
export interface CanvasEvidenceSummary {
  linked: number; current: number; stale: number; unavailable: number; ready: boolean;
}

export async function captureBrowserEvidence(convId: string, input: {
  url: string; page_title?: string; selected_text: string;
  locator?: BrowserEvidenceLocator; screenshot_hash?: string; browser_session_id?: string;
}): Promise<{ ok: boolean; dedup: boolean; evidence: BrowserEvidenceRef }> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/evidence-refs`, {
    method: "POST", headers: headers(), body: JSON.stringify(input),
  });
}

export async function verifyBrowserEvidence(convId: string, evidenceId: string, input: {
  url: string; selected_text?: string;
}): Promise<{ ok: boolean; evidence: BrowserEvidenceRef; affected_artifacts: string[] }> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/evidence-refs/${encodeURIComponent(evidenceId)}/verify`, {
    method: "POST", headers: headers(), body: JSON.stringify(input),
  });
}

export async function listCanvasEvidence(convId: string, filename: string): Promise<{
  schema: string; revision: number; summary: CanvasEvidenceSummary; items: CanvasEvidenceLink[];
}> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/files/${encodeURIComponent(filename)}/evidence-links`, {
    headers: headers(),
  });
}

export async function linkCanvasEvidence(convId: string, filename: string, input: {
  evidence_id: string; block_id?: string; block_hash?: string; label?: string;
}): Promise<{ ok: boolean; dedup: boolean; link: CanvasEvidenceLink; evidence: BrowserEvidenceRef; summary: CanvasEvidenceSummary }> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/files/${encodeURIComponent(filename)}/evidence-links`, {
    method: "POST", headers: headers(), body: JSON.stringify(input),
  });
}

export async function unlinkCanvasEvidence(convId: string, filename: string, linkId: string): Promise<{
  ok: boolean; removed: boolean; summary: CanvasEvidenceSummary;
}> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/files/${encodeURIComponent(filename)}/evidence-links/${encodeURIComponent(linkId)}`, {
    method: "DELETE", headers: headers(),
  });
}
// ── V226 Artifacts 化：发布与组织内 Viewer ──
export async function canvasPublish(convId: string, filename: string, visibility: "org" | "private" | "link"): Promise<{ ok: boolean; share_id: string; url: string; visibility: string; passcode?: string }> {
  return _fetch(`/api/canvas/publish`, { method: "POST", headers: headers(), body: JSON.stringify({ conv_id: convId, filename, visibility }) });
}
export async function canvasShareStatus(convId: string, filename: string): Promise<{ published: boolean; share_id?: string; url?: string; visibility?: string; views?: number; passcode?: string; comments_count?: number; comments_new?: number }> {
  return _fetch(`/api/canvas/shares?conv_id=${encodeURIComponent(convId)}&filename=${encodeURIComponent(filename)}`, { headers: headers() });
}
export async function canvasCommentsRead(shareId: string): Promise<{ ok: boolean; read_ts: number }> {
  return _fetch(`/api/canvas/comments/read`, { method: "POST", headers: headers(), body: JSON.stringify({ share_id: shareId }) });
}
export async function canvasResetPasscode(shareId: string): Promise<{ ok: boolean; passcode: string }> {
  return _fetch(`/api/canvas/passcode/reset`, { method: "POST", headers: headers(), body: JSON.stringify({ share_id: shareId }) });
}
export async function canvasUnpublish(shareId: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/canvas/shares/${shareId}`, { method: "DELETE", headers: headers() });
}
// ── V226 我的模板 ──
export async function listCanvasTemplates(): Promise<{ items: { id: string; name: string; created: number }[]; org_items?: { id: string; name: string; by: string }[] }> {
  return _fetch(`/api/canvas/templates`, { headers: headers() });
}
export async function getCanvasTemplate(id: string): Promise<{ id: string; name: string; html: string }> {
  return _fetch(`/api/canvas/templates/${id}`, { headers: headers() });
}
// ── V231 触发器（webhook） ──
export async function listHooks(): Promise<{ items: { id: string; name: string; kind: string; runner: string; hits: number; last_hit: number; url: string; enabled?: boolean; recent?: { ts: number; keys: string[] }[] }[] }> {
  return _fetch(`/api/hooks`, { headers: headers() });
}
export async function createHook(name: string, kind: string, goal: string): Promise<{ ok: boolean; id: string; url: string }> {
  return _fetch(`/api/hooks`, { method: "POST", headers: headers(), body: JSON.stringify({ name, kind, runner: "desktop", payload: { goal, task: goal, conv_id: "" } }) });
}
export async function replanDispatch(taskId: string): Promise<{ ok: boolean; task_ids: string[] }> {
  return _fetch(`/api/dispatch/${taskId}/replan`, { method: "POST", headers: headers() });
}
export async function toggleHook(id: string): Promise<{ ok: boolean; enabled: boolean }> {
  return _fetch(`/api/hooks/${id}/toggle`, { method: "POST", headers: headers() });
}
export async function retryDispatch(taskId: string): Promise<{ ok: boolean; task_id: string }> {
  return _fetch(`/api/dispatch/${taskId}/retry`, { method: "POST", headers: headers() });
}
export async function deleteHook(id: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/hooks/${id}`, { method: "DELETE", headers: headers() });
}
// ── V231 AI 偏好卡 ──
export async function getPrefs(): Promise<{ text: string; updated: number }> {
  return _fetch(`/api/profile/preferences`, { headers: headers() });
}
export async function putPrefs(text: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/profile/preferences`, { method: "PUT", headers: headers(), body: JSON.stringify({ text }) });
}
export async function delPrefs(): Promise<{ ok: boolean }> {
  return _fetch(`/api/profile/preferences`, { method: "DELETE", headers: headers() });
}
// ── V231 站内通知（桌面铃铛） ──
export async function listNotifications(): Promise<{ items: { id: string; type: string; text: string; by: string; ts: number; read: boolean }[]; unread: number }> {
  return _fetch(`/api/notifications`, { headers: headers() });
}
export async function readAllNotifications(): Promise<{ ok: boolean }> {
  return _fetch(`/api/notifications/read`, { method: "POST", headers: headers() });
}
export async function promoteCanvasTemplate(id: string): Promise<{ ok: boolean; name: string }> {
  return _fetch(`/api/canvas/templates/${id}/promote`, { method: "POST", headers: headers() });
}
export async function canvasLock(convId: string, filename: string, session: string, action: "acquire" | "beat" | "release"): Promise<{ ok: boolean; held?: boolean; age?: number }> {
  return _fetch(`/api/canvas/lock`, { method: "POST", headers: headers(), body: JSON.stringify({ conv_id: convId, filename, session, action }) });
}
export async function marketTemplates(): Promise<{ items: { id: string; name: string; desc: string }[] }> {
  return _fetch(`/api/market/templates`, { headers: headers() });
}
export async function marketInstall(id: string): Promise<{ ok: boolean; name: string }> {
  return _fetch(`/api/market/install`, { method: "POST", headers: headers(), body: JSON.stringify({ id }) });
}
export async function importCanvasTemplates(items: { name: string; html: string }[]): Promise<{ ok: boolean; imported: number; skipped: number }> {
  return _fetch(`/api/canvas/templates/import`, { method: "POST", headers: headers(), body: JSON.stringify({ items }) });
}
export async function deleteCanvasTemplate(id: string): Promise<{ ok: boolean; scope?: string }> {
  return _fetch(`/api/canvas/templates/${id}`, { method: "DELETE", headers: headers() });
}
export async function saveCanvasTemplate(name: string, html: string): Promise<{ ok: boolean; id: string }> {
  return _fetch(`/api/canvas/templates`, { method: "POST", headers: headers(), body: JSON.stringify({ name, html }) });
}

export async function saveCanvasQa(convId: string, user: string, assistant: string): Promise<{ ok?: boolean }> {
  return _fetch(`/api/llm/cu_save`, { method: "POST", headers: headers(),
    body: JSON.stringify({ conv_id: convId, user_content: user, assistant_content: assistant }) });
}
export async function canvasAsk(question: string, context: string): Promise<{ answer: string; model?: string }> {
  return _fetch(`/api/canvas/ask`, { method: "POST", headers: headers(), body: JSON.stringify({ question, context }) });
}

// ── V217: 画布版本历史（跨会话落盘，与 previewFile 同一条会话文件链路）──
export async function getCanvasVersions(convId: string, filename: string): Promise<{ versions: { ts: number; html: string }[] }> {
  return _fetch(`/api/conversations/${convId}/files/${encodeURIComponent(filename)}/canvas-versions`, { headers: headers() });
}
export async function saveCanvasVersion(convId: string, filename: string, html: string): Promise<{ versions: number; dedup?: boolean }> {
  return _fetch(`/api/conversations/${convId}/files/${encodeURIComponent(filename)}/canvas-versions`, {
    method: "PUT", headers: headers(), body: JSON.stringify({ html }),
  });
}

// ── Conversation Search ──
export async function searchConversations(query: string): Promise<{
  results: Array<{ session_id: string; title: string; role: string; snippet: string; message_index: number; created: number }>;
  query: string; total: number;
}> {
  return _fetch(`/api/search?q=${encodeURIComponent(query)}`, { headers: headers() });
}

// ═══════════════════════════════════════════════════════════════════
// v10.0: Conversation APIs (server-side persistence)
// ═══════════════════════════════════════════════════════════════════

export interface ConversationPage {
  total: number;
  has_more: boolean;
  oldest_created_at?: number | null;
}

export async function loadConversation(convId: string): Promise<{ conversation: ConversationMeta; messages: Message[]; page?: ConversationPage }> {
  return _fetch(`/api/conversations/${convId}`, { headers: headers() });
}

export async function loadEarlierConversationMessages(
  convId: string, beforeTs: number, limit = 500,
): Promise<{ messages: Message[]; page?: ConversationPage }> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/messages?limit=${Math.max(1, Math.min(limit, 2000))}&before_ts=${encodeURIComponent(beforeTs)}`,
    { headers: headers() });
}

export async function listConversations(since = ""): Promise<{ conversations: ConversationMeta[]; sync_cursor?: string }> {
  const q = since ? `?since=${encodeURIComponent(since)}` : "";
  return _fetch(`/api/conversations${q}`, { headers: headers() });
}

export async function getConversationPrompt(convId: string): Promise<{ prompt: string }> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/prompt`, { headers: headers() });
}

export async function saveConversationPrompt(convId: string, prompt: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/prompt`, {
    method: "PATCH",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
}

export async function createConversation(id: string, title: string, projectId?: string): Promise<{ id: string; project_id?: string | null }> {
  return _fetch("/api/conversations", {
    method: "POST", headers: headers(),
    body: JSON.stringify({ id, title, project_id: projectId || null })
  });
}

export interface ChatContinuation {
  schema: "hashmm.chat-continuation.v2";
  id: string;
  owner_id: string;
  project_id?: string | null;
  source_conversation_id: string;
  target_conversation_id: string;
  source_revision: number;
  status: "sealed" | "claimed" | "acknowledged" | "rejected" | "expired" | "superseded";
  created_at: number;
  expires_at: number;
  payload: {
    objective?: string;
    latest_user_request?: string;
    constraints?: string[];
    plan?: Array<{ id: string; text: string; status: string }>;
    completed_steps?: string[];
    pending_steps?: string[];
    blockers?: string[];
    next_action?: string;
    policy?: Record<string, unknown>;
  };
  state_verification?: { stale: boolean; reasons: string[]; checked_at: number };
}

export async function createChatContinuation(
  sourceConversationId: string,
  idempotencyKey: string,
): Promise<{ handoff: ChatContinuation; created: boolean }> {
  return _fetch(`/api/conversations/${encodeURIComponent(sourceConversationId)}/handoffs`, {
    method: "POST",
    headers: { ...headers(), "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({}),
  });
}

export async function getIncomingChatContinuation(
  targetConversationId: string,
): Promise<{ handoff: ChatContinuation | null }> {
  return _fetch(`/api/conversations/${encodeURIComponent(targetConversationId)}/handoffs/incoming`, {
    headers: headers(),
  });
}

export async function transitionChatContinuation(
  targetConversationId: string,
  handoffId: string,
  action: "claim" | "acknowledge" | "reject" | "supersede",
): Promise<{ handoff: ChatContinuation }> {
  return _fetch(
    `/api/conversations/${encodeURIComponent(targetConversationId)}/handoffs/${encodeURIComponent(handoffId)}/${action}`,
    { method: "POST", headers: headers(), body: "{}" },
  );
}

/** @deprecated Use the bounded Chat Continuation names; this is not a device resume. */
export type ChatHandoff = ChatContinuation;
export const createChatHandoff = createChatContinuation;
export const getIncomingChatHandoff = getIncomingChatContinuation;
export const transitionChatHandoff = transitionChatContinuation;

export interface ChatMailboxMessage {
  id: string;
  schema: "hashmm.chat-mailbox.v1";
  source_conversation_id: string;
  target_conversation_id: string;
  status: "queued" | "delivered" | "read" | "acknowledged" | "rejected" | "expired";
  payload: Record<string, unknown>;
  policy: {
    execution_authority: false;
    permissions_carried: false;
    private_reasoning_persisted: false;
  };
  created_at: number;
  updated_at: number;
  expires_at: number;
}

export async function sendChatMailboxMessage(
  sourceConversationId: string,
  targetConversationId: string,
  payload: Record<string, unknown>,
  idempotencyKey: string,
): Promise<{ message: ChatMailboxMessage; created: boolean }> {
  return _fetch(`/api/conversations/${encodeURIComponent(sourceConversationId)}/mailbox`, {
    method: "POST",
    headers: { ...headers(), "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ target_conversation_id: targetConversationId, payload }),
  });
}

export async function getChatMailbox(
  targetConversationId: string,
): Promise<{ messages: ChatMailboxMessage[] }> {
  return _fetch(`/api/conversations/${encodeURIComponent(targetConversationId)}/mailbox`, {
    headers: headers(),
  });
}

export async function transitionChatMailboxMessage(
  targetConversationId: string,
  messageId: string,
  action: "read" | "acknowledge" | "reject",
): Promise<{ message: ChatMailboxMessage }> {
  return _fetch(
    `/api/conversations/${encodeURIComponent(targetConversationId)}/mailbox/${encodeURIComponent(messageId)}/${action}`,
    { method: "POST", headers: headers(), body: "{}" },
  );
}

export async function updateConversation(convId: string, data: { title?: string; pinned?: number; archived?: number }) {
  return _fetch(`/api/conversations/${convId}`, {
    method: "PATCH", headers: headers(),
    body: JSON.stringify(data)
  });
}

export async function deleteConversation(convId: string) {
  return _fetch(`/api/conversations/${convId}`, { method: "DELETE", headers: headers() });
}

export async function saveStreamingMessage(convId: string, msgId: string, content: string) {
  try {
    await _fetch(`/api/conversations/${convId}/messages/${msgId}`, {
      method: "PATCH", headers: headers(),
      body: JSON.stringify({ content, status: "streaming" })
    });
  } catch {}
}

type ConvFileList = { files: FileInfo[]; revision?: string };
type ConvFileListCacheRecord = { etag: string; data: ConvFileList; storedAt: number };
const CONV_FILE_LIST_CACHE = "hashmm-conversation-file-lists-v1";
const _convFileListMemory = new Map<string, ConvFileListCacheRecord>();

function convFileListKey(convId: string): string {
  return `${previewScope()}|${convId}`;
}

function convFileListRequest(key: string): Request | null {
  if (typeof window === "undefined") return null;
  return new Request(`${window.location.origin}/__hashmm_conversation_file_list_cache__/${encodeURIComponent(key)}`);
}

async function readConvFileListCache(key: string): Promise<ConvFileListCacheRecord | null> {
  const memory = _convFileListMemory.get(key);
  if (memory) return memory;
  try {
    if (typeof caches === "undefined") return null;
    const request = convFileListRequest(key); if (!request) return null;
    const response = await (await caches.open(CONV_FILE_LIST_CACHE)).match(request);
    if (!response) return null;
    const record = await response.json() as ConvFileListCacheRecord;
    if (!Array.isArray(record?.data?.files)) return null;
    _convFileListMemory.set(key, record);
    return record;
  } catch { return null; }
}

async function writeConvFileListCache(key: string, record: ConvFileListCacheRecord): Promise<void> {
  _convFileListMemory.set(key, record);
  try {
    if (typeof caches === "undefined") return;
    const request = convFileListRequest(key); if (!request) return;
    await (await caches.open(CONV_FILE_LIST_CACHE)).put(request, new Response(JSON.stringify(record), {
      headers: { "Content-Type": "application/json", "X-HashMM-File-List-Cache": "1" },
    }));
  } catch { /* memory cache remains available */ }
}

async function fetchConvFileListVersion(convId: string, key: string, cached: ConvFileListCacheRecord | null): Promise<ConvFileList | null> {
  if (useStore.getState().token) await ensureFreshToken();
  const makeHeaders = () => ({ ...headers(), ...(cached?.etag ? { "If-None-Match": cached.etag } : {}) });
  let response = await fetch(`/api/conversations/${encodeURIComponent(convId)}/files`, { headers: makeHeaders() });
  if (response.status === 401 && useStore.getState().token) {
    const renewed = await _doRefresh();
    if (renewed && renewed !== REFRESH_UNAVAILABLE) {
      response = await fetch(`/api/conversations/${encodeURIComponent(convId)}/files`, { headers: makeHeaders() });
    }
  }
  if (response.status === 304 && cached) return null;
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json() as ConvFileList;
  const record = { etag: response.headers.get("ETag") || `legacy-${JSON.stringify(data).length}`, data, storedAt: Date.now() };
  await writeConvFileListCache(key, record);
  if (cached && (cached.etag !== record.etag || cached.data.revision !== data.revision) && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("hmm-conv-file-list-updated", { detail: { key, data } }));
  }
  return data;
}

export function onConvFileListUpdated(convId: string, listener: (data: ConvFileList) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const key = convFileListKey(convId);
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<{ key?: string; data?: ConvFileList }>).detail;
    if (detail?.key === key && detail.data) listener(detail.data);
  };
  window.addEventListener("hmm-conv-file-list-updated", handler);
  return () => window.removeEventListener("hmm-conv-file-list-updated", handler);
}

export async function listConvFiles(convId: string): Promise<ConvFileList> {
  const key = convFileListKey(convId);
  const cached = await readConvFileListCache(key);
  if (cached) {
    // Open instantly from the account-scoped cache; a conditional request only
    // transfers a body when the owned workspace metadata revision changed.
    void fetchConvFileListVersion(convId, key, cached).catch(() => {});
    return cached.data;
  }
  const fresh = await fetchConvFileListVersion(convId, key, null);
  return fresh || { files: [] };
}

/** v10.0 SSE — uses new conversation endpoint with typed handlers. */
export async function chatStreamV10(
  convId: string,
  message: string,
  fileContext: string | null,
  cb: StreamCallbacks,
  docFilter?: string[],
  retrievalMode?: string,
  attachments?: string[],
  effort?: string,
  featureContexts?: FeatureContext[],
  retrievalDepth?: "auto" | "deep",
  runMode?: "auto" | "browser" | "deep" | "computer",
  pluginIds?: string[],
) {
  await ensureFreshToken();   // renew a near-expired access token before a long stream
  const controller = new AbortController();
  // A long AgentLoop is kept alive by SSE keepalives/progress events. Use an
  // idle timeout rather than an arbitrary five-minute wall clock cutoff, with
  // a hard upper bound so a broken server cannot leave a renderer hanging
  // forever. This preserves long tasks without weakening cancellation.
  const idleTimeoutMs = 120000;
  const hardTimeoutMs = 30 * 60 * 1000;
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  let hardTimer: ReturnType<typeof setTimeout> | null = null;
  let cancelTimer: ReturnType<typeof setInterval> | null = null;
  // Keep this as a runtime tag (rather than a narrowed union) because the
  // value is assigned from timer callbacks outside TypeScript's control-flow
  // graph.
  let timeoutReason: string = "idle";
  const clearTimers = () => {
    if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
    if (hardTimer) { clearTimeout(hardTimer); hardTimer = null; }
    if (cancelTimer) { clearInterval(cancelTimer); cancelTimer = null; }
  };
  const refreshIdle = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { timeoutReason = "idle"; controller.abort(); }, idleTimeoutMs);
  };
  refreshIdle();
  hardTimer = setTimeout(() => { timeoutReason = "hard"; controller.abort(); }, hardTimeoutMs);
  cancelTimer = setInterval(() => {
    if (cb.shouldStop?.()) {
      timeoutReason = "user";
      controller.abort();
    }
  }, 100);
  try {
    const res = await fetch(`/api/conversations/${convId}/stream`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        message,
        file_context: fileContext || undefined,
        custom_prompt: useStore.getState().customPrompt || undefined,
        doc_filter: docFilter && docFilter.length > 0 ? docFilter : undefined,
        retrieval_mode: retrievalMode || "mix",
        answer_style: (typeof window !== "undefined" && localStorage.getItem("hmm_answer_style")) || undefined,
        // V86: 问答栏附件文件名（已 /api/upload 落盘）。服务端持久化为用户消息的
        // files 元数据；图片在配置视觉模型时做定向理解注入上下文。
        attachments: attachments && attachments.length > 0 ? attachments : undefined,
        // V269 努力档位（对齐 Claude Code 的 effort：控制"整体干多少活"，不只是想多久）
        effort: effort && effort !== "standard" ? effort : undefined,
        // V340：面板数据是结构化“上下文附件”，不是拼进用户问题的隐藏指令。
        // 服务端会再次做 kind 白名单、预算、密钥脱敏和不可信边界封装。
        feature_contexts: featureContexts && featureContexts.length > 0
          ? featureContexts.map(x => ({
              kind: x.kind,
              title: x.title,
              content: x.content,
              source: x.source,
              document_names: x.document_names,
            }))
          : undefined,
        // 深度检索进入同一个 conversation SSE / AgentLoop，不再走独立旁路答案。
        retrieval_depth: retrievalDepth === "deep" ? "deep" : undefined,
        work_method: {
          retrieval: retrievalMode || "auto",
          effort: effort || "standard",
          run_mode: runMode || (retrievalDepth === "deep" ? "deep" : "auto"),
        },
        // Missing means "use the server default active set" for compatibility.
        // An explicit empty array means the user disabled every plugin for Chat.
        plugin_ids: pluginIds,
      }),
      signal: controller.signal,
    });
    if (res.status === 502 || res.status === 503) {   // V269: 壳层代离线 → 友好提示 + 点亮离线模式
      clearTimers(); _setBackendOnline(false);
      cb.onError("后端未连接，请确认服务已启动后重试（历史与云端记录可离线查看）"); return;
    }
    if (!res.ok) {
      clearTimers();
      if (res.status === 401) {
        // Try one silent refresh; only log out if it genuinely failed (revoked).
        const nt = await _doRefresh();
        if (nt === REFRESH_UNAVAILABLE) { cb.onError("身份续期暂时不可用，登录状态已保留，请稍后重试"); return; }
        if (nt) { cb.onError("登录已自动刷新，请重新发送"); return; }
        if (useStore.getState().backendOnline === false) { cb.onError("后端未连接，请确认服务已启动后重试"); return; }   // V270 保险丝
        useStore.getState().requireReauth(); cb.onError("登录已过期"); return;
      }
      cb.onError(`服务错误 (${res.status})`);
      return;
    }
    const reader = res.body?.getReader();
    if (!reader) { clearTimers(); cb.onError("浏览器不支持流式响应"); return; }

    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // Any bytes from the SSE stream (including keepalive frames) prove the
      // backend is still alive and extend the idle window.
      refreshIdle();
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";
      for (const event of events) {
        if (!event.trim()) continue;
        const lines = event.split("\n");
        let eventType = "", data = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) eventType = line.slice(7);
          else if (line.startsWith("data: ")) data = line.slice(6);
        }
        if (!data || eventType === "keepalive") continue;
        try {
          const parsed = JSON.parse(data);
          if (eventType === "turn_started") cb.onTurnStarted?.(parsed);
          else if (eventType === "turn_state") cb.onTurnState?.(parsed);
          else if (eventType === "steer_applied") cb.onSteerApplied?.(parsed);
          else if (eventType === "trace") cb.onTrace(parsed.steps || []);
          else if (eventType === "plan") cb.onPlan?.(parsed);
          else if (eventType === "task_contract") cb.onTaskContract?.(parsed);
          else if (eventType === "token") cb.onToken(parsed.content || "");
          else if (eventType === "thinking") cb.onThinking(parsed);
          else if (eventType === "step_start") cb.onStepStart(parsed);
          else if (eventType === "step_done") cb.onStepDone(parsed);
          else if (eventType === "file") cb.onFile(parsed);
          else if (eventType === "iteration") cb.onIteration(parsed);
          else if (eventType === "progress") { if (cb.onProgress) cb.onProgress(parsed); }
          else if (eventType === "sources") { if (cb.onSources) cb.onSources(parsed.sources || []); }
          else if (eventType === "input_request") { if (cb.onInputRequest) cb.onInputRequest(parsed); }
          else if (eventType === "approval_request") { if (cb.onApprovalRequest) cb.onApprovalRequest(parsed); }
          else if (eventType === "clarify") { if (cb.onClarify) cb.onClarify(parsed); }
          else if (eventType === "orchestration") { if (cb.onOrchestration) cb.onOrchestration(parsed); }
          else if (eventType === "subagent") { if (cb.onSubagent) cb.onSubagent(parsed); }
          else if (eventType === "done") cb.onDone(parsed);
        } catch {}
      }
    }
    clearTimers();
  } catch (e: unknown) {
    clearTimers();
    if (e instanceof Error && e.name === "AbortError") {
      if (timeoutReason === "user") {
        cb.onCancelled?.();
        return;
      }
      cb.onError(timeoutReason === "hard"
        ? "任务已运行超过 30 分钟，已停止等待；如果后台任务仍在执行，可稍后重新打开会话查看结果。"
        : "超过 2 分钟没有收到服务端进展，已停止等待；请检查后端状态后重试。");
      return;
    }
    if (e instanceof Error && e.message?.includes("Failed to fetch")) {
      _setBackendOnline(false);   // V269: 网络级失败＝后端离线 → 点亮离线模式
      cb.onError("后端未连接，请确认服务已启动后重试（历史与云端记录可离线查看）"); return;
    }
    cb.onError(e instanceof Error ? e.message : "网络错误");
  }
}

// ── v10.0: Evolution API ──
export async function listEvolutionSkills() { return _fetch("/api/evolution/skills", { headers: headers() }); }
export interface SkillCandidateCheck {
  id: string;
  passed: boolean;
  detail: string;
}
export interface SkillReplayEvaluation {
  schema: "hashmm.skill-replay-evaluation.v1";
  status: "not_evaluable" | "passed" | "failed" | "insufficient_evidence";
  release_eligible: boolean;
  evaluated_at?: number;
  historical_cases: number;
  adversarial_cases: number;
  gates: SkillCandidateCheck[];
  failed_required: string[];
  baseline: { mean_score: number; latency_ms: number; output_tokens_estimated: number };
  candidate: { mean_score: number; latency_ms: number; output_tokens_estimated: number };
  delta: { mean_score: number; material_regressions: number; safety_regressions: number };
  raw_prompts_persisted?: false;
  raw_answers_persisted?: false;
  limitation: string;
}
export interface SkillEvolutionVariant {
  id: string;
  status: "candidate" | "promoted" | "rejected" | "retired" | "rolled_back" | "legacy";
  prompt_hash: string;
  prompt_preview: string;
  prompt_text?: string;
  uses: number;
  positive: number;
  negative: number;
  evaluation: {
    status: "passed" | "failed";
    checks: SkillCandidateCheck[];
    failed_required: string[];
    automatic_promotion_allowed: false;
    quality?: Partial<SkillReplayEvaluation> & { status?: SkillReplayEvaluation["status"]; reason?: string };
  };
  permission_diff: {
    schema?: string;
    expanded: boolean;
    baseline?: Record<string, unknown>;
    candidate?: Record<string, unknown>;
  };
  created_at: number;
}
export interface SkillEvolutionRun {
  schema: "hashmm.governed-skill-evolution.v2";
  id: string;
  skill_id: string;
  status: "review_required" | "promoted" | "rejected" | "rolled_back" | "stale";
  baseline_hash: string;
  baseline_scope: string;
  permission_manifest: Record<string, unknown>;
  source_run_ids: string[];
  selected_variant_id: string;
  decision_reason: string;
  work_run_id: string;
  created_at: number;
  updated_at: number;
  decided_at: number;
  variants: SkillEvolutionVariant[];
  automatic_promotion_allowed: false;
  rollback_available: boolean;
}
export interface SkillEvolutionStatus {
  schema: "hashmm.governed-skill-evolution.v2";
  skill_id: string;
  state: string;
  runs: SkillEvolutionRun[];
  active_run: SkillEvolutionRun | null;
  automatic_promotion_allowed: false;
}
export async function getSkillEvolution(id: string): Promise<SkillEvolutionStatus> {
  return _fetch(`/api/evolution/skills/${encodeURIComponent(id)}/evolution`, { headers: headers() });
}
export async function proposeSkillEvolution(id: string, sourceRunIds: string[] = []): Promise<{ ok: boolean; run: SkillEvolutionRun }> {
  return _fetch(`/api/evolution/skills/${encodeURIComponent(id)}/evolve`, {
    method: "POST", headers: headers(), body: JSON.stringify({ source_run_ids: sourceRunIds }),
  });
}
export async function evaluateSkillEvolution(
  skillId: string, runId: string, variantId: string,
): Promise<{ ok: boolean; state: string; release_eligible: boolean; run: SkillEvolutionRun }> {
  return _fetch(`/api/evolution/skills/${encodeURIComponent(skillId)}/evolution/${encodeURIComponent(runId)}/evaluate`, {
    method: "POST", headers: headers(), body: JSON.stringify({ variant_id: variantId }),
  });
}
export async function approveSkillEvolution(
  skillId: string, runId: string, variantId: string, expectedBaselineHash: string, reason = "",
): Promise<{ ok: boolean; state: string; run: SkillEvolutionRun }> {
  return _fetch(`/api/evolution/skills/${encodeURIComponent(skillId)}/evolution/${encodeURIComponent(runId)}/approve`, {
    method: "POST", headers: headers(),
    body: JSON.stringify({ variant_id: variantId, expected_baseline_hash: expectedBaselineHash, reason }),
  });
}
export async function rejectSkillEvolution(
  skillId: string, runId: string, reason = "",
): Promise<{ ok: boolean; state: string; run: SkillEvolutionRun }> {
  return _fetch(`/api/evolution/skills/${encodeURIComponent(skillId)}/evolution/${encodeURIComponent(runId)}/reject`, {
    method: "POST", headers: headers(), body: JSON.stringify({ reason }),
  });
}
export async function rollbackSkillEvolution(
  skillId: string, runId: string, reason = "",
): Promise<{ ok: boolean; state: string; run: SkillEvolutionRun }> {
  return _fetch(`/api/evolution/skills/${encodeURIComponent(skillId)}/evolution/${encodeURIComponent(runId)}/rollback`, {
    method: "POST", headers: headers(), body: JSON.stringify({ reason }),
  });
}

// ── V203 技能包（Agent Skills / SKILL.md）：routes/skill_packs.py ──
export interface SkillPack {
  id: string; name: string; description: string; enabled: boolean; source: string;
  installed_at: number; license: string; triggers: string[]; file_count: number; size_bytes: number;
  allowed_tools?: string[]; network?: boolean; filesystem?: boolean;
}
export interface CatalogSkill {
  catalog_id: string; name: string; description: string; license: string;
  allowed_tools: string[]; network: boolean; filesystem: boolean;
  file_count: number; size_bytes: number; installed: boolean; preview: string;
}
export async function listSkillCatalog(): Promise<{ ok: boolean; catalog: CatalogSkill[]; stats: { total: number; installed: number } }> {
  return _fetch("/api/skills/packs/catalog", { headers: headers() });
}
export async function installFromCatalog(catalogId: string) {
  return _fetch("/api/skills/packs/catalog/install", { method: "POST", headers: headers(), body: JSON.stringify({ catalog_id: catalogId }) });
}
export async function listSkillPacks(): Promise<{ ok: boolean; packs: SkillPack[]; enabled_count: number }> {
  return _fetch("/api/skills/packs", { headers: headers() });
}
export async function getSkillPack(id: string): Promise<{ ok: boolean; pack: SkillPack; skill_md: string; files: { path: string; size: number }[] }> {
  return _fetch(`/api/skills/packs/${encodeURIComponent(id)}`, { headers: headers() });
}
export async function toggleSkillPack(id: string, enabled: boolean) {
  return _fetch(`/api/skills/packs/${encodeURIComponent(id)}/toggle`, { method: "POST", headers: headers(), body: JSON.stringify({ enabled }) });
}
export async function deleteSkillPack(id: string) {
  return _fetch(`/api/skills/packs/${encodeURIComponent(id)}`, { method: "DELETE", headers: headers() });
}
export async function importSkillPack(req: { kind: "github" | "path"; url?: string; path?: string }) {
  return _fetch("/api/skills/packs/import", { method: "POST", headers: headers(), body: JSON.stringify(req) });
}
export async function seedBuiltinSkillPacks() {
  return _fetch("/api/skills/packs/seed-builtins", { method: "POST", headers: headers() });
}
/** 上传 .zip 技能包（multipart，不能走 JSON 的 _fetch）。 */
export async function uploadSkillPack(file: File): Promise<{ ok: boolean; installed: SkillPack[] }> {
  await ensureFreshToken();
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/skills/packs/upload", { method: "POST", headers: authHeaders(), body: fd });
  if (!r.ok) {
    const body = await r.json().catch(() => ({} as Record<string, string>));
    throw new Error((body as { detail?: string; message?: string }).detail || (body as { message?: string }).message || `HTTP ${r.status}`);
  }
  return r.json();
}

// User-owned skills are isolated from the administrator-managed system
// library. They use the same Agent Skills/SKILL.md format but only influence
// turns owned by the authenticated account.
export async function listMySkillPacks(): Promise<{ ok: boolean; packs: SkillPack[]; enabled_count: number; execution: string }> {
  return _fetch("/api/skills/packs/mine", { headers: headers() });
}
export async function getMySkillPack(id: string): Promise<{ ok: boolean; pack: SkillPack; skill_md: string; files: { path: string; size: number }[] }> {
  return _fetch(`/api/skills/packs/mine/${encodeURIComponent(id)}`, { headers: headers() });
}
export async function toggleMySkillPack(id: string, enabled: boolean) {
  return _fetch(`/api/skills/packs/mine/${encodeURIComponent(id)}/toggle`, {
    method: "POST", headers: headers(), body: JSON.stringify({ enabled }),
  });
}
export async function deleteMySkillPack(id: string) {
  return _fetch(`/api/skills/packs/mine/${encodeURIComponent(id)}`, { method: "DELETE", headers: headers() });
}
export async function importMySkillPack(req: {
  kind: "github" | "url"; url: string;
  source?: "website" | "github" | "codex" | "claude" | "agent-skills";
}): Promise<{ ok: boolean; installed: SkillPack[] }> {
  return _fetch("/api/skills/packs/mine/import", {
    method: "POST", headers: headers(), body: JSON.stringify(req),
  });
}
export async function uploadMySkillPack(
  file: File,
  source: "upload" | "codex" | "claude" | "agent-skills" = "upload",
): Promise<{ ok: boolean; installed: SkillPack[] }> {
  await ensureFreshToken();
  const fd = new FormData();
  fd.append("file", file);
  const response = await fetch(`/api/skills/packs/mine/upload?source=${encodeURIComponent(source)}`, {
    method: "POST", headers: authHeaders(), body: fd,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({} as Record<string, string>));
    throw new Error((body as { detail?: string }).detail || `HTTP ${response.status}`);
  }
  return response.json();
}

export interface RuntimePlugin {
  name: string;
  version: string;
  description: string;
  author: string;
  enabled: boolean;
  runtime: "manifest" | "python";
  capabilities: string[];
  permissions: { filesystem?: string; network?: string; side_effects?: boolean };
  tools: string[];
  sha256: string;
  trusted: boolean;
  active: boolean;
  status: string;
  error?: string;
  execution_boundary?: string;
}
export async function listRuntimePlugins(): Promise<{ plugins: RuntimePlugin[]; can_manage: boolean }> {
  return _fetch("/api/plugins", { headers: headers() });
}
export async function trustRuntimePlugin(name: string, expectedSha256: string) {
  return _fetch(`/api/plugins/${encodeURIComponent(name)}/trust`, {
    method: "POST", headers: headers(), body: JSON.stringify({ expected_sha256: expectedSha256 }),
  });
}
export async function loadRuntimePlugin(name: string) {
  return _fetch(`/api/plugins/${encodeURIComponent(name)}/load`, {
    method: "POST", headers: headers(), body: JSON.stringify({}),
  });
}
export async function revokeRuntimePlugin(name: string) {
  return _fetch(`/api/plugins/${encodeURIComponent(name)}/revoke`, {
    method: "POST", headers: headers(), body: JSON.stringify({}),
  });
}
export async function activateManifestPlugin(name: string, expectedSha256: string) {
  return _fetch(`/api/plugins/${encodeURIComponent(name)}/activate`, {
    method: "POST", headers: headers(), body: JSON.stringify({ expected_sha256: expectedSha256 }),
  });
}
export async function deactivateManifestPlugin(name: string, expectedSha256: string) {
  return _fetch(`/api/plugins/${encodeURIComponent(name)}/deactivate`, {
    method: "POST", headers: headers(), body: JSON.stringify({ expected_sha256: expectedSha256 }),
  });
}
export async function quarantineRuntimePlugin(name: string) {
  return _fetch(`/api/plugins/${encodeURIComponent(name)}/quarantine`, {
    method: "POST", headers: headers(), body: JSON.stringify({}),
  });
}
export interface RuntimePluginDiagnostics {
  schema: "hashmm.plugin-diagnostics.v1";
  name: string; version: string; runtime: "manifest" | "python"; status: string;
  active: boolean; sha256: string; file_count: number; files: string[];
  permissions: { filesystem?: string; network?: string; side_effects?: boolean };
  tools: Array<{ name: string; annotations: { read_only?: boolean; destructive?: boolean; idempotent?: boolean; open_world?: boolean; title?: string } }>;
  trust: { trusted: boolean; trusted_at: number; trusted_by: string };
  execution_boundary: string; error?: string;
}
export async function getRuntimePluginDiagnostics(name: string): Promise<RuntimePluginDiagnostics> {
  return _fetch(`/api/plugins/${encodeURIComponent(name)}/diagnostics`, { headers: headers() });
}
export async function installRuntimePlugin(file: File, replace = false): Promise<{
  ok: true; action: "installed" | "upgraded"; archive_sha256: string; package_sha256: string;
  file_count: number; plugin: RuntimePlugin; trust_required: true; execution_started: false;
  previous_quarantined: boolean;
}> {
  await ensureFreshToken();
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const sha256 = Array.from(new Uint8Array(digest)).map(value => value.toString(16).padStart(2, "0")).join("");
  const response = await fetch(`/api/plugins/install?replace=${replace ? "true" : "false"}`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/zip", "X-Plugin-Sha256": sha256 },
    body: bytes,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({} as Record<string, string>));
    const error = new Error((body as { detail?: string }).detail || `HTTP ${response.status}`) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return response.json();
}

// ── V338 执行 Hooks：声明式规则 + Python Hook 精确哈希信任 ──
export interface ExecutionHookRule {
  name: string;
  when: { tools?: string[]; pattern?: string };
  action: "notify" | "confirm" | "block";
  message?: string;
  enabled?: boolean;
}

export interface CodeHookItem {
  name: string;
  source: string;
  sha256: string;
  size: number;
  modified: number;
  events: string[];
  trusted: boolean;
  active: boolean;
  receipt_present: boolean;
  trusted_at: number;
  status: "disabled" | "invalid" | "untrusted" | "changed" | "active" | "trusted_pending_restart" | "unsafe" | "missing";
  error?: string;
}

export async function getExecutionHookRules(): Promise<{ ok: boolean; hooks: ExecutionHookRule[] }> {
  return _fetch("/api/skills/packs/hooks", { headers: headers() });
}

export async function saveExecutionHookRules(hooks: ExecutionHookRule[]): Promise<{ ok: boolean; hooks: ExecutionHookRule[] }> {
  return _fetch("/api/skills/packs/hooks", {
    method: "POST", headers: headers(), body: JSON.stringify({ hooks }),
  });
}

export async function listCodeHooks(): Promise<{ ok: boolean; enabled: boolean; can_manage: boolean; items: CodeHookItem[]; warning: string }> {
  return _fetch("/api/skills/packs/code-hooks", { headers: headers() });
}

export async function trustCodeHook(name: string, expectedSha256: string): Promise<{ ok: boolean; hook: CodeHookItem; restart_required: boolean }> {
  return _fetch(`/api/skills/packs/code-hooks/${encodeURIComponent(name)}/trust`, {
    method: "POST", headers: headers(), body: JSON.stringify({ expected_sha256: expectedSha256 }),
  });
}

export async function revokeCodeHook(name: string): Promise<{ ok: boolean; revoked: boolean }> {
  return _fetch(`/api/skills/packs/code-hooks/${encodeURIComponent(name)}/revoke`, {
    method: "POST", headers: headers(),
  });
}
export async function deleteEvolutionSkill(id: string) {
  return _fetch(`/api/evolution/skills/${encodeURIComponent(id)}`, { method: "DELETE", headers: headers() });
}
export async function skillFeedback(skillId: string, feedback: "up" | "down") {
  return _fetch("/api/evolution/skills/feedback", { method: "POST", headers: headers(), body: JSON.stringify({ skill_id: skillId, feedback }) });
}
export async function getUserProfile() { return _fetch("/api/evolution/profile", { headers: headers() }); }
export async function getPromptAnalysis() { return _fetch("/api/evolution/prompt/analysis", { headers: headers() }); }

// ── v10.0: Tool Management API ──
export async function listTools() { return _fetch("/api/admin/tools", { headers: headers() }); }
export async function toggleTool(name: string, enabled: boolean) {
  return _fetch(`/api/admin/tools/${encodeURIComponent(name)}`, { method: "PATCH", headers: headers(), body: JSON.stringify({ enabled }) });
}
export async function getToolStats() { return _fetch("/api/admin/tools/stats", { headers: headers() }); }

// v14 Phase 3: custom API tools
export interface CustomToolParam {
  name: string; type: string; description: string; required: boolean; location: string;
}
export interface CustomToolConfig {
  id?: string;
  name: string; description: string; method: string; url_template: string;
  headers: Record<string, string>; params_schema: CustomToolParam[];
  body_template: string; response_path: string; enabled: boolean;
  call_count?: number;
}
export async function listCustomTools() { return _fetch("/api/admin/tools/custom", { headers: headers() }); }
export async function createCustomTool(cfg: CustomToolConfig) {
  return _fetch("/api/admin/tools/custom", { method: "POST", headers: headers(), body: JSON.stringify(cfg) });
}
export async function updateCustomTool(id: string, cfg: CustomToolConfig) {
  return _fetch(`/api/admin/tools/custom/${id}`, { method: "PUT", headers: headers(), body: JSON.stringify(cfg) });
}
export async function deleteCustomTool(id: string) {
  return _fetch(`/api/admin/tools/custom/${id}`, { method: "DELETE", headers: headers() });
}
export async function testCustomTool(config: CustomToolConfig, test_args: Record<string, unknown>) {
  return _fetch("/api/admin/tools/custom/test", { method: "POST", headers: headers(), body: JSON.stringify({ config, test_args }) });
}

// v14 Phase 4: MCP servers
export interface MCPServerConfig {
  id?: string;
  name: string; transport: string; endpoint: string;
  headers: Record<string, string>; enabled: boolean;
  tools_cache?: { name: string; description?: string }[];
  last_status?: "unknown" | "ready" | "error";
  last_error?: string; last_checked_at?: number; protocol_version?: string;
  server_info?: Record<string, unknown>; capabilities?: Record<string, unknown>;
}
export async function listMcpServers() { return _fetch("/api/admin/tools/mcp", { headers: headers() }); }
export async function createMcpServer(cfg: MCPServerConfig) {
  return _fetch("/api/admin/tools/mcp", { method: "POST", headers: headers(), body: JSON.stringify(cfg) });
}
export async function updateMcpServer(id: string, cfg: MCPServerConfig) {
  return _fetch(`/api/admin/tools/mcp/${id}`, { method: "PUT", headers: headers(), body: JSON.stringify(cfg) });
}
export async function deleteMcpServer(id: string) {
  return _fetch(`/api/admin/tools/mcp/${id}`, { method: "DELETE", headers: headers() });
}
export async function refreshMcpServer(id: string) {
  return _fetch(`/api/admin/tools/mcp/${id}/refresh`, { method: "POST", headers: headers() });
}
export async function testMcpServer(cfg: MCPServerConfig) {
  return _fetch("/api/admin/tools/mcp/test", { method: "POST", headers: headers(), body: JSON.stringify(cfg) });
}

// ── v10.0: Data Export/Import ──
export async function exportAllData() { return _fetch("/api/admin/export", { headers: headers() }); }

// V97: 跨会话记忆搜索（Hermes session search）
export async function searchSessions(q: string, limit = 20): Promise<{ ok: boolean; mode: string; results: Array<{ conv_id: string; title: string; snippet: string; role: string; created_at: number }> }> {
  const r = await fetch(`/api/memory/search?q=${encodeURIComponent(q)}&limit=${limit}`, { headers: authHeaders() });
  if (!r.ok) return { ok: false, mode: "error", results: [] };
  return r.json();
}

// ── V204 Session 运维（对标 Qoder Cloud Agents）：运行时补丁 + 诊断助手 ──
export interface SessionRuntime {
  model?: string; temperature?: number; system_append?: string;
  tools_allow?: string[]; tools_deny?: string[]; _updated_at?: number;
}
export async function getSessionRuntime(convId: string): Promise<{ conv_id: string; overrides: SessionRuntime }> {
  return _fetch(`/api/conversations/${convId}/runtime`, { headers: headers() });
}
export async function patchSessionRuntime(convId: string, patch: Record<string, unknown>): Promise<{ ok: boolean; overrides: SessionRuntime }> {
  return _fetch(`/api/conversations/${convId}/runtime`, { method: "PATCH", headers: headers(), body: JSON.stringify(patch) });
}
export async function clearSessionRuntime(convId: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/conversations/${convId}/runtime`, { method: "DELETE", headers: headers() });
}
export interface DiagnoseFinding { type: string; name: string; cause: string; fix: string }
export async function diagnoseConversation(convId: string): Promise<{ ok: boolean; findings: DiagnoseFinding[]; collected: { turns: number; tool_fail: number; errors: number }; report_md: string }> {
  return _fetch(`/api/conversations/${convId}/diagnose`, { method: "POST", headers: headers() });
}

// ── V205 新增能力（P1/P2）：图片库 / 质量隔离区 / 凭据仓 / 派活 ──
export interface ImageItem { id: string; filename: string; caption: string; size: number; created: number; score?: number }
export async function listImages(limit = 50): Promise<{ items: ImageItem[]; count: number; bytes: number }> {
  return _fetch(`/api/images?limit=${limit}`, { headers: headers() });
}
export async function searchImages(q: string, topK = 5): Promise<{ items: ImageItem[] }> {
  return _fetch(`/api/images/search?q=${encodeURIComponent(q)}&top_k=${topK}`, { headers: headers() });
}
export async function deleteImage(id: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/images/${id}`, { method: "DELETE", headers: headers() });
}

export interface QuarantineItem { qid: string; filename: string; score: number; issues: string[]; created: number }
export async function listQuarantine(): Promise<{ items: QuarantineItem[]; count: number; threshold: number }> {
  return _fetch(`/api/kb/quarantine`, { headers: headers() });
}
export async function approveQuarantine(qid: string): Promise<{ ok: boolean; result: unknown }> {
  return _fetch(`/api/kb/quarantine/approve`, { method: "POST", headers: headers(), body: JSON.stringify({ qid }) });
}
export async function rejectQuarantine(qid: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/kb/quarantine/reject`, { method: "POST", headers: headers(), body: JSON.stringify({ qid }) });
}

export interface CredItem { connector: string; masked: string; note: string; created: number; updated: number; last_used: number }
export async function listCredentials(): Promise<{ items: CredItem[]; placeholder: string }> {
  return _fetch(`/api/credentials`, { headers: headers() });
}
export async function setCredential(connector: string, value: string, note = ""): Promise<{ ok: boolean }> {
  return _fetch(`/api/credentials/${encodeURIComponent(connector)}`, { method: "PUT", headers: headers(), body: JSON.stringify({ value, note }) });
}
export async function deleteCredential(connector: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/credentials/${encodeURIComponent(connector)}`, { method: "DELETE", headers: headers() });
}

export interface DispatchTask { task_id: string; runner: string; kind: string; status: string; created: number; done_at?: number; result?: string }
export interface RunnerStatus { name: string; online: boolean; last_seen: number | null; last_claim: number | null; today_done: number; today_failed: number }
export async function listDispatch(limit = 50): Promise<{ items: DispatchTask[]; stats: Record<string, number>; runners?: RunnerStatus[] }> {
  return _fetch(`/api/dispatch?limit=${limit}`, { headers: headers() });
}
export async function createDispatch(runner: string, kind: string, payload: Record<string, unknown>): Promise<{ ok: boolean; task_id: string }> {
  return _fetch(`/api/dispatch`, { method: "POST", headers: headers(), body: JSON.stringify({ runner, kind, payload }) });
}
export async function getDispatch(taskId: string): Promise<DispatchTask> {
  return _fetch(`/api/dispatch/${encodeURIComponent(taskId)}`, { headers: headers() });
}
export async function getRunners(): Promise<{ runners: RunnerStatus[] }> {
  return _fetch(`/api/dispatch/runners`, { headers: headers() });
}

// ── 路线图阶段 A：能力模块开关 ──
export interface AgentModule {
  key: string; title: string; desc: string; enabled: boolean; core: boolean;
  healthy: boolean; wired?: boolean; health_msg: string; tools: string[];
  active_tools?: string[]; missing_tools?: string[];
}
export type RuntimeCapabilityState = "ready" | "setup_required" | "degraded" | "disabled" | "unavailable";
export interface RuntimeCapability {
  id: string; title: string; description: string; state: RuntimeCapabilityState; reason: string;
  enabled: boolean; wired: boolean; requires_desktop: boolean; active_tools: string[];
  missing_tools: string[]; surfaces: Record<string, string>; entrypoints: string[]; tests: string[];
  availability: "available" | "degraded" | "unavailable";
  visibility: "user" | "admin" | "diagnostic";
  diagnostic_only: boolean;
  production_ready: boolean;
  evidence?: { kind?: string; route_or_tool_wiring_checked?: boolean; tests?: string[] };
}
export interface RuntimeCapabilities {
  contract: "hashmm.runtime-capabilities.v1"; revision: string; chat_tool_count: number;
  truth_contract?: "hashmm.capability-truth.v1";
  chat_declared_tool_count?: number; chat_effective_tool_count?: number;
  chat_missing_executors?: string[];
  ready_count: number; available_count?: number; degraded_count?: number;
  unavailable_count?: number; total_count: number; capabilities: RuntimeCapability[];
}
// ── V220: 后端健康/版本探测（高级能力·能力模块顶部档位卡用；老后端无 release 字段则视为待升级）──
export async function getHealth(): Promise<{ status?: string; release?: string; features?: { preset?: string; advanced_on?: string[]; advanced_on_count?: number; advanced_total?: number } }> {
  return _fetch(`/api/health`, { headers: headers() });
}

export async function listModules(): Promise<{ modules: AgentModule[]; note: string }> {
  return _fetch(`/api/modules`, { headers: headers() });
}
export async function runtimeCapabilities(): Promise<RuntimeCapabilities> {
  return _fetch(`/api/runtime/capabilities`, { headers: headers() });
}
export async function getRemoteReadiness(): Promise<{
  schema: "hashmm.remote.readiness.v1"; production_ready: boolean;
  criteria: Record<string, boolean>;
  network_acceptance?: { status?: string; created_at?: number; device_count?: number } | null;
  soak_acceptance?: { status?: string; created_at?: number; duration_seconds?: number } | null;
}> {
  return _fetch(`/api/remote/readiness`, { headers: headers() });
}

export interface RemoteDeviceV2 {
  device_id: string;
  id: string;
  name: string;
  platform: string;
  role: "host" | "viewer" | string;
  app_version: string;
  online: boolean;
  remote_ready: boolean;
  agent_ready: boolean;
  busy: boolean;
  last_seen: number;
  capabilities: string[];
  generation: number;
  device_fingerprint: string;
}
export async function getRemoteDevices(includeOffline = true): Promise<{ protocol: string; owner_fingerprint: string; lease_ttl_seconds: number; devices: RemoteDeviceV2[] }> {
  return _fetch(`/api/remote/v4/devices?include_offline=${includeOffline ? "true" : "false"}`, { headers: headers() });
}

export interface RemoteDiagnosticStep { id: string; state: "ok" | "waiting" | "missing" | "blocked"; detail: string }
export interface RemoteDiagnostics {
  schema: string; protocol: string; owner_fingerprint: string; device_fingerprint: string;
  host_registered: boolean; viewer_registered: boolean; online_devices: number; remote_ready_devices: number;
  presence_backend: string; shared_broker: string; shared_broker_status: string;
  multi_instance_ready: boolean; lease_ttl_seconds: number; steps: RemoteDiagnosticStep[];
  latest_failure?: RemoteConnectionAttempt | null;
  connection_attempts?: RemoteConnectionAttempt[];
  last_attempts: { role: string; attempt_id: string; trace_id?: string; protocol?: string; created_at: number; consumed: boolean; expired: boolean }[];
}
export interface RemoteConnectionAttempt {
  attempt_id: string; trace_id: string; device_id: string; role: string; protocol: string;
  stage: string; error_code: string; close_code: number; detail: string;
  security?: { secure?: boolean; source?: string; asgi_scheme?: string; forwarded_proto?: string; trusted_proxy?: boolean; cloudflare?: boolean };
  created_at: number; updated_at: number; registered_at: number; closed_at: number;
}
export async function getRemoteDiagnostics(deviceId = ""): Promise<RemoteDiagnostics> {
  const suffix = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : "";
  return _fetch(`/api/remote/v4/diagnostics/self${suffix}`, { headers: headers() });
}
export async function toggleModule(key: string, enable: boolean): Promise<{ ok: boolean; key: string; enabled: boolean; hint: string }> {
  return _fetch(`/api/modules/${key}/toggle`, { method: "POST", headers: headers(), body: JSON.stringify({ enable }) });
}

// ── 路线图阶段 D：语音任务编排 ──
export interface VoiceOrchestration {
  action: "clarify" | "local" | "dispatch";
  reason: string;
  clarifying_question?: string;
  dispatch_kind?: string;
  dispatch_payload?: Record<string, unknown>;
  task_id?: string;
  intent?: { goal: string; confidence: number; constraints: string[] };
}
export async function orchestrateVoice(text: string, hasDesktop: boolean, runner?: string): Promise<VoiceOrchestration> {
  return _fetch(`/api/voice/orchestrate`, { method: "POST", headers: headers(), body: JSON.stringify({ text, has_desktop: hasDesktop, runner: runner || "" }) });
}

/* ── V249 记忆中枢（hashmm/memory/hub.py）：四路联邦召回 + 快照 ── */
export async function hubRecall(q: string, limit = 20): Promise<{ ok: boolean; items: { kind: string; source: string; text: string; score: number; ts: number }[] }> {
  return _fetch(`/api/memory/recall?q=${encodeURIComponent(q)}&limit=${limit}`, { headers: headers() });
}
export async function hubRemember(text: string): Promise<{ ok: boolean; id?: string }> {
  return _fetch("/api/memory/hub/remember", { method: "POST", headers: headers(), body: JSON.stringify({ text }) });
}
export async function hubStats(): Promise<{ ok: boolean; service?: { count?: number }; episodic?: number; profile?: number; entities?: number }> {
  return _fetch("/api/memory/hub/stats", { headers: headers() });
}

/* ── V249 模型容灾链（hashmm/llm_failover.py + routes/model_route.py）── */
export async function getModelFallbacks(): Promise<{ ok: boolean; enabled: boolean; primary: { id?: string; name?: string }; fallbacks: { id: string; name: string; exists: boolean }[] }> {
  return _fetch("/api/admin/model-route/fallbacks", { headers: headers() });
}
export async function putModelFallbacks(ids: string[]): Promise<{ ok: boolean; fallbacks: { id: string; name: string }[] }> {
  return _fetch("/api/admin/model-route/fallbacks", { method: "PUT", headers: headers(), body: JSON.stringify({ ids }) });
}
export async function modelsHealth(): Promise<{ ok: boolean; items: { id: string; name: string; state: string; consecutive_fails: number; open_remaining_s: number; ok_count: number; fail_count: number; last_ok_latency_ms: number; last_error: string }[] }> {
  return _fetch("/api/admin/model-route/health", { headers: headers() });
}

/* ── V250 共享链接管理（设置·数据管理）：列本人全部分享 + 撤销 ── */
export async function listMyShares(): Promise<{ items: { share_id: string; url: string; conv_id: string; filename: string; visibility: string; views: number; comments_count: number; created: number }[] }> {
  return _fetch("/api/canvas/shares", { headers: headers() });
}
export async function revokeShare(shareId: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/canvas/shares/${encodeURIComponent(shareId)}`, { method: "DELETE", headers: headers() });
}

/* ── V254 多智能体操作界面（routes/team_ops.py）：预览分工 → 可改 → 启动 → 轮询 ── */
export interface TeamRole { role: string; task: string; state?: "wait" | "run" | "ok" | "fail" | "stop"; finding?: string; err?: string; agent_id?: string; thread_id?: string; parent_thread_id?: string; session_id?: string; scope_id?: string; session_status?: string; tool_calls?: number; execution_receipts?: ExecutionReceipt[]; allowed_tools?: string[]; session_steps?: string[]; created_at?: number; started_at?: number | null; finished_at?: number | null; ms?: number }
export interface TeamEvidence { state: "pending" | "ready" | "empty" | "skipped" | "error"; count: number; detail: string; sources: Source[]; groundings?: GroundingLedger }
export interface TeamTraceItem { id: string; node: string; state: string; detail: string; elapsed_ms?: number }
export interface AgentMeshTask { task_id: string; session_id?: string; parent_task_id?: string; role: string; title: string; status: string; result_hash?: string; upstream_failures?: number; revision?: number }
export interface AgentMeshGraph { schema: "hashmm.agent-mesh.v1"; team_id: string; nodes: AgentMeshTask[]; edges: { from: string; to: string; relation: string }[]; summary: { total: number; active: number; blocked: number; completed: number; failed: number }; graph_hash: string }
export interface AgentSessionTree { schema?: string; root_session_id?: string; nodes?: { session_id: string; parent_session_id?: string; role?: string; task?: string; status?: string; stop_requested?: boolean; tool_calls?: number; depth?: number }[]; edges?: { from: string; to: string; relation: string }[]; truncated?: boolean }
export interface AgentMailboxSummary { schema?: string; team_id?: string; counts?: Record<string, number>; pending?: number; items?: { message_id: string; recipient_session_id: string; sender_kind: string; message_type: string; body_hash: string; status: string; attempts: number; preview?: string; created_at: number }[] }
export interface IndependentVerification { schema?: string; verdict?: "passed" | "failed" | "unknown"; summary?: string; session_id?: string; verified_at?: number; checks?: { name: string; status: string; detail: string }[] }
export interface TeamStatus { team_id: string; goal: string; conv_id: string; file: string; status: "running" | "stopping" | "stopped" | "done" | "failed"; final: string; roles: TeamRole[]; created: number; mode?: "parallel" | "pipeline"; retry_of?: string; stop_requested?: boolean; ok_n?: number; total?: number; root_session_id?: string; agent_mesh?: AgentMeshGraph; agent_session_tree?: AgentSessionTree; mailbox?: AgentMailboxSummary; independent_verification?: IndependentVerification; evidence?: TeamEvidence; evidence_graph?: TaskEvidenceGraph; causal_work_graph?: CausalWorkGraph; execution_receipts?: ExecutionReceipt[]; execution_frontier?: ExecutionFrontier; completion_gate?: CompletionGate; trace?: TeamTraceItem[]; attached_context?: { count?: number; kinds?: string[]; titles?: string[]; total_chars?: number; truncated?: number; dropped?: number; redacted_types?: string[] } }
export async function teamPreview(goal: string): Promise<{ ok: boolean; roles: TeamRole[] }> {
  return _fetch("/api/team/preview", { method: "POST", headers: headers(), body: JSON.stringify({ goal }) });
}
export async function teamStart(goal: string, convId: string, roles?: TeamRole[], mode?: string, featureContexts?: FeatureContext[]): Promise<{ ok: boolean; team_id: string; roles: TeamRole[]; file?: string; retry_of?: string }> {
  return _fetch("/api/team/start", {
    method: "POST", headers: headers(), body: JSON.stringify({
      goal, conv_id: convId, roles, mode,
      feature_contexts: featureContexts?.map(({ kind, title, content, source, document_names }) => ({
        kind, title, content, source, document_names,
      })),
    }),
  });
}
export async function teamStatus(teamId: string): Promise<TeamStatus> {
  return _fetch(`/api/team/status/${encodeURIComponent(teamId)}`, { headers: headers() });
}
export async function teamStop(teamId: string): Promise<{ ok: boolean; team: TeamStatus }> {
  return _fetch(`/api/team/${encodeURIComponent(teamId)}/stop`, { method: "POST", headers: headers() });
}
export async function teamRetry(teamId: string): Promise<{ ok: boolean; team_id: string; roles: TeamRole[]; file?: string; retry_of?: string }> {
  return _fetch(`/api/team/${encodeURIComponent(teamId)}/retry`, { method: "POST", headers: headers() });
}
export async function teamTree(teamId: string): Promise<{ schema: string; team_id: string; root_session_id: string; session_tree: AgentSessionTree; task_graph: AgentMeshGraph; mailbox: AgentMailboxSummary; independent_verification: IndependentVerification }> {
  return _fetch(`/api/team/${encodeURIComponent(teamId)}/tree`, { headers: headers() });
}
export async function teamAgentMessage(teamId: string, sessionId: string, content: string, clientMessageId: string): Promise<{ ok: boolean; mailbox: AgentMailboxSummary }> {
  return _fetch(`/api/team/${encodeURIComponent(teamId)}/agents/${encodeURIComponent(sessionId)}/message`, {
    method: "POST", headers: headers(), body: JSON.stringify({ content, client_message_id: clientMessageId }),
  });
}
export async function teamAgentStop(teamId: string, sessionId: string): Promise<{ ok: boolean; session: { session_id: string; status: string; stop_requested?: boolean } }> {
  return _fetch(`/api/team/${encodeURIComponent(teamId)}/agents/${encodeURIComponent(sessionId)}/stop`, {
    method: "POST", headers: headers(),
  });
}

/* ── V254 总控中枢·全局工作区（routes/workspace_gw.py，理念对标 Anthropic GWT 研究）── */
export interface GwEvent { id: string; seq: number; ts: number; module: string; kind: string; summary: string; salience: number; won?: boolean; conv_id?: string; user?: string }
export interface GwModule { id: string; name: string; desc: string; state: string; detail: string; active: boolean; last_ts: number | null; events: number; won: number }
export interface GwSnapshot { focus: { goal: string; source: string; ts: number; by: string } | null; buffer: GwEvent[]; modules: GwModule[]; history: GwEvent[]; seq: number; ts: number }
export async function gwState(historyN = 60): Promise<GwSnapshot> {
  return _fetch(`/api/gw/state?history_n=${historyN}`, { headers: headers() });
}
export async function gwFocus(goal: string): Promise<{ ok: boolean; focus: GwSnapshot["focus"] }> {
  return _fetch("/api/gw/focus", { method: "POST", headers: headers(), body: JSON.stringify({ goal }) });
}
export async function gwBroadcast(summary: string, module = "chat"): Promise<{ ok: boolean }> {
  return _fetch("/api/gw/broadcast", { method: "POST", headers: headers(), body: JSON.stringify({ summary, module }) });
}
/* V254 深度检索能力自检：面板/开关据此给诚实提示 */
export async function deepSearchStatus(): Promise<{ full_model_dir: boolean; lite: boolean; available: boolean }> {
  return _fetch("/api/deepsearch/status", { headers: headers() });
}

/* ── V255 工作区增强回答（J-lens 产品化）：读出注入 → 任意已配置模型作答 ── */
export async function gwAsk(question: string, modelId?: string): Promise<{ ok: boolean; answer: string; model?: string; context_used?: string; error?: string }> {
  return _fetch("/api/gw/ask", { method: "POST", headers: headers(), body: JSON.stringify({ question, model_id: modelId }) });
}
export async function gwContext(): Promise<{ context: string }> {
  return _fetch("/api/gw/context", { headers: headers() });
}

/* ── V255 智能体工坊：Agent 库 + 智能/手动编队 ── */
export interface AgentInfo { id: string; name: string; skill: string; category?: string; source?: string; license?: string }
export async function teamAgents(): Promise<{ agents: AgentInfo[]; count?: number }> {
  return _fetch("/api/team/agents", { headers: headers() });
}
export async function teamPreviewV2(goal: string, agentIds?: string[]): Promise<{ ok: boolean; roles: TeamRole[]; mode: string }> {
  return _fetch("/api/team/preview", { method: "POST", headers: headers(), body: JSON.stringify({ goal, agent_ids: agentIds }) });
}
export async function teamList(): Promise<{ items: TeamStatus[] }> {
  return _fetch("/api/team/list", { headers: headers() });
}

/* ── V255 文档工坊：文件深度理解与生成 ── */
export interface DocAction { id: string; name: string; desc: string }
export async function docActions(): Promise<{ actions: DocAction[] }> {
  return _fetch("/api/docstudio/actions", { headers: headers() });
}
export interface DocRunResult {
  ok: boolean;
  conv_id: string;
  file: string;
  artifact: { filename: string; download_url: string; kind: string } | null;
  content: string;
  note: string;
}
export async function docRun(p: { conv_id?: string; filename?: string; text?: string; action: string; target?: string; instruction?: string }): Promise<DocRunResult> {
  return _fetch("/api/docstudio/run", { method: "POST", headers: headers(), body: JSON.stringify(p) });
}

/* ── V256 问答智能调度设置 ── */
export async function agentRouteGet(): Promise<{ mode: string }> {
  return _fetch("/api/team/route", { headers: headers() });
}
export async function agentRouteSet(mode: string): Promise<{ ok: boolean; mode: string }> {
  return _fetch("/api/team/route", { method: "POST", headers: headers(), body: JSON.stringify({ mode }) });
}

/* ── V257 事件自动化（事情一发生它就动） ── */
export interface EventRule { id: string; name: string; module: string; on: boolean }
export async function gwRules(): Promise<{ rules: EventRule[] }> {
  return _fetch("/api/gw/rules", { headers: headers() });
}
export async function gwRuleSet(id: string, on: boolean): Promise<{ ok: boolean }> {
  return _fetch("/api/gw/rules", { method: "POST", headers: headers(), body: JSON.stringify({ id, on }) });
}

/* ── V335 可恢复长任务（真实 AgentLoop + 工具证据 + 预算 + 对象权限） ── */
export interface LoopInfo {
  id: string; type: "goal" | "interval"; status: string;
  goal?: string; acceptance?: string; max_rounds?: number; threshold?: number; rounds?: number; score?: number; verified?: boolean;
  prompt?: string; interval_min?: number; max_runs?: number; runs?: number; next_run?: number; last?: string;
  history?: { round?: number; run?: number; score?: number; verified?: boolean; note?: string; tokens?: number; tools?: number; files?: number; ts?: number }[];
  trace?: { kind?: string; node?: string; name?: string; status?: string; detail?: string; elapsed_ms?: number; ts?: number }[];
  tools?: { name?: string; status?: string; elapsed_ms?: number; receipt?: ExecutionReceipt }[];
  execution_receipts?: ExecutionReceipt[];
  context_capsule?: { schema?: string; fingerprint?: string; generation?: number; rendered_chars?: number; sections?: unknown[] };
  files?: { filename?: string; download_url?: string }[];
  result?: string; summary_file?: string; conv_id?: string; created?: number; updated?: number;
  approval_mode?: "read_only" | "workspace"; recovered?: boolean; stop_reason?: string; error?: string;
  work_run_id?: string; work_runtime_state?: "linked" | "degraded" | "not_configured"; work_runtime_error?: string;
  acceptance_confirmation?: { accepted: boolean; note?: string; confirmed_by?: string; confirmed_at?: number };
  tokens_used?: number; max_tokens?: number; token_accounting?: string; active_seconds?: number; max_seconds?: number;
  execution_scope?: {
    schema: "hashmm.execution-scope.v1"; scope_id: string; run_id: string; parent_scope_id?: string;
    conversation_id: string; depth: number; sharing: "private"; memory_scope: "conversation";
    approval_mode: "read_only" | "workspace"; allowed_tools: string[]; allow_subagents: boolean;
    network: { mode: "deny" | "allow" | "allowlist"; origins: string[] };
    budgets: { max_tool_calls: number; max_workers: number };
  };
  task_contract?: {
    schema: string; run_id: string; goal: string; execution_scope_id?: string;
    success_criteria: { check_id: string; label: string; required: boolean; source?: string }[];
  };
  verification?: {
    status: string; failed_required: string[]; not_evaluable: string[]; model_self_report_used: boolean;
    checks: { check_id: string; label: string; required: boolean; status: "passed" | "failed" | "not_evaluable"; detail: string }[];
  };
  evidence_graph?: TaskEvidenceGraph;
  causal_work_graph?: CausalWorkGraph;
  execution_frontier?: ExecutionFrontier;
  completion_gate?: CompletionGate;
}
export async function loopsList(): Promise<{ loops: LoopInfo[] }> {
  return _fetch("/api/loops", { headers: headers() });
}
export async function loopDetail(id: string): Promise<{ loop: LoopInfo }> {
  return _fetch(`/api/loops/${id}`, { headers: headers() });
}
export async function loopGoal(p: { goal: string; acceptance?: string; max_rounds?: number; threshold?: number; conv_id?: string; approval_mode?: "read_only" | "workspace"; max_tokens?: number; max_seconds?: number; network_mode?: "deny" | "allow" | "allowlist"; allowed_origins?: string[]; allow_subagents?: boolean }): Promise<{ ok: boolean; id: string }> {
  return _fetch("/api/loops/goal", { method: "POST", headers: headers(), body: JSON.stringify(p) });
}
export async function loopInterval(p: { prompt: string; interval_min?: number; max_runs?: number; conv_id?: string; approval_mode?: "read_only" | "workspace"; max_tokens?: number; max_seconds?: number; network_mode?: "deny" | "allow" | "allowlist"; allowed_origins?: string[]; allow_subagents?: boolean }): Promise<{ ok: boolean; id: string }> {
  return _fetch("/api/loops/interval", { method: "POST", headers: headers(), body: JSON.stringify(p) });
}
export async function loopPause(id: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/loops/${id}/pause`, { method: "POST", headers: headers() });
}
export async function loopResume(id: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/loops/${id}/resume`, { method: "POST", headers: headers() });
}
export async function loopStop(id: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/loops/${id}/stop`, { method: "POST", headers: headers() });
}
export async function loopAcceptance(id: string, accepted: boolean, note = ""): Promise<{ ok: boolean; loop: LoopInfo }> {
  return _fetch(`/api/loops/${id}/acceptance`, {
    method: "POST", headers: headers(), body: JSON.stringify({ accepted, note }),
  });
}

/* ── V336 仓库计划与证据型变更审查 ── */
export interface RepoPlanStep { n: number; action: string; acceptance: string; side_effect: boolean; files: string[] }
export interface RepoPlanResult { ok: boolean; summary: string; steps: RepoPlanStep[]; requires_confirmation: boolean; model: string; notice: string }
export interface RepoReviewFinding { severity: "P0" | "P1" | "P2" | "P3"; file: string; line: number; title: string; body: string; evidence: string; confidence: number; evidence_validated: boolean }
export interface RepoReviewResult { ok: boolean; summary: string; findings: RepoReviewFinding[]; discarded_findings: number; model: string; scope: string; notice: string }
export async function repoPlan(p: { goal: string; instructions?: string; plan_template?: string; status?: string }): Promise<RepoPlanResult> {
  return _fetch("/api/repo/plan", { method: "POST", headers: headers(), body: JSON.stringify(p) });
}
export async function repoReview(p: { diff: string; instructions?: string; scope?: string }): Promise<RepoReviewResult> {
  return _fetch("/api/repo/review", { method: "POST", headers: headers(), body: JSON.stringify(p) });
}

/* ── V261 我的模型（用户自己的 API） ── */
export interface MyModel {
  id: string; name: string; provider: string; base_url: string;
  model_name: string; api_key: string; has_api_key?: boolean;
  wire_api?: import("./types").ModelWireApi; is_preferred: boolean;
}
export async function myModels(): Promise<{ models: MyModel[]; preferred: string }> {
  return _fetch("/api/models/mine", { headers: headers() });
}
export async function userModelProviders(): Promise<{ providers: import("./types").ProviderSpec[] }> {
  const { normalizeProviderResponse } = await import("./modelApiBoundary");
  return normalizeProviderResponse(await _fetch("/api/models/providers", { headers: headers() }));
}
export async function addMyModel(p: {
  name: string; provider?: string; base_url: string; api_key: string;
  model_name: string; wire_api?: import("./types").ModelWireApi;
  config?: Record<string, unknown>;
}): Promise<{ ok: boolean; id: string }> {
  return _fetch("/api/models/mine", { method: "POST", headers: headers(), body: JSON.stringify(p) });
}
export async function delMyModel(id: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/models/mine/${id}`, { method: "DELETE", headers: headers() });
}
export async function preferMyModel(modelId: string): Promise<{ ok: boolean }> {
  return _fetch("/api/models/mine/prefer", { method: "POST", headers: headers(), body: JSON.stringify({ model_id: modelId }) });
}

/* ── V274 Agent 架构选型顾问（资料 6.1/6.5，纯启发式） ── */
export interface ArchAdvice { ok: boolean; arch?: string; arch_name?: string; reason?: string; n_agents?: number; confidence?: number; metrics?: Record<string, string>; alternatives?: string[]; detail?: string }
export async function archAdvise(task: string, opts?: { baseline?: number; tool_budget_fixed?: boolean; subtask_count?: number }): Promise<ArchAdvice> {
  return _fetch("/api/selftest/arch-advise", { method: "POST", headers: headers(), body: JSON.stringify({ task, ...(opts || {}) }) });
}

/* ── V277 Figma 设计稿导入到画布（需用户提供 Figma Personal Access Token，仅本次使用、不落库） ── */
export async function figmaImport(
  urlOrKey: string, token: string, opts?: { conv_id?: string; page_index?: number }
): Promise<{ ok: boolean; file: string; html: string; note: string }> {
  return _fetch("/api/figma/import", {
    method: "POST", headers: headers(),
    body: JSON.stringify({ url_or_key: urlOrKey, token, ...(opts || {}) }),
  });
}

/* ── V279 上下文透视（对标 /context list：看这轮 system 由哪些块组成、各多少字符） ── */
export async function contextInspect(convId?: string): Promise<{
  ok: boolean; blocks: { id: string; name: string; present: boolean; chars: number; preview: string; note: string }[];
  total_chars: number; tips: string[]; conv_id?: string; conv_title?: string; query?: string; resolved_latest?: boolean;
}> {
  const q = convId ? `?conv_id=${encodeURIComponent(convId)}` : "";
  return _fetch(`/api/context/inspect${q}`, { headers: headers() });
}

/** V373 owner-bound, incremental work state shared by Chat/desktop/App. */
const workCanvasCache = new Map<string, { etag: string; data: WorkCanvas }>();
const workspaceSnapshotCache = new Map<string, { etag: string; data: WorkspaceSnapshotV2 }>();

export type WorkspaceStateV2 =
  | "draft" | "planned" | "ready" | "running" | "waiting_user"
  | "waiting_approval" | "blocked" | "review" | "accepted"
  | "change_requested" | "completed" | "failed" | "cancelled"
  | "interrupted" | "observed";

export interface WorkspaceCriterionV2 {
  id: string;
  label: string;
  required: boolean;
  status: "pending" | "passed" | "failed" | "not_evaluable";
  evidence_refs: string[];
}

export interface WorkspaceRunV2 {
  schema: "hashmm.workspace-run.v2";
  id: string;
  workspace_id: string;
  thread_id: string;
  kind: string;
  state: WorkspaceStateV2;
  legacy_status: string;
  revision: number;
  change_cursor: number;
  title: string;
  contract: {
    schema: "hashmm.outcome-contract.v2";
    goal: string;
    deliverable: string;
    criteria: WorkspaceCriterionV2[];
    permission_mode: string;
    source: string;
    user_confirmed: boolean;
  };
  current_step: string;
  next_action: string;
  needs_user: boolean;
  progress: Record<string, unknown>;
  evidence: {
    schema: "hashmm.evidence-graph.v2";
    nodes: Array<Record<string, unknown>>;
    edges: Array<Record<string, unknown>>;
    summary: { evidence_count: number; relation_count: number; authoritative: boolean };
  };
  artifacts: Array<{
    id: string;
    name: string;
    media_type: string;
    revision: number;
    verification: string;
    content_hash: string;
    locator: Record<string, unknown>;
  }>;
  available_transitions: string[];
  available_commands: string[];
  execution_target: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

export interface WorkspaceSnapshotV2 {
  schema: "hashmm.workspace.v2";
  generated_at: number;
  etag: string;
  workspace: {
    id: string;
    kind: "personal" | "project";
    name: string;
    goal: string;
    deliverable: string;
    success_criteria: string[];
    permission_mode: string;
    revision: number;
    updated_at?: number;
    conversation_count?: number;
  };
  projects: Array<{
    id: string;
    name: string;
    goal: string;
    deliverable: string;
    success_criteria: string[];
    permission_mode: string;
    revision: number;
    conversation_count: number;
    updated_at: number;
  }>;
  today: {
    needs_user: WorkspaceRunV2[];
    in_progress: WorkspaceRunV2[];
    recent_results: WorkspaceRunV2[];
  };
  runs: WorkspaceRunV2[];
  devices: {
    state: "ready" | "unknown";
    items: Array<{
      id: string;
      name: string;
      online: boolean;
      last_seen: number;
      runner: string;
      version: string;
    }>;
    online_count: number;
    authoritative: boolean;
    reason?: string;
  };
  sync: {
    after_cursor: number;
    next_cursor: number;
    high_water_cursor: number;
    has_more: boolean;
    delta: boolean;
  };
  trust: {
    owner_isolation: true;
    model_prose_is_execution_evidence: false;
    side_effects_require_policy: true;
    completion_requires_evidence: true;
    role: string;
  };
}

export interface WorkProject {
  id: string; user_id: string; name: string; description: string;
  custom_prompt?: string;
  goal: string;
  deliverable: string;
  success_criteria: string[];
  permission_mode: "ask" | "read_only" | "trusted_workspace";
  status: "active" | "paused" | "completed";
  archived: boolean;
  revision: number;
  conv_count: number;
  created_at?: number;
  updated_at?: number;
}

export async function listWorkProjects(): Promise<WorkProject[]> {
  const result = await _fetch("/api/projects", { headers: headers() }) as { projects?: WorkProject[] };
  return Array.isArray(result.projects) ? result.projects : [];
}

export async function listKnowledgeDocuments(): Promise<{
  documents: Array<Record<string, unknown>>;
  error?: string;
}> {
  return _fetch("/api/kb/documents", { headers: headers() });
}

export interface SearchIntegration {
  provider: string;
  enabled: boolean;
  configured: boolean;
  masked_api_key: string;
  config: {
    version?: "global" | "custom";
    base_url: string;
    snippet_length?: number;
    count: number;
    auth_level?: number | null;
    model?: string;
  };
  updated_at: number;
  notice?: string;
}

export async function listSearchIntegrations(): Promise<{
  providers: Array<{ provider: string; label: string; kind: string; integration: SearchIntegration | null }>;
}> {
  return _fetch("/api/integrations/search", { headers: headers() });
}

export async function getSearchIntegration(provider = "doubao"): Promise<SearchIntegration> {
  return _fetch(`/api/integrations/search/${encodeURIComponent(provider)}`, { headers: headers() });
}

export async function saveSearchIntegration(
  provider: string,
  input: { api_key?: string; enabled: boolean; config: SearchIntegration["config"] },
): Promise<{ ok: boolean; integration: SearchIntegration }> {
  return _fetch(`/api/integrations/search/${encodeURIComponent(provider)}`, {
    method: "PUT",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function removeSearchIntegration(provider = "doubao"): Promise<{ ok: boolean }> {
  return _fetch(`/api/integrations/search/${encodeURIComponent(provider)}`, {
    method: "DELETE",
    headers: headers(),
  });
}

export async function testSearchIntegration(provider = "doubao"): Promise<{
  ok: boolean;
  provider: string;
  result_count: number;
  latency_ms: number;
}> {
  return _fetch(`/api/integrations/search/${encodeURIComponent(provider)}/test`, {
    method: "POST",
    headers: headers(),
  });
}

export interface OkfPack {
  id: string;
  name: string;
  description: string;
  source_name: string;
  concept_count: number;
  warning_count: number;
  created_at: number;
  updated_at: number;
}

export interface OkfPreview {
  preview_id: string;
  name: string;
  description: string;
  source_name: string;
  concept_count: number;
  warning_count: number;
  warnings: Array<{ code: string; path: string; message: string }>;
  trust: { "human-reviewed": number; "machine-confirmed": number; unverified: number };
  types: string[];
  concepts: Array<{
    path: string; type: string; title: string; trust: string;
    status: string; stale_after: string; link_count: number;
  }>;
  expires_at: number;
  notice: string;
}

export async function listOkfPacks(): Promise<{ packs: OkfPack[] }> {
  return _fetch("/api/okf/packs", { headers: headers() });
}

export async function previewOkfPack(file: File): Promise<{ ok: boolean; preview: OkfPreview }> {
  await ensureFreshToken();
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/okf/preview", {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error((body as { detail?: string }).detail || `HTTP ${response.status}`);
  return body;
}

export async function applyOkfPack(previewId: string): Promise<{
  ok: boolean;
  pack: { id: string; name: string; concept_count: number; warning_count: number; indexed_chunks: number };
}> {
  return _fetch(`/api/okf/apply/${encodeURIComponent(previewId)}`, {
    method: "POST",
    headers: headers(),
  });
}

export function okfExportUrl(packId: string): string {
  return withToken(`/api/okf/packs/${encodeURIComponent(packId)}/export`);
}

export async function createWorkProject(input: {
  name: string;
  description?: string;
  goal?: string;
  deliverable?: string;
  success_criteria?: string[];
  permission_mode?: WorkProject["permission_mode"];
}): Promise<{ ok: boolean; id: string; project: WorkProject }> {
  return _fetch("/api/projects", {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function updateWorkProject(
  projectId: string,
  patch: Partial<Pick<WorkProject, "name" | "description" | "goal" | "deliverable" | "success_criteria" | "permission_mode" | "status" | "archived">>,
): Promise<{ ok: boolean; project: WorkProject }> {
  return _fetch(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export interface ProjectResourceMembership {
  resource_id: string;
  conv_id: string;
  filename: string;
  sha256: string;
  role: string;
  created_at: number;
}

export async function listProjectResources(projectId: string): Promise<ProjectResourceMembership[]> {
  const result = await _fetch(`/api/projects/${encodeURIComponent(projectId)}/resources`, { headers: headers() }) as { items?: ProjectResourceMembership[] };
  return Array.isArray(result.items) ? result.items : [];
}

export async function addProjectResource(
  projectId: string,
  input: { conversation_id: string; filename: string; role?: string },
): Promise<{ ok: boolean; membership: ProjectResourceMembership; resource: Record<string, unknown> }> {
  return _fetch(`/api/projects/${encodeURIComponent(projectId)}/resources`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function removeProjectResource(projectId: string, resourceId: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/projects/${encodeURIComponent(projectId)}/resources/${encodeURIComponent(resourceId)}`, {
    method: "DELETE", headers: headers(),
  });
}

export interface UserRoutine {
  id: string;
  name: string;
  action: string;
  schedule_kind: "interval" | "daily";
  interval_seconds: number;
  daily_at: string;
  enabled: boolean;
  last_run: number;
  next_run: number;
  last_status: string;
  last_result: string;
  run_count: number;
  conversation_id: string;
  timezone: string;
  result_destination: "conversation" | "work_ledger";
  permission_mode: "read_only";
  created_at: number;
  last_work_run_id: string;
  last_work_status: string;
  resumable: boolean;
  next_actions: string[];
  verification_status: string;
}

export async function listUserRoutines(): Promise<{
  schema: "hashmm.user-routines.v1";
  available: boolean;
  actions: Array<{ id: string; name: string; description: string }>;
  items: UserRoutine[];
}> {
  return _fetch("/api/user-work/routines", { headers: headers() });
}

export async function createUserRoutine(input: {
  action: string;
  name: string;
  schedule_kind: "interval" | "daily";
  interval_seconds?: number;
  daily_at?: string;
  conversation_id?: string;
  timezone: string;
  result_destination: "conversation" | "work_ledger";
}): Promise<{ ok: boolean; task: UserRoutine }> {
  return _fetch("/api/user-work/routines", {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function updateUserRoutine(id: string, input: {
  name?: string;
  schedule_kind?: "interval" | "daily";
  interval_seconds?: number;
  daily_at?: string;
  conversation_id?: string;
  timezone?: string;
  result_destination?: "conversation" | "work_ledger";
}): Promise<{ ok: boolean; task: UserRoutine }> {
  return _fetch(`/api/user-work/routines/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export async function runUserRoutine(id: string): Promise<{
  ok: boolean;
  status: string;
  result: string;
  next_run: number;
  work_run_id: string;
  resumable: boolean;
}> {
  return _fetch(`/api/user-work/routines/${encodeURIComponent(id)}/run`, {
    method: "POST", headers: headers(),
  });
}

export async function toggleUserRoutine(id: string, enabled: boolean): Promise<{ ok: boolean }> {
  return _fetch(`/api/user-work/routines/${encodeURIComponent(id)}/toggle`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export async function deleteUserRoutine(id: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/user-work/routines/${encodeURIComponent(id)}`, {
    method: "DELETE", headers: headers(),
  });
}

export interface UserWorkSearchItem {
  kind: "project" | "conversation" | "work";
  id: string;
  title: string;
  snippet: string;
  updated_at: number;
}

export interface UserWorkOverview {
  schema: "hashmm.user-workspace.v1";
  generated_at: number;
  selected_project_id: string;
  projects: WorkProject[];
  work: WorkFeed;
  routines: {
    schema: "hashmm.user-routines.v1";
    available: boolean;
    actions: Array<{ id: string; name: string; description: string }>;
    items: UserRoutine[];
  };
  continuity: {
    online_devices: number;
    cross_device: boolean;
    supported: boolean;
    state: "ready" | "waiting_device";
    authoritative_source: "server";
  };
  trust: {
    schema: "hashmm.user-trust.v1";
    account: "connected";
    role: string;
    owner_isolation: boolean;
    approval_policy: "runtime_scoped";
    completion_requires_evidence: boolean;
  };
}

export async function getUserWorkOverview(projectId = ""): Promise<UserWorkOverview> {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  const suffix = params.size ? `?${params}` : "";
  return _fetch(`/api/user-work/overview${suffix}`, { headers: headers() });
}

export async function searchUserWork(query: string, limit = 30): Promise<{
  schema: "hashmm.user-search.v1";
  query: string;
  items: UserWorkSearchItem[];
}> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return _fetch(`/api/user-work/search?${params}`, { headers: headers() });
}

export async function assignConversationProject(conversationId: string, projectId: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/conversations/${encodeURIComponent(conversationId)}/assign-project`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId || null }),
  });
}

async function workCanvasCacheNamespace(): Promise<string> {
  const secret = useStore.getState().refreshToken || useStore.getState().token || "";
  if (!secret || typeof crypto === "undefined" || !crypto.subtle) return "";
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(secret));
  return Array.from(new Uint8Array(bytes), value => value.toString(16).padStart(2, "0")).join("");
}

function validCachedWorkCanvas(runId: string, value: unknown): value is WorkCanvas {
  const data = value as WorkCanvas | null;
  return !!(
    data && data.schema === "hashmm.work-canvas.v1" && data.run_id === runId
    && data.assurance?.schema === "hashmm.work-assurance.v1"
    && data.integrity?.projection_only === true
    && data.integrity?.auto_executes === false
    && data.integrity?.widens_scope === false
  );
}

function validCachedWorkspaceSnapshot(
  workspaceId: string,
  value: unknown,
): value is WorkspaceSnapshotV2 {
  const data = value as WorkspaceSnapshotV2 | null;
  return !!(
    data
    && data.schema === "hashmm.workspace.v2"
    && data.workspace?.id === workspaceId
    && data.trust?.owner_isolation === true
    && data.trust?.model_prose_is_execution_evidence === false
    && Array.isArray(data.runs)
    && Number.isFinite(Number(data.sync?.high_water_cursor))
  );
}

/**
 * Read the user workspace through the same owner-bound, encrypted cache used
 * by the work canvas. Cached data is accepted only after schema/trust checks;
 * online reads are conditionally revalidated with ETag, while a verified cache
 * remains available when the backend is temporarily offline.
 */
export async function getWorkspaceSnapshot(
  workspaceId = "personal",
  force = false,
): Promise<WorkspaceSnapshotV2> {
  const uid = useStore.getState().user?.id || useStore.getState().user?.username || "anonymous";
  const cacheId = `workspace:${workspaceId}`;
  const key = `${uid}:${cacheId}`;
  await ensureFreshToken();
  const namespace = await workCanvasCacheNamespace();
  const bridge = getDesktop();
  let cached = workspaceSnapshotCache.get(key);
  if (!cached && namespace && bridge?.workCanvasCacheGet) {
    try {
      const disk = await bridge.workCanvasCacheGet(namespace, cacheId);
      if (disk?.hit && validCachedWorkspaceSnapshot(workspaceId, disk.data)) {
        cached = { etag: String(disk.etag || ""), data: disk.data };
        workspaceSnapshotCache.set(key, cached);
      }
    } catch { /* an authenticated server read remains authoritative */ }
  }
  const requestHeaders: Record<string, string> = { ...headers() };
  if (cached && !force) requestHeaders["If-None-Match"] = cached.etag;
  let response: Response;
  try {
    response = await fetch(
      `/api/v2/workspaces/${encodeURIComponent(workspaceId)}/snapshot`,
      { headers: requestHeaders, cache: "no-store" },
    );
  } catch {
    if (cached) return cached.data;
    throw _offlineError();
  }
  if (response.status === 401) {
    const token = await _doRefresh();
    if (token && token !== REFRESH_UNAVAILABLE) {
      response = await fetch(
        `/api/v2/workspaces/${encodeURIComponent(workspaceId)}/snapshot`,
        {
          headers: { ...requestHeaders, Authorization: `Bearer ${token}` },
          cache: "no-store",
        },
      );
    } else if (token === REFRESH_UNAVAILABLE && cached) {
      return cached.data;
    }
  }
  if (response.status === 304 && cached) return cached.data;
  if (!response.ok) {
    let message = `工作空间读取失败（${response.status}）`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) message = body.detail;
    } catch { /* keep the bounded status message */ }
    throw new Error(message);
  }
  const data = await response.json() as WorkspaceSnapshotV2;
  if (!validCachedWorkspaceSnapshot(workspaceId, data)) {
    throw new Error("服务器返回了不兼容的工作空间");
  }
  const etag = response.headers.get("ETag") || `"workspace-v2-${data.etag || ""}"`;
  workspaceSnapshotCache.set(key, { etag, data });
  if (namespace && bridge?.workCanvasCachePut) {
    try {
      await bridge.workCanvasCachePut(namespace, cacheId, etag, data);
    } catch { /* memory cache remains valid */ }
  }
  return data;
}

export async function createWorkspaceRun(input: {
  workspaceId?: string;
  idempotencyKey?: string;
  goal: string;
  deliverable?: string;
  successCriteria?: string[];
  permissionMode?: string;
  conversationId?: string;
  kind?: "chat" | "loop" | "team" | "browser" | "computer" | "remote" | "artifact" | "workflow";
}): Promise<{ ok: boolean; run: WorkspaceRunV2 }> {
  const workspaceId = input.workspaceId || "personal";
  const idempotencyKey = input.idempotencyKey
    || globalThis.crypto?.randomUUID?.()
    || `workspace-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const result = await _fetch(
    `/api/v2/workspaces/${encodeURIComponent(workspaceId)}/runs`,
    {
      method: "POST",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify({
        idempotency_key: idempotencyKey,
        goal: input.goal,
        deliverable: input.deliverable || "",
        success_criteria: input.successCriteria || [],
        permission_mode: input.permissionMode || "ask",
        conversation_id: input.conversationId || "",
        kind: input.kind || "workflow",
      }),
    },
  ) as { ok: boolean; run: WorkspaceRunV2 };
  workspaceSnapshotCache.clear();
  return result;
}

export type WorkspaceRuntimeProvider = {
  id: string;
  label: string;
  protocol: string;
  configured: boolean;
  enabled: boolean;
  preview: boolean;
  endpoint: string;
  problems: string[];
  capabilities: string[];
  reachable?: boolean;
  ready?: boolean;
  upstream?: string;
};

export async function workspaceRuntimeProviders(): Promise<{
  protocol: string;
  providers: WorkspaceRuntimeProvider[];
}> {
  return _fetch("/api/v3/runtime/providers", { headers: headers() });
}

export async function cloudflareComputerHealth(): Promise<WorkspaceRuntimeProvider> {
  return _fetch("/api/v3/runtime/providers/cloudflare-computer/health", { headers: headers() });
}

export async function workspaceRunCommand(
  workspaceId: string,
  runId: string,
  input: {
    action: string;
    expectedRevision: number;
    note?: string;
    commandId?: string;
  },
): Promise<{ ok: boolean; duplicate?: boolean; run?: WorkspaceRunV2; error?: string }> {
  const commandId = input.commandId
    || globalThis.crypto?.randomUUID?.()
    || `workspace-command-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const result = await _fetch(
    `/api/v2/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/commands`,
    {
      method: "POST",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify({
        command_id: commandId,
        action: input.action,
        expected_revision: input.expectedRevision,
        note: input.note || "",
      }),
    },
  ) as { ok: boolean; duplicate?: boolean; run?: WorkspaceRunV2; error?: string };
  workspaceSnapshotCache.clear();
  return result;
}

export function subscribeWorkspaceSnapshot(opts: {
  workspaceId?: string;
  afterCursor?: number;
  onSnapshot: (snapshot: WorkspaceSnapshotV2) => void;
  onState?: (state: "connecting" | "open" | "fallback") => void;
}): () => void {
  const controller = new AbortController();
  const workspaceId = opts.workspaceId || "personal";
  let cursor = Math.max(0, opts.afterCursor || 0);
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  const connect = async () => {
    if (controller.signal.aborted) return;
    opts.onState?.("connecting");
    await ensureFreshToken();
    const q = new URLSearchParams({ after_cursor: String(cursor) });
    try {
      let response = await fetch(
        `/api/v2/workspaces/${encodeURIComponent(workspaceId)}/stream?${q}`,
        {
          headers: { ...authHeaders(), Accept: "text/event-stream" },
          cache: "no-store",
          signal: controller.signal,
        },
      );
      if (response.status === 401) {
        const token = await _doRefresh();
        if (token && token !== REFRESH_UNAVAILABLE) {
          response = await fetch(
            `/api/v2/workspaces/${encodeURIComponent(workspaceId)}/stream?${q}`,
            {
              headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
              cache: "no-store",
              signal: controller.signal,
            },
          );
        }
      }
      if (!response.ok || !response.body) throw new Error(`workspace stream ${response.status}`);
      opts.onState?.("open");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!controller.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = block.split("\n")
            .filter(line => line.startsWith("data:"))
            .map(line => line.slice(5).trimStart()).join("\n");
          if (data) {
            try {
              const snapshot = JSON.parse(data) as WorkspaceSnapshotV2;
              if (validCachedWorkspaceSnapshot(workspaceId, snapshot)) {
                cursor = Math.max(cursor, Number(snapshot.sync.next_cursor || 0));
                opts.onSnapshot(snapshot);
              }
            } catch { /* never acknowledge an invalid or incomplete event */ }
          }
          boundary = buffer.indexOf("\n\n");
        }
      }
    } catch (error) {
      if (controller.signal.aborted || (error as Error)?.name === "AbortError") return;
    }
    if (!controller.signal.aborted) {
      opts.onState?.("fallback");
      retryTimer = setTimeout(connect, 3_000);
    }
  };
  void connect();
  return () => {
    controller.abort();
    if (retryTimer) clearTimeout(retryTimer);
  };
}

async function removePersistedWorkCanvas(runId: string): Promise<void> {
  try {
    const namespace = await workCanvasCacheNamespace();
    const bridge = getDesktop();
    if (namespace && bridge?.workCanvasCacheRemove) {
      await bridge.workCanvasCacheRemove(namespace, runId);
    }
  } catch { /* encrypted cache cleanup is best effort */ }
}

export async function workRunsFeed(
  afterCursor = 0,
  convId = "",
  opts?: { activeOnly?: boolean; limit?: number; projectId?: string },
): Promise<WorkFeed> {
  const q = new URLSearchParams({ after_cursor: String(Math.max(0, afterCursor)), limit: String(opts?.limit || 100) });
  if (convId) q.set("conversation_id", convId);
  if (opts?.projectId) q.set("project_id", opts.projectId);
  if (opts?.activeOnly) q.set("active_only", "1");
  return _fetch(`/api/work-runs?${q.toString()}`, { headers: headers() });
}

export type OcrJob = {
  schema: "hashmm.ocr-job.v2" | string;
  id: string;
  resource_id: string;
  resource_revision: number;
  filename: string;
  sha256: string;
  engine_requested: string;
  engine_resolved: string;
  lang: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | string;
  result_state?: "ready" | "partial" | string;
  priority: number;
  total_pages: number;
  processed_pages: number;
  failed_pages: number;
  progress: number;
  attempts: number;
  max_attempts: number;
  next_attempt_at: number;
  error_code?: string;
  error?: string;
  cancel_requested?: boolean;
  created_at: number;
  updated_at: number;
};

export async function ocrJobs(conversationId?: string): Promise<{ schema: string; items: OcrJob[] }> {
  const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
  return _fetch(`/api/ocr-jobs${q}`, { headers: headers() });
}

export async function retryOcrJob(jobId: string): Promise<OcrJob> {
  return _fetch(`/api/ocr-jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST", headers: { ...headers(), "Content-Type": "application/json" }, body: "{}",
  });
}

export async function cancelOcrJob(jobId: string): Promise<OcrJob> {
  return _fetch(`/api/ocr-jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST", headers: { ...headers(), "Content-Type": "application/json" }, body: "{}",
  });
}

export async function ocrCapabilities(probe = false): Promise<Record<string, unknown>> {
  return _fetch(`/api/ocr-jobs/capabilities${probe ? "?probe=true" : ""}`, { headers: headers() });
}

/** Owner-authenticated work push stream.
 *
 * EventSource cannot attach a Bearer header, so the access token is never put
 * in a URL. This small fetch-stream client keeps the existing cursor/ETag API
 * as the recovery path and reconnects from the last acknowledged cursor.
 */
export function subscribeWorkFeed(opts: {
  afterCursor?: number;
  projectId?: string;
  onFeed: (feed: WorkFeed) => void;
  onState?: (state: "connecting" | "open" | "fallback") => void;
}): () => void {
  const controller = new AbortController();
  let cursor = Math.max(0, opts.afterCursor || 0);
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const connect = async () => {
    if (controller.signal.aborted) return;
    opts.onState?.("connecting");
    await ensureFreshToken();
    const q = new URLSearchParams({ after_cursor: String(cursor) });
    if (opts.projectId) q.set("project_id", opts.projectId);
    let response: Response;
    try {
      response = await fetch(`/api/work-runs/stream?${q.toString()}`, {
        headers: { ...authHeaders(), Accept: "text/event-stream" },
        cache: "no-store",
        signal: controller.signal,
      });
      if (response.status === 401) {
        const token = await _doRefresh();
        if (token && token !== REFRESH_UNAVAILABLE) {
          response = await fetch(`/api/work-runs/stream?${q.toString()}`, {
            headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
            cache: "no-store",
            signal: controller.signal,
          });
        }
      }
      if (!response.ok || !response.body) throw new Error(`work stream ${response.status}`);
      opts.onState?.("open");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!controller.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = block.split("\n")
            .filter(line => line.startsWith("data:"))
            .map(line => line.slice(5).trimStart())
            .join("\n");
          if (data) {
            try {
              const feed = JSON.parse(data) as WorkFeed;
              if (feed?.schema === "hashmm.work-feed.v1") {
                cursor = Math.max(cursor, Number(feed.next_cursor || 0));
                opts.onFeed(feed);
              }
            } catch { /* an incomplete/invalid event is never acknowledged */ }
          }
          boundary = buffer.indexOf("\n\n");
        }
      }
    } catch (error) {
      if (controller.signal.aborted || (error as Error)?.name === "AbortError") return;
    }
    if (!controller.signal.aborted) {
      opts.onState?.("fallback");
      retryTimer = setTimeout(connect, 3_000);
    }
  };
  void connect();
  return () => {
    controller.abort();
    if (retryTimer) clearTimeout(retryTimer);
  };
}

export async function workRunDetail(runId: string, afterSeq = 0): Promise<WorkRun> {
  return _fetch(`/api/work-runs/${encodeURIComponent(runId)}?after_seq=${Math.max(0, afterSeq)}`, { headers: headers() });
}

export async function workRunLease(
  runId: string,
  body: Record<string, unknown>,
): Promise<{ ok: boolean; duplicate?: boolean; error?: string; current_revision?: number; lease?: WorkExecutionLease }> {
  return _fetch(`/api/work-runs/${encodeURIComponent(runId)}/lease`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function workExecutionDevices(): Promise<{
  schema: "hashmm.execution-devices.v1";
  items: WorkExecutionDevice[];
  online_count: number;
  server_time: number;
}> {
  return _fetch("/api/work-runs/devices", { headers: headers() });
}

export async function workRunPlacement(
  runId: string,
  deviceId: string,
  expectedRevision: number,
): Promise<{ ok: boolean; error?: string; current_revision?: number }> {
  const result = await _fetch(`/api/work-runs/${encodeURIComponent(runId)}/placement`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({
      device_id: deviceId,
      expected_revision: expectedRevision,
    }),
  }) as { ok: boolean; error?: string; current_revision?: number };
  const uid = useStore.getState().user?.id || useStore.getState().user?.username || "anonymous";
  workCanvasCache.delete(`${uid}:${runId}`);
  await removePersistedWorkCanvas(runId);
  return result;
}

export async function workWorkflowCandidate(
  runId: string,
  name: string,
): Promise<{ ok: boolean; error?: string; workflow?: { id: string; status: string; revision: number } }> {
  return _fetch("/api/work-runs/workflows", {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({
      source_run_id: runId,
      name,
      idempotency_key: `workflow:${runId}`,
    }),
  });
}

export async function workWorkflowPublish(
  workflowId: string,
  expectedRevision: number,
): Promise<{ ok: boolean; error?: string; workflow?: { id: string; status: string; revision: number } }> {
  return _fetch(`/api/work-runs/workflows/${encodeURIComponent(workflowId)}/publish`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      expected_revision: expectedRevision,
      approved: true,
      parameters: {},
    }),
  });
}

export async function workRunAnnotation(
  runId: string,
  body: {
    annotation_id: string; artifact_id: string; artifact_revision: number;
    target: Record<string, unknown>; note: string; expected_revision: number;
    idempotency_key: string;
  },
): Promise<{ ok: boolean; duplicate?: boolean; error?: string; current_revision?: number; annotation?: WorkArtifactAnnotation }> {
  const result = await _fetch(`/api/work-runs/${encodeURIComponent(runId)}/annotations`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }) as { ok: boolean; duplicate?: boolean; error?: string; current_revision?: number; annotation?: WorkArtifactAnnotation };
  const uid = useStore.getState().user?.id || useStore.getState().user?.username || "anonymous";
  workCanvasCache.delete(`${uid}:${runId}`);
  await removePersistedWorkCanvas(runId);
  return result;
}

/** V397/V399 unified work canvas with private ETag revalidation.
 *
 * The cached body is reused only after the owner-authenticated server returns
 * 304. A different account receives a different cache key and cannot inherit
 * another user's in-memory work projection.
 */
export async function workRunWorkspace(runId: string, force = false): Promise<WorkCanvas> {
  const uid = useStore.getState().user?.id || useStore.getState().user?.username || "anonymous";
  const key = `${uid}:${runId}`;
  await ensureFreshToken();
  const namespace = await workCanvasCacheNamespace();
  const bridge = getDesktop();
  let cached = workCanvasCache.get(key);
  if (!cached && namespace && bridge?.workCanvasCacheGet) {
    try {
      const disk = await bridge.workCanvasCacheGet(namespace, runId);
      if (disk?.hit && validCachedWorkCanvas(runId, disk.data)) {
        cached = { etag: String(disk.etag || ""), data: disk.data };
        workCanvasCache.set(key, cached);
      }
    } catch { /* fall through to the owner-authenticated server */ }
  }
  const requestHeaders: Record<string, string> = { ...headers() };
  if (cached && !force) requestHeaders["If-None-Match"] = cached.etag;
  let response: Response;
  try {
    response = await fetch(`/api/work-runs/${encodeURIComponent(runId)}/workspace`, {
      headers: requestHeaders,
      cache: "no-store",
    });
  } catch {
    if (cached) return cached.data;
    throw _offlineError();
  }
  if (response.status === 401) {
    const token = await _doRefresh();
    if (token && token !== REFRESH_UNAVAILABLE) {
      response = await fetch(`/api/work-runs/${encodeURIComponent(runId)}/workspace`, {
        headers: { ...requestHeaders, Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
    } else if (token === REFRESH_UNAVAILABLE && cached) {
      return cached.data;
    }
  }
  if (response.status === 304 && cached) return cached.data;
  if (!response.ok) {
    let message = `工作画布读取失败（${response.status}）`;
    try {
      const body = await response.json() as { detail?: string };
      if (body.detail) message = body.detail;
    } catch { /* keep bounded status message */ }
    throw new Error(message);
  }
  const data = await response.json() as WorkCanvas;
  if (data?.schema !== "hashmm.work-canvas.v1") throw new Error("服务器返回了不兼容的工作画布");
  const etag = response.headers.get("ETag") || `"${data.sync?.etag || ""}"`;
  workCanvasCache.set(key, { etag, data });
  if (namespace && bridge?.workCanvasCachePut && validCachedWorkCanvas(runId, data)) {
    try { await bridge.workCanvasCachePut(namespace, runId, etag, data); } catch { /* memory cache remains valid */ }
  }
  return data;
}

export async function workRunCommand(
  runId: string,
  action: WorkControlAction,
  expectedRevision: number,
  commandId = globalThis.crypto?.randomUUID?.() || `cmd-${Date.now()}-${Math.random().toString(16).slice(2)}`,
): Promise<WorkCommandResponse> {
  const request = () => _fetch(`/api/work-runs/${encodeURIComponent(runId)}/commands`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({ command_id: commandId, action, expected_revision: expectedRevision }),
  }) as Promise<WorkCommandResponse>;
  let result: WorkCommandResponse;
  try {
    result = await request();
  } catch (error) {
    const uid = useStore.getState().user?.id || useStore.getState().user?.username || "";
    if ((error as Error & { offline?: boolean })?.offline && uid) {
      enqueueWorkCommand({
        commandId,
        runId,
        ownerId: uid,
        action,
        expectedRevision,
      });
    }
    throw error;
  }
  const uid = useStore.getState().user?.id || useStore.getState().user?.username || "anonymous";
  workCanvasCache.delete(`${uid}:${runId}`);
  await removePersistedWorkCanvas(runId);
  return result;
}

/** Flush only commands belonging to the current authenticated owner. */
export async function flushWorkCommandOutbox(): Promise<WorkOutboxDrainResult> {
  const uid = useStore.getState().user?.id || useStore.getState().user?.username || "";
  if (!uid) {
    return { schema: "hashmm.work-outbox.v1", sent: 0, pending: 0, stoppedOnNetworkError: false };
  }
  return drainWorkOutbox(uid, async (entry: WorkOutboxEntry) => {
    const result = await _fetch(`/api/work-runs/${encodeURIComponent(entry.runId)}/commands`, {
      method: "POST",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify({
        command_id: entry.commandId,
        action: entry.action,
        expected_revision: entry.expectedRevision,
      }),
    }) as WorkCommandResponse;
    return { ok: result.ok, duplicate: result.duplicate };
  });
}

export async function workRunDecision(
  runId: string,
  action: "accept_delivery" | "request_changes",
  expectedRevision: number,
  note = "",
  decisionId = globalThis.crypto?.randomUUID?.() || `decision-${Date.now()}-${Math.random().toString(16).slice(2)}`,
): Promise<WorkDecisionResponse> {
  const result = await _fetch(`/api/work-runs/${encodeURIComponent(runId)}/decisions`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify({
      decision_id: decisionId,
      action,
      expected_revision: expectedRevision,
      note,
    }),
  }) as WorkDecisionResponse;
  const uid = useStore.getState().user?.id || useStore.getState().user?.username || "anonymous";
  workCanvasCache.delete(`${uid}:${runId}`);
  await removePersistedWorkCanvas(runId);
  return result;
}

export async function compactConversation(convId: string): Promise<{
  ok: boolean; compacted: boolean; reason?: string;
  state: { source_messages: number; estimated_tokens: number; compaction_count: number; last_trigger: string; updated_at: number };
}> {
  return _fetch(`/api/conversations/${encodeURIComponent(convId)}/compact`, {
    method: "POST", headers: headers(),
  });
}

/* ── V272 会话运行时补丁（/persona 专家人格等） ── */
export async function sessionPatch(convId: string, patch: { system_append?: string; temperature?: number; model?: string }) {
  return _fetch(`/api/conversations/${convId}/runtime`, { method: "PATCH", headers: headers(), body: JSON.stringify(patch) });
}

/* ── V272 测试中枢：分组自测（真实调用各功能入口） ── */
export interface SelfTestSuite { id: string; name: string; group: string; slow: boolean }
export interface SelfTestRunDetail { run: number; passed: boolean; score: number; failure_mode?: string; gist?: string }
export interface SelfTestCase { name: string; passed: boolean; score: number; detail: string; skipped: boolean; runs?: number; passes?: number; pass_rate?: number; failure_freq?: Record<string, number>; trace?: Record<string, unknown>; runs_detail?: SelfTestRunDetail[] }
export interface SelfTestResult { id: string; name: string; group: string; ok: boolean; skip: boolean; detail: string; ms: number; cases?: SelfTestCase[]; metrics?: { pass_rate: number; avg_score: number; passed: number; failed: number; skipped: number } }
export async function selftestSuites(): Promise<{ suites: SelfTestSuite[] }> {
  return _fetch("/api/selftest/suites", { headers: headers() });
}

// V306 外部基准对标：清单(含 leaderboard 参照) 与 趋势
export interface BenchRef { 0: string; 1: number }
export interface BenchInfo {
  id: string; name: string; lb_key: string; requires: string; kind: string; desc: string;
  leaderboard: { metric: string; refs: [string, number][]; note: string; source_url?: string };
}
export interface BenchTrendPoint { ts: number; bench_id: string; name: string; mode: string; score_pct: number; passed: number; total: number; detail: string }
export interface BenchTrendSummary { latest: number; best: number; runs: number; prev: number | null; delta: number | null; trend: string }
// V317 大厂对比与口径对齐
export interface BenchVsRow {
  id: string; name: string; your_score: number; n: number; passed: number;
  ci_low: number; ci_high: number; ci_width: number; coverage_pct: number;
  official_full: number; verdict: string; anchors: [string, number][];
  beats: string[]; why_not?: string; elapsed_ms?: number;
  /** V326：远程 CI（GitHub Actions 等）回传的分数标注来源，透明可查 */
  remote?: boolean; source?: string;
  /** V327：被测系统（你的Agent / 模型直答 / 官方harness×模型）——回答"测的是谁" */
  sut?: string;
  /** V327：裸模型基线分（消融对照，HASHMM_BENCH_BASELINE=1 跑出）；差值=你agent的贡献 */
  baseline_score?: number | null;
}
export interface BenchParityRow {
  id: string; name: string; verdict: string; dataset: string; judge: string; note: string;
}
export async function benchList(): Promise<{ ok: boolean; benchmarks: BenchInfo[]; mode?: string }> {
  return _fetch("/api/selftest/bench/list", { headers: headers() });
}
export async function benchReport(): Promise<{ ok: boolean; report_md: string }> {
  return _fetch("/api/selftest/bench/report", { headers: headers() });
}
// V317：Context Engine 三合一底座状态（知识检索+会话记忆+工具检索）
export async function contextEngineStatus(): Promise<{
  ok: boolean;
  pillars: Record<string, { ready: boolean; tools?: number; layered?: boolean; core_tools?: number; active_now?: boolean; note?: string }>;
  budgets: Record<string, number>;
  summary: string;
}> {
  return _fetch("/api/selftest/context-engine", { headers: headers() });
}
// V317：你 vs 2026 大厂横向对比（带可比性门禁）+ 口径对齐表
export async function benchVsFrontier(): Promise<{
  ok: boolean;
  comparison: {
    comparable: BenchVsRow[]; trend_only: BenchVsRow[];
    missing: { id: string; name: string; reason: string }[]; model: string;
  };
  summary?: { comparable_benches: number; anchors_total: number; anchors_beaten: number; topped_benches: number; headline: string };
  parity: BenchParityRow[];
  report_md: string;
}> {
  return _fetch("/api/selftest/bench/vs-frontier", { headers: headers() });
}
export async function benchPurge(): Promise<{ ok: boolean; purged: number; detail: string }> {
  return _fetch("/api/selftest/bench/purge", { method: "POST", headers: headers() });
}
/** V327 内网闭环：导入 CI Artifact 里的结果 JSON（服务器收不到回传时的离线通道）。 */
export async function benchImport(items: unknown): Promise<{ ok: boolean; imported: number; total: number; detail: string; results: { ok: boolean; bench_id?: string; detail?: string; baseline?: boolean }[] }> {
  return _fetch("/api/selftest/bench/import", {
    method: "POST", headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(items),
  });
}
export async function benchTrend(benchId?: string): Promise<{ ok: boolean; history: BenchTrendPoint[]; summary: Record<string, BenchTrendSummary> }> {
  const q = benchId ? `?bench_id=${encodeURIComponent(benchId)}` : "";
  return _fetch(`/api/selftest/bench/trend${q}`, { headers: headers() });
}
export async function selftestRun(ids: string[], save = true): Promise<{ results: SelfTestResult[]; summary: { pass: number; fail: number; skip: number; total: number }; report_md?: string; report_path?: string }> {
  return _fetch("/api/selftest/run", { method: "POST", headers: headers(), body: JSON.stringify({ ids, save }) });
}

/* V300 异步自测：立刻拿 job_id，后台跑；配合 selftestJob 轮询——每次 HTTP 都短，超重套件也不会被反代掐断。 */
export async function selftestRunAsync(ids: string[], save = true): Promise<{ job_id: string; total: number }> {
  return _fetch("/api/selftest/run_async", { method: "POST", headers: headers(), body: JSON.stringify({ ids, save }) });
}

export interface SelftestJob {
  id: string; status: "running" | "done" | "error";
  done: number; total: number; results: SelfTestResult[];
  summary: { pass: number; fail: number; skip: number; total: number } | null;
  report_md: string; report_path: string; error: string | null;
}
export async function selftestJob(jobId: string): Promise<SelftestJob> {
  return _fetch(`/api/selftest/job/${jobId}`, { method: "GET", headers: headers() });
}

/* V285：用已跑结果直接渲染完整报告（不重跑，省 LLM 开销）。 */
export async function selftestRenderReport(results: SelfTestResult[]): Promise<{ ok: boolean; report_md?: string; report_path?: string }> {
  return _fetch("/api/selftest/render-report", { method: "POST", headers: headers(), body: JSON.stringify({ results }) });
}

/* V289：把前端生成好的报告 md 直接发去服务器落盘（小载荷=纯文本，最稳）。 */
export async function selftestSaveReport(reportMd: string): Promise<{ ok: boolean; report_path?: string; dir?: string }> {
  return _fetch("/api/selftest/save-report", { method: "POST", headers: headers(), body: JSON.stringify({ report_md: reportMd }) });
}
export async function selftestReportDir(): Promise<{ dir: string; latest: string }> {
  return _fetch("/api/selftest/report-dir", { headers: headers() });
}

/* ── V270 用户版控制台：查看自己的操作日志 ── */
export async function myAudit(limit = 200): Promise<{ logs: AuditLog[]; total?: number }> {
  return _fetch(`/api/admin/audit/mine?limit=${limit}`, { headers: headers() });
}

/* ── V2500 Provider Fabric：用户自有官方 API / Sub2API / 兼容网关 ── */
export interface ProviderChannel {
  id: string; model_alias: string; upstream_model: string; priority: number;
  weight: number; max_concurrency: number; active_leases?: number; enabled: number;
  capabilities?: Record<string, unknown>; circuit_state?: string; connection_status?: string;
}
export interface ProviderConnection {
  id: string; name: string; kind: "official" | "sub2api" | "openai_compatible" | "anthropic_compatible" | "local";
  base_url: string; wire_api: string; enabled: boolean; status: string;
  has_credential: boolean; credential_count: number; channels: ProviderChannel[]; updated_at: number;
}
export interface ProviderFabricOverview {
  contract: "hashmm.provider-fabric.v2"; connections: ProviderConnection[];
  summary: { connections: number; channels: number; healthy: number; sub2api: number };
  security: { credentials_encrypted: boolean; upstream_auth_isolated: boolean; retry_boundary: string; prompt_logging: boolean };
}
export async function providerFabric(): Promise<ProviderFabricOverview> {
  return _fetch("/api/provider-fabric", { headers: headers() });
}
export async function addProviderConnection(payload: Record<string, unknown>): Promise<{ ok: boolean; connection: ProviderConnection }> {
  return _fetch("/api/provider-fabric", { method: "POST", headers: headers(), body: JSON.stringify(payload) });
}
export async function deleteProviderConnection(id: string): Promise<{ ok: boolean }> {
  return _fetch(`/api/provider-fabric/${encodeURIComponent(id)}`, { method: "DELETE", headers: headers() });
}
export async function probeProviderConnection(id: string): Promise<{ ok: boolean; message?: string; latency_ms?: number }> {
  return _fetch(`/api/provider-fabric/${encodeURIComponent(id)}/probe`, { method: "POST", headers: headers() });
}
export async function activateProviderConnection(id: string): Promise<{ ok: boolean; model_id: string }> {
  return _fetch(`/api/provider-fabric/${encodeURIComponent(id)}/activate`, { method: "POST", headers: headers() });
}
export async function addProviderChannel(id: string, payload: Record<string, unknown>): Promise<{ ok: boolean; channel: ProviderChannel }> {
  return _fetch(`/api/provider-fabric/${encodeURIComponent(id)}/channels`, { method: "POST", headers: headers(), body: JSON.stringify(payload) });
}
export async function previewProviderRoute(modelAlias: string): Promise<{
  contract: "hashmm.provider-route.v1"; preview: boolean; retry_boundary: string; channel: ProviderChannel & { connection_name?: string };
}> {
  return _fetch(`/api/provider-fabric/routes/${encodeURIComponent(modelAlias)}/preview`, { headers: headers() });
}
export async function updateProviderRoutingPolicy(modelAlias: string, payload: Record<string, unknown>): Promise<{ ok: boolean; policy: Record<string, unknown> }> {
  return _fetch(`/api/provider-fabric/policies/${encodeURIComponent(modelAlias)}`, { method: "PUT", headers: headers(), body: JSON.stringify(payload) });
}

/* ── V266 我在 HashMM 的用量（普通用户可见，走 /usage/me） ── */
export interface MyUsage { requests: number; tokens: number; cost: number }
export async function usageMe(days = 30): Promise<MyUsage> {
  return _fetch(`/api/admin/usage/me?days=${days}`, { headers: headers() });
}

export interface UsageOverviewRow {
  model?: string; username?: string; requests: number; tokens: number; cost: number;
}
export interface UsageOverview {
  contract: "hashmm.usage-overview.v1";
  scope: "team" | "personal";
  days: number; requests: number; tokens: number; tokens_in: number; tokens_out: number;
  cost: number; currency: string; by_model: UsageOverviewRow[]; by_user: UsageOverviewRow[];
}
export async function usageOverview(days = 30): Promise<UsageOverview> {
  return _fetch(`/api/admin/usage/overview?days=${days}`, { headers: headers() });
}

/* ── V317 跨 Agent 协作（好友/同组织的 agent 安全协作 + 防窃取）── */
export interface CollabFriend { user: string; status: string; direction: string }
export interface CollabScope { scope: string; desc: string; reads_private: boolean }
export interface CollabAuditEntry { id: string; ts: number; from_user: string; to_user: string; action: string; ok: number; scope: string; detail: string }
export async function collabFriends(): Promise<{ ok: boolean; friends: CollabFriend[] }> {
  return _fetch("/api/collab/friends", { headers: headers() });
}
export async function collabFriendRequest(target: string): Promise<{ ok: boolean; status?: string; detail: string }> {
  return _fetch("/api/collab/friends/request", { method: "POST", headers: headers(), body: JSON.stringify({ target }) });
}
export async function collabFriendAccept(requester: string): Promise<{ ok: boolean; status?: string; detail: string }> {
  return _fetch("/api/collab/friends/accept", { method: "POST", headers: headers(), body: JSON.stringify({ requester }) });
}
export async function collabFriendRemove(other: string): Promise<{ ok: boolean; detail: string }> {
  return _fetch("/api/collab/friends/remove", { method: "POST", headers: headers(), body: JSON.stringify({ other }) });
}
export async function collabScopes(): Promise<{ ok: boolean; scopes: CollabScope[] }> {
  return _fetch("/api/collab/scopes", { headers: headers() });
}
export async function collabRequest(to_user: string, scope: string, task: string, allow_private = false): Promise<{
  ok: boolean; result?: string; redacted?: boolean; rejected_reason?: string;
  security?: Record<string, unknown>; audit_id?: string;
}> {
  return _fetch("/api/collab/request", { method: "POST", headers: headers(), body: JSON.stringify({ to_user, scope, task, allow_private }) });
}
export async function collabAuditIncoming(): Promise<{ ok: boolean; log: CollabAuditEntry[] }> {
  return _fetch("/api/collab/audit/incoming", { headers: headers() });
}

/* ── V319 交互 Agent（对外通信安全网关）状态 ── */
export async function interactionAgentStatus(): Promise<{
  ok: boolean; role: string; rate_limit: string; active_peers: number; guards: string[];
}> {
  return _fetch("/api/collab/interaction-agent", { headers: headers() });
}

/* ── V320 组织管理 ── */
export interface OrgMember { user_id: string; role: string; joined: number }
export async function collabOrgs(): Promise<{ ok: boolean; orgs: string[] }> {
  return _fetch("/api/collab/orgs", { headers: headers() });
}
export async function collabOrgMembers(orgId: string): Promise<{ ok: boolean; members: OrgMember[]; detail?: string }> {
  return _fetch(`/api/collab/orgs/${encodeURIComponent(orgId)}/members`, { headers: headers() });
}
export async function collabOrgAddMember(org_id: string, user_id: string, role = "member"): Promise<{ ok: boolean; detail: string }> {
  return _fetch("/api/collab/orgs/add-member", { method: "POST", headers: headers(), body: JSON.stringify({ org_id, user_id, role }) });
}

/* ── V320 跨机对等体（RPC 传输，需管理员）── */
export interface RpcPeer { peer_id: string; endpoint: string; label: string; created: number; updated: number }
export async function collabPeers(): Promise<{ ok: boolean; self_peer_id: string; peers: RpcPeer[] }> {
  return _fetch("/api/collab/peers", { headers: headers() });
}
export async function collabPeerGenSecret(): Promise<{ ok: boolean; secret: string }> {
  return _fetch("/api/collab/peers/gen-secret", { method: "POST", headers: headers() });
}
export async function collabPeerPair(peer_id: string, endpoint: string, secret: string, label = ""): Promise<{ ok: boolean; secure?: boolean; detail: string }> {
  return _fetch("/api/collab/peers/pair", { method: "POST", headers: headers(), body: JSON.stringify({ peer_id, endpoint, secret, label }) });
}
export async function collabPeerRemove(peer_id: string): Promise<{ ok: boolean; detail: string }> {
  return _fetch("/api/collab/peers/remove", { method: "POST", headers: headers(), body: JSON.stringify({ peer_id }) });
}

/* ── V320 对比图导出（SVG/CSV 原文，前端转 Blob 触发下载）── */
export async function benchChartSvg(): Promise<string> {
  const r = await fetch("/api/selftest/bench/chart.svg", { headers: authHeaders() });
  if (!r.ok) throw new Error(`导出失败 HTTP ${r.status}`);
  return r.text();
}
export async function benchChartCsv(): Promise<string> {
  const r = await fetch("/api/selftest/bench/chart.csv", { headers: authHeaders() });
  if (!r.ok) throw new Error(`导出失败 HTTP ${r.status}`);
  return r.text();
}

/* ── V320.1 可对比运行：跑够题数(standard=50)去和大厂比 ── */
export interface ComparableCandidate {
  id: string; name: string; runnable: boolean; hint: string;
  standard_n: number; full_n: number; min_comparable: number;
}
export async function benchComparableCandidates(): Promise<{ ok: boolean; candidates: ComparableCandidate[]; runnable_now: string[] }> {
  return _fetch("/api/selftest/bench/comparable-candidates", { headers: headers() });
}
export async function benchComparableRun(bench_ids?: string[], sample: "standard" | "full" = "standard", parallel?: number, paired_baseline = false): Promise<{ ok: boolean; job_id?: string; total?: number; sample?: string; bench_ids?: string[]; parallel?: number | null; paired_baseline?: boolean; detail?: string }> {
  // V332：parallel=基准级并发（1~4，后端上限 4）；缺省走服务端 HASHMM_BENCH_PARALLEL（默认 2）。
  return _fetch("/api/selftest/bench/comparable-run", { method: "POST", headers: headers(), body: JSON.stringify({ bench_ids, sample, paired_baseline, ...(parallel != null ? { parallel } : {}) }) });
}
