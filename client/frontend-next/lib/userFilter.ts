// lib/userFilter.ts — 用户管理：搜索/角色过滤/排序/统计纯逻辑（V103.90，frontend-next 真 UI）。
// 给后台用户管理加大厂级深度（按关键词/角色过滤、按名字/角色/时间排序、角色分布统计）。
// 纯函数，便于 tsc 编译后单测。

import type { User } from "./types";

export type RoleFilter = "all" | "admin" | "user" | "viewer";
export type UserSort = "name" | "role" | "created";

/** 按关键词（用户名或显示名，大小写不敏感）+ 角色过滤。 */
export function filterUsers(users: User[], opts: { query?: string; role?: RoleFilter } = {}): User[] {
  const arr = Array.isArray(users) ? users : [];
  const q = String(opts.query || "").trim().toLowerCase();
  const role = opts.role || "all";
  return arr.filter((u) => {
    if (role !== "all" && u.role !== role) return false;
    if (q) {
      const hay = `${u.username || ""} ${u.display_name || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

const ROLE_ORDER: Record<string, number> = { admin: 0, user: 1, viewer: 2 };

/** 排序（不改原数组）。name：按显示名/用户名；role：管理员→用户→只读；created：新→旧。 */
export function sortUsers(users: User[], by: UserSort): User[] {
  const arr = (Array.isArray(users) ? users : []).slice();
  if (by === "name") {
    arr.sort((a, b) => (a.display_name || a.username || "").localeCompare(b.display_name || b.username || "", "zh"));
  } else if (by === "role") {
    arr.sort((a, b) => (ROLE_ORDER[a.role] ?? 9) - (ROLE_ORDER[b.role] ?? 9));
  } else if (by === "created") {
    arr.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  }
  return arr;
}

export interface RoleCounts { total: number; admin: number; user: number; viewer: number; }

/** 角色分布统计。 */
export function userRoleCounts(users: User[]): RoleCounts {
  const arr = Array.isArray(users) ? users : [];
  let admin = 0, user = 0, viewer = 0;
  for (const u of arr) {
    if (u.role === "admin") admin++;
    else if (u.role === "viewer") viewer++;
    else user++;
  }
  return { total: arr.length, admin, user, viewer };
}
