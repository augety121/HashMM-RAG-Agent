/** lib/supabase.ts — Supabase 登录（统一身份：与 App 共用 Supabase 账号）。
 *
 *  客户端直连 Supabase 官方鉴权 REST（POST /auth/v1/token），零依赖、不经过 HashMM 后端，
 *  所以**不管连不连 AutoDL、后端有没有配 Supabase，都能登录**。
 *  配置优先用后端 /api/auth/supabase-config；后端未配（本地/离线）时用内置配置（publishable key 可公开）。
 *  角色（admin/user）从登录返回的 JWT 的 app_metadata.role 解析。
 */
export interface SupabaseConfig { enabled: boolean; url: string; publishable_key: string }
export interface SupabaseLoginResult { access_token: string; refresh_token: string; email: string; user_id: string; role: string }

// 内置兜底配置（与 App 同一个 Supabase 项目；publishable key 为可公开密钥）。
const FALLBACK_CONFIG: SupabaseConfig = {
  enabled: true,
  url: "https://your-project.supabase.co",
  publishable_key: "YOUR_SUPABASE_PUBLISHABLE_KEY",
};

let _config: SupabaseConfig | null = null;

/** 取 Supabase 公开配置：后端已配则用后端，否则用内置兜底（保证哪里都能登录）。 */
export async function getSupabaseConfig(): Promise<SupabaseConfig> {
  if (_config) return _config;
  try {
    const r = await fetch("/api/auth/supabase-config");
    if (r.ok) {
      const c = await r.json();
      if (c?.enabled && c?.url && c?.publishable_key) { _config = c; return _config; }
    }
  } catch { /* 后端不可达：用内置兜底 */ }
  _config = FALLBACK_CONFIG;
  return _config;
}

/** 从 Supabase access_token(JWT) 解析应用角色（app_metadata.role）。失败回退 "user"。 */
export function roleFromToken(token: string): string {
  try {
    const seg = token.split(".")[1];
    if (!seg) return "user";
    const payload = JSON.parse(atob(seg.replace(/-/g, "+").replace(/_/g, "/")));
    return payload?.app_metadata?.role || payload?.user_metadata?.role || "user";
  } catch { return "user"; }
}

/** 从 Supabase access_token(JWT) 取用户 uuid（sub 声明）。失败回退 ""。
 *  说明：客户端登录后只存了 email/role，没存 id；头像 / 档案要按 Supabase 真实 uuid（=sub）来读写。 */
export function userIdFromToken(token: string | null | undefined): string {
  if (!token) return "";
  try {
    const seg = token.split(".")[1];
    if (!seg) return "";
    const payload = JSON.parse(atob(seg.replace(/-/g, "+").replace(/_/g, "/")));
    return payload?.sub || "";
  } catch { return ""; }
}

// ── PostgREST helper：直连 Supabase /rest/v1（带用户令牌，RLS 生效）──
async function _rest(token: string, path: string, init?: RequestInit): Promise<Response> {
  const cfg = await getSupabaseConfig();
  const base = cfg.url.replace(/\/+$/, "");
  return fetch(`${base}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: cfg.publishable_key,
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
}

export interface SupabaseProfile {
  id: string; username: string; display_name: string; avatar_url: string | null; is_admin: boolean;
}

/** 读当前登录用户的云端档案（含头像 data URL）。无档案 / 失败返回 null。 */
export async function getMyProfile(token: string): Promise<SupabaseProfile | null> {
  const uid = userIdFromToken(token);
  if (!uid) return null;
  try {
    const r = await _rest(token, `profiles?id=eq.${uid}&select=id,username,display_name,avatar_url,is_admin&limit=1`);
    if (!r.ok) return null;
    const rows = await r.json().catch(() => []);
    return Array.isArray(rows) && rows[0] ? rows[0] as SupabaseProfile : null;
  } catch { return null; }
}

// ── V269 离线云端历史（只读）────────────────────────────────────────────
// 后端在线时会把每个会话/消息实时推到 Supabase（hashmm/api/supabase_sync.py，
// 表结构与 RLS 见 sql/hashmm-supabase-sync.sql：select 仅限 auth.uid()=user_id）。
// 这两个函数让前端在**后端没启动**时用用户自己的 JWT 直读云端记录——换设备、
// 缓存被清也能看到历史。写入永远由后端完成，前端只读，密钥面最小。
export interface CloudConv {
  id: string; title: string; pinned: boolean; archived?: boolean;
  created_at: string; updated_at: string; project_id?: string;
}
export async function listCloudConversations(token: string): Promise<CloudConv[]> {
  const uid = userIdFromToken(token);
  if (!uid) return [];   // 非 Supabase 登录（本地账号）没有云端记录
  try {
    const all: CloudConv[] = [];
    const pageSize = 1000;
    let modernSchema = true;
    for (let offset = 0; offset < 10000; offset += pageSize) {
      let r = await _rest(token, `chat_conversations?user_id=eq.${uid}` +
        `&select=${modernSchema ? "id,title,pinned,archived,project_id,created_at,updated_at" : "id,title,pinned,archived,created_at,updated_at"}` +
        `&order=updated_at.desc,id.desc&limit=${pageSize}&offset=${offset}`);
      if (!r.ok && modernSchema && offset === 0) {
        modernSchema = false;
        r = await _rest(token, `chat_conversations?user_id=eq.${uid}` +
          `&select=id,title,pinned,archived,created_at,updated_at` +
          `&order=updated_at.desc,id.desc&limit=${pageSize}&offset=0`);
      }
      if (!r.ok) return all;
      const rows = await r.json().catch(() => []);
      if (!Array.isArray(rows)) return all;
      all.push(...rows as CloudConv[]);
      if (rows.length < pageSize) break;
    }
    return all;
  } catch { return []; }
}
export interface CloudMsg {
  id: string; role: string; content: string; thinking?: string;
  tool_calls?: unknown[]; files?: unknown[]; sources?: unknown[]; suggestions?: unknown[];
  status?: string;
  created_at: string;
}
export async function getCloudMessages(token: string, convId: string): Promise<CloudMsg[]> {
  if (!userIdFromToken(token)) return [];
  try {
    const r = await _rest(token, `chat_messages?conv_id=eq.${encodeURIComponent(convId)}` +
      `&select=id,role,content,thinking,tool_calls,files,sources,suggestions,status,created_at` +
      `&order=created_at.asc&limit=500`);
    if (!r.ok) return [];
    const rows = await r.json().catch(() => []);
    return Array.isArray(rows) ? rows as CloudMsg[] : [];
  } catch { return []; }
}

/** 把头像（已压成的 data URL 字符串）写入云端档案。upsert，行不存在也安全。返回是否成功。 */
export async function updateMyAvatar(token: string, dataUrl: string): Promise<boolean> {  const uid = userIdFromToken(token);
  if (!uid) return false;
  try {
    // 先 PATCH（行通常已由 handle_new_user 触发器建好）；行不存在(204 但 0 行)再兜底 upsert。
    const patch = await _rest(token, `profiles?id=eq.${uid}`, {
      method: "PATCH",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify({ avatar_url: dataUrl }),
    });
    if (patch.ok) return true;
    const up = await _rest(token, `profiles?on_conflict=id`, {
      method: "POST",
      headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
      body: JSON.stringify({ id: uid, avatar_url: dataUrl }),
    });
    return up.ok;
  } catch { return false; }
}

/** V203：更新当前用户的云端档案字段（如 display_name）。与 updateMyAvatar 同一
 *  PATCH→upsert 兜底模式；Supabase 登录的用户改名走这里（后端 /api/admin/users
 *  只认后端本地账号且要管理员，之前 Supabase 用户改名就是因此坏掉的）。 */
export async function updateMyProfile(token: string, fields: { display_name?: string }): Promise<boolean> {
  const uid = userIdFromToken(token);
  if (!uid) return false;
  try {
    const patch = await _rest(token, `profiles?id=eq.${uid}`, {
      method: "PATCH",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify(fields),
    });
    if (patch.ok) return true;
    const up = await _rest(token, `profiles?on_conflict=id`, {
      method: "POST",
      headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
      body: JSON.stringify({ id: uid, ...fields }),
    });
    return up.ok;
  } catch { return false; }
}

export interface AdminProfile {
  id: string; username: string; display_name: string; avatar_url: string | null;
  is_admin: boolean; email: string; created_at: string | null; last_sign_in_at: string | null;
}

/** 管理员：拉「Supabase 里所有用户」（走 list_all_profiles RPC，函数内自校验管理员）。
 *  非管理员 / 未部署 RPC 会失败 → 返回 null，调用方回退后端本地用户列表。 */
export async function listAllProfiles(token: string): Promise<AdminProfile[] | null> {
  try {
    const r = await _rest(token, `rpc/list_all_profiles`, { method: "POST", body: "{}" });
    if (!r.ok) return null;
    const rows = await r.json().catch(() => null);
    return Array.isArray(rows) ? rows as AdminProfile[] : null;
  } catch { return null; }
}

export interface MemoryRow { id: string; category: string; key: string; value: string; confidence: number; last_used: string | null; }

/** 读当前用户在 Supabase user_memory 的记忆（与 App 同源）。失败返回 null。 */
export async function getMyMemories(token: string): Promise<MemoryRow[] | null> {
  const uid = userIdFromToken(token);
  if (!uid) return null;
  try {
    const r = await _rest(token, `user_memory?user_id=eq.${uid}&select=id,category,key,value,confidence,last_used&order=category.asc&limit=500`);
    if (!r.ok) return null;
    const rows = await r.json().catch(() => null);
    return Array.isArray(rows) ? rows as MemoryRow[] : null;
  } catch { return null; }
}

/** 手动写一条记忆到 Supabase user_memory（让 App 也能看到桌面端加的记忆）。返回新 id 或 null。 */
export async function addMyMemory(token: string, category: string, key: string, value: string): Promise<string | null> {
  const uid = userIdFromToken(token);
  if (!uid) return null;
  const id = `m_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  try {
    const r = await _rest(token, `user_memory`, {
      method: "POST",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify({ id, user_id: uid, category, key, value, confidence: 0.8 }),
    });
    return r.ok ? id : null;
  } catch { return null; }
}

/** 删除 Supabase user_memory 一条（best-effort，仅本人 RLS 生效）。 */
export async function deleteMyMemory(token: string, id: string): Promise<boolean> {
  try {
    const r = await _rest(token, `user_memory?id=eq.${encodeURIComponent(id)}`, {
      method: "DELETE", headers: { Prefer: "return=minimal" },
    });
    return r.ok;
  } catch { return false; }
}

/** 用 refresh_token 直连 Supabase 续期 access_token（不经后端）。远程被控保活专用：
 *  被控窗只有一个会过期的 access_token，自己无法续；前端用这里拿到新令牌再推给它。
 *  仅对 Supabase 登录的会话有效；若 refresh_token 不是 Supabase 的（如后端 JWT 会话）返回 null，调用方静默忽略。 */
export type SupabaseRefreshResult =
  | { status: "ok"; access_token: string; refresh_token: string }
  | { status: "rejected" }
  | { status: "unavailable" };

/** Detailed refresh result: an identity-service outage is not a revoked session. */
export async function refreshSupabaseTokenDetailed(refreshToken: string): Promise<SupabaseRefreshResult> {
  if (!refreshToken) return { status: "rejected" };
  try {
    const cfg = await getSupabaseConfig();
    if (!cfg.url || !cfg.publishable_key) return { status: "unavailable" };
    const base = cfg.url.replace(/\/+$/, "");
    const r = await fetch(`${base}/auth/v1/token?grant_type=refresh_token`, {
      method: "POST",
      headers: { "Content-Type": "application/json", apikey: cfg.publishable_key },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!r.ok) {
      // 400/401 are explicit refresh-token rejection. 5xx/rate-limit are
      // provider availability failures and must preserve the local session.
      return r.status === 400 || r.status === 401
        ? { status: "rejected" }
        : { status: "unavailable" };
    }
    const data = await r.json().catch(() => null);
    if (!data?.access_token) return { status: "unavailable" };
    return { status: "ok", access_token: data.access_token, refresh_token: data.refresh_token || refreshToken };
  } catch { return { status: "unavailable" }; }
}

export async function refreshSupabaseToken(refreshToken: string): Promise<{ access_token: string; refresh_token: string } | null> {
  const result = await refreshSupabaseTokenDetailed(refreshToken);
  return result.status === "ok"
    ? { access_token: result.access_token, refresh_token: result.refresh_token }
    : null;
}

/** 邮箱密码登录 Supabase（客户端直连 REST），返回 token + 角色。失败抛错。 */
export async function resendSignupOtp(email: string): Promise<void> {
  const cfg = await getSupabaseConfig();
  if (!cfg.url || !cfg.publishable_key) throw new Error("Supabase 未配置");
  const base = cfg.url.replace(/\/+$/, "");
  const r = await fetch(`${base}/auth/v1/resend`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: cfg.publishable_key },
    body: JSON.stringify({ type: "signup", email }),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data?.msg || data?.error_description || "重新发送失败");
  }
}

export async function verifySignupOtp(email: string, token: string): Promise<SupabaseLoginResult> {
  const cfg = await getSupabaseConfig();
  if (!cfg.url || !cfg.publishable_key) throw new Error("Supabase 未配置");
  const base = cfg.url.replace(/\/+$/, "");
  // Supabase 邮箱注册验证码：POST /auth/v1/verify { type:"signup", email, token }
  const r = await fetch(`${base}/auth/v1/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: cfg.publishable_key },
    body: JSON.stringify({ type: "signup", email, token }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = data?.error_description || data?.msg || data?.error || "验证码错误或已过期";
    throw new Error(typeof msg === "string" ? msg : "验证失败");
  }
  const tok = data?.access_token;
  if (!tok) throw new Error("验证未返回会话，请稍后重试");
  return {
    access_token: tok,
    refresh_token: data?.refresh_token || "",
    email: data?.user?.email || email,
    user_id: data?.user?.id || "",
    role: data?.user?.app_metadata?.role || roleFromToken(tok),
  };
}

export async function signUpWithSupabase(email: string, password: string): Promise<{ needsConfirm: boolean; session: SupabaseLoginResult | null }> {
  const cfg = await getSupabaseConfig();
  if (!cfg.url || !cfg.publishable_key) throw new Error("Supabase 未配置");
  const base = cfg.url.replace(/\/+$/, "");
  const r = await fetch(`${base}/auth/v1/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: cfg.publishable_key },
    body: JSON.stringify({ email, password }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = data?.error_description || data?.msg || data?.error || "注册失败";
    throw new Error(typeof msg === "string" ? msg : "注册失败");
  }
  const token = data?.access_token;
  if (token) {
    return { needsConfirm: false, session: {
      access_token: token,
      refresh_token: data?.refresh_token || "",
      email: data?.user?.email || email,
      user_id: data?.user?.id || "",
      role: data?.user?.app_metadata?.role || roleFromToken(token),
    } };
  }
  // 无 session = 需邮箱验证（确认链接或验证码）后才能登录
  return { needsConfirm: true, session: null };
}

export async function signInWithSupabase(email: string, password: string): Promise<SupabaseLoginResult> {
  const cfg = await getSupabaseConfig();
  if (!cfg.url || !cfg.publishable_key) throw new Error("Supabase 未配置");
  const base = cfg.url.replace(/\/+$/, "");
  const r = await fetch(`${base}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: cfg.publishable_key },
    body: JSON.stringify({ email, password }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = data?.error_description || data?.msg || data?.error || "登录失败";
    throw new Error(typeof msg === "string" ? msg : "登录失败");
  }
  const token = data?.access_token;
  if (!token) throw new Error("登录未返回会话");
  return {
    access_token: token,
    refresh_token: data?.refresh_token || "",
    email: data?.user?.email || email,
    user_id: data?.user?.id || "",
    role: data?.user?.app_metadata?.role || roleFromToken(token),
  };
}
