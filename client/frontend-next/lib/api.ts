import type { ChatResponse, Stats, ModelConfig, User, KB, AuditLog, Source, TraceStep, ToolStep, FileInfo, StreamDoneData, ConversationMeta, Message } from "./types";
import { useStore, setTokens } from "./store";

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
  trace?: TraceStep[]; answer_by?: string | null; degraded?: boolean;
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

let _refreshInFlight: Promise<string | null> | null = null;

async function _doRefresh(): Promise<string | null> {
  // Single-flight: concurrent callers share one refresh request.
  if (_refreshInFlight) return _refreshInFlight;
  _refreshInFlight = (async () => {
    const rt = useStore.getState().refreshToken;
    if (!rt) return null;
    try {
      const r = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!r.ok) return null;
      const data = await r.json();
      if (!data?.token) return null;
      setTokens(data.token, data.refresh_token);   // rotation: store new refresh too
      return data.token as string;
    } catch { return null; }
  })();
  try { return await _refreshInFlight; }
  finally { _refreshInFlight = null; }
}

/** Refresh proactively if the access token expires within 60s. */
export async function ensureFreshToken(): Promise<void> {
  const token = useStore.getState().token;
  if (!token) return;
  const exp = _decodeExp(token);
  if (exp !== null && exp - Math.floor(Date.now() / 1000) < 60) {
    await _doRefresh();
  }
}

// v12: Request dedup cache — prevents duplicate GET requests within 2s window
const _dedupCache = new Map<string, { promise: Promise<unknown>; ts: number }>();
const DEDUP_TTL = 2000; // 2 seconds

async function _fetch(url: string, init?: RequestInit) {
  // Proactively renew a near-expired access token before sending.
  if (useStore.getState().token) await ensureFreshToken();

  // GET requests: dedup within TTL window
  const method = init?.method?.toUpperCase() || "GET";
  const cacheKey = method === "GET" ? url : "";
  if (cacheKey) {
    const cached = _dedupCache.get(cacheKey);
    if (cached && Date.now() - cached.ts < DEDUP_TTL) {
      return cached.promise;
    }
  }

  const promise = (async () => {
    let r = await fetch(url, init);
    if (r.status === 401) {
      // V93: 游客态（本次请求根本没带 token）的 401 是正常态——静默失败，
      // 绝不触发 refresh/logout（否则游客浏览期会被 401 风暴反复打断）。
      const hadAuth = !!((init?.headers as Record<string, string> | undefined)?.Authorization);
      if (!hadAuth) throw new Error("未登录");
      // Reactive refresh + single retry before giving up.
      const nt = await _doRefresh();
      if (nt) {
        const retryInit: RequestInit = { ...init };
        retryInit.headers = { ...(init?.headers as Record<string, string>), Authorization: `Bearer ${nt}` };
        r = await fetch(url, retryInit);
      }
      if (r.status === 401) {
        useStore.getState().logout();
        throw new Error("登录已过期，请重新登录");
      }
    }
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.message || body.detail || `HTTP ${r.status}`);
    }
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

// ── Chat ──
export async function chat(message: string, sid?: string | null, fileContext?: string | null): Promise<ChatResponse> {
  return _fetch("/api/chat", { method: "POST", headers: headers(), body: JSON.stringify({ message, session_id: sid, file_context: fileContext || undefined }) });
}

export interface StreamCallbacks {
  onTrace: (steps: TraceStep[]) => void;
  onToken: (token: string) => void;
  onThinking: (data: { content?: string }) => void;
  onTodo?: (data: { items: Array<{ text: string; status: "pending" | "doing" | "done" }> }) => void;  // V50: 任务清单
  onDelta?: (data: { t: string }) => void;            // V54: 真·流式直播增量（预览）
  onDeltaCommit?: (data: { as: "narrate" | "answer" }) => void;  // V54: 直播段归属裁决
  onFileDelta?: (data: { filename: string; t: string }) => void;  // V55: 右栏逐字写代码
  onCtx?: (data: { chars: number; budget: number }) => void;      // V55: 上下文用量表
  onStepStart: (step: { tool: string; detail: string; id?: string; args?: Record<string, unknown> }) => void;
  onStepDone: (step: { tool: string; status: string; detail: string; duration_ms?: number; id?: string }) => void;
  onFile: (file: FileInfo) => void;
  onIteration: (data: { current: number; max: number }) => void;
  onProgress?: (data: { stage: string; pct: number; msg: string }) => void;
  onSources?: (sources: Source[]) => void;  // v9.0: pre-sent sources before streaming
  onClarify?: (data: { question: string; options: string[] }) => void;  // V103.27: 主动澄清可点选项
  onOrchestration?: (data: { strategy?: string; members: Array<{ id: string; step?: number; role_label?: string; task?: string }> }) => void;  // V103.30: 子 agent 编排 DAG
  onSubagent?: (data: { id: string; status: string; step?: number; total?: number; description?: string; elapsed_ms?: number; preview?: string }) => void;  // V103.30: 子 agent 实时状态
  onDone: (data: StreamDoneData) => void;
  onError: (error: string) => void;
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
        if (res.status === 401) {
          // 先静默续期再重试，只有续期也失败（refresh token 真失效）才登出——避免动不动就要求重新登录。
          const nt = await _doRefresh();
          if (nt) { cb.onError("登录已自动刷新，请重新发送"); return; }
          useStore.getState().logout(); cb.onError("登录已过期"); return;
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
            if (eventType === "trace") cb.onTrace(parsed.steps || []);
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
export async function toggleScheduled(id: string) { return _fetch(`/api/admin/scheduled/${encodeURIComponent(id)}/toggle`, { method: "POST", headers: headers() }); }
// 模型路由/角色→后端（V103.28）：读取/保存任务→本地·云端 路由表（仅管理员）。
export async function getLlmRouting() { return _fetch("/api/admin/llm-routing", { headers: headers() }); }
export async function testLlmRouting(task: string) { return _fetch("/api/admin/llm-routing/test", { method: "POST", headers: headers(), body: JSON.stringify({ task }) }); }
export async function saveLlmRouting(routing: Record<string, string>) { return _fetch("/api/admin/llm-routing", { method: "PUT", headers: headers(), body: JSON.stringify({ routing }) }); }
// Agent 运行轨迹（V103.29）：读取最近 run_record 遥测（仅管理员）。
export async function listRuns(limit = 50) { return _fetch(`/api/admin/runs?limit=${limit}`, { headers: headers() }); }
// 自发现（V103.31）：按需跑一次主动扫描（仅管理员）。
export async function runDiscovery() { return _fetch("/api/admin/discovery", { headers: headers() }); }
// 发现 → 一键处理（V103.33，loop 闭环 handoff）：构建社区 / 创建定时任务。
export async function rebuildCommunities() { return _fetch("/api/kg/rebuild-communities", { method: "POST", headers: headers() }); }
export async function createScheduledTask(body: Record<string, unknown>) { return _fetch("/api/admin/scheduled", { method: "POST", headers: headers(), body: JSON.stringify(body) }); }
// V103.34：手动写记忆（教它记住）。重建索引复用既有 rebuildIndex。
export async function addMemory(body: { category?: string; key: string; value: string }) { return _fetch("/api/memory", { method: "POST", headers: headers(), body: JSON.stringify(body) }); }
export async function deleteSession(sid: string) { return _fetch(`/api/sessions/${sid}`, { method: "DELETE", headers: headers() }); }

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
export async function listModels(): Promise<ModelConfig[]> { return _fetch("/api/admin/models", { headers: headers() }); }
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
export async function getModelPresets() { return _fetch("/api/admin/models/presets", { headers: headers() }); }

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
export async function compareEvalRuns(baseline: string, candidate: string) {
  return _fetch(`/api/admin/eval/compare?baseline=${baseline}&candidate=${candidate}`, { headers: headers() });
}
export async function getUsageSummary(days = 30) {
  return _fetch(`/api/admin/usage/summary?days=${days}`, { headers: headers() });
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
  try { return await _fetch("/api/admin/models", { headers: headers() }); } catch { return []; }
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

// ── File Preview (v13: conversation-scoped) ──
export async function previewFile(filename: string, convId?: string): Promise<{
  filename: string; ext: string; size: number; language?: string;
  content: string | null; lines?: number; binary?: boolean; download_url: string;
  rich?: { kind: string; html?: string; sheets?: { name: string; headers: string[]; rows: (string | number | null)[][]; total_rows?: number }[]; slides?: string[]; pages?: number } | null;
}> {
  // 防御：有时 filename 里混进了 ?token=...（从下载 URL 误取），会让后端文件名超长崩溃。先剥掉查询/锚点。
  filename = filename.split("?")[0].split("#")[0];
  // v27: Always use conversation-scoped preview (old global URL removed — always 404)
  if (convId) {
    return _fetch(`/api/conversations/${convId}/files/${encodeURIComponent(filename)}/preview`, { headers: headers() });
  }
  // No convId — try to get from current URL
  const urlConvId = typeof window !== 'undefined' ? window.location.pathname.match(/\/chat\/([^/]+)/)?.[1] : null;
  if (urlConvId) {
    return _fetch(`/api/conversations/${urlConvId}/files/${encodeURIComponent(filename)}/preview`, { headers: headers() });
  }
  return Promise.reject(new Error("无法确定对话 ID"));
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

export async function loadConversation(convId: string): Promise<{ conversation: ConversationMeta; messages: Message[] }> {
  return _fetch(`/api/conversations/${convId}`, { headers: headers() });
}

export async function listConversations(): Promise<{ conversations: ConversationMeta[] }> {
  return _fetch("/api/conversations", { headers: headers() });
}

export async function createConversation(id: string, title: string): Promise<{ id: string }> {
  return _fetch("/api/conversations", {
    method: "POST", headers: headers(),
    body: JSON.stringify({ id, title })
  });
}

export async function updateConversation(convId: string, data: { title?: string; pinned?: number }) {
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

export async function listConvFiles(convId: string): Promise<{ files: FileInfo[] }> {
  return _fetch(`/api/conversations/${convId}/files`, { headers: headers() });
}

/** v10.0 SSE — uses new conversation endpoint with typed handlers. */
export async function chatStreamV10(convId: string, message: string, fileContext: string | null, cb: StreamCallbacks, docFilter?: string[], retrievalMode?: string, attachments?: string[]) {
  await ensureFreshToken();   // renew a near-expired access token before a long stream
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 300000);
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
      }),
      signal: controller.signal,
    });
    if (!res.ok) {
      clearTimeout(timer);
      if (res.status === 401) {
        // Try one silent refresh; only log out if it genuinely failed (revoked).
        const nt = await _doRefresh();
        if (nt) { cb.onError("登录已自动刷新，请重新发送"); return; }
        useStore.getState().logout(); cb.onError("登录已过期"); return;
      }
      cb.onError(`服务错误 (${res.status})`);
      return;
    }
    const reader = res.body?.getReader();
    if (!reader) { clearTimeout(timer); cb.onError("浏览器不支持流式响应"); return; }

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
        if (!data || eventType === "keepalive") continue;
        try {
          const parsed = JSON.parse(data);
          if (eventType === "trace") cb.onTrace(parsed.steps || []);
          else if (eventType === "token") cb.onToken(parsed.content || "");
          else if (eventType === "thinking") cb.onThinking(parsed);
          else if (eventType === "step_start") cb.onStepStart(parsed);
          else if (eventType === "step_done") cb.onStepDone(parsed);
          else if (eventType === "file") cb.onFile(parsed);
          else if (eventType === "iteration") cb.onIteration(parsed);
          else if (eventType === "progress") { if (cb.onProgress) cb.onProgress(parsed); }
          else if (eventType === "sources") { if (cb.onSources) cb.onSources(parsed.sources || []); }
          else if (eventType === "clarify") { if (cb.onClarify) cb.onClarify(parsed); }
          else if (eventType === "orchestration") { if (cb.onOrchestration) cb.onOrchestration(parsed); }
          else if (eventType === "subagent") { if (cb.onSubagent) cb.onSubagent(parsed); }
          else if (eventType === "done") cb.onDone(parsed);
        } catch {}
      }
    }
    clearTimeout(timer);
  } catch (e: unknown) {
    clearTimeout(timer);
    if (e instanceof Error && e.name === "AbortError") { cb.onError("请求超时（5分钟），请简化问题后重试"); return; }
    cb.onError(e instanceof Error && e.message?.includes("Failed to fetch") ? "连接中断，请刷新页面" : (e instanceof Error ? e.message : "网络错误"));
  }
}

// ── v10.0: Evolution API ──
export async function listEvolutionSkills() { return _fetch("/api/evolution/skills", { headers: headers() }); }
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
