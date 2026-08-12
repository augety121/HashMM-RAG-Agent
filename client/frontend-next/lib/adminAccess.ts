export type IdentityProbe = { role?: string } | "offline" | "invalid" | null;
export type AdminSurface = "loading" | "admin" | "user" | "invalid";

/**
 * Decide which control surface may be rendered while /api/auth/me is being
 * resolved.  A cached role is useful when the backend is genuinely offline,
 * but it is never accepted after the server has rejected the token.
 *
 * This is presentation gating only; every administrator API still performs
 * its own server-side authorization check.
 */
export function resolveAdminSurface(probe: IdentityProbe, cachedRole?: string): AdminSurface {
  if (probe === null) return "loading";
  if (probe === "invalid") return "invalid";
  if (probe === "offline") return cachedRole === "admin" ? "admin" : "user";
  return probe.role === "admin" ? "admin" : "user";
}
