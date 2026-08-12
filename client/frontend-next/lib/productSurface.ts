export type ProductAudience = "user" | "admin" | "diagnostic";
export type ProductRole = "user" | "admin" | undefined;

export function canSeeProductSurface(
  audience: ProductAudience,
  role: ProductRole,
): boolean {
  if (audience === "user") return true;
  return role === "admin";
}

export function productSurfaceLabel(audience: ProductAudience): string {
  if (audience === "admin") return "管理员";
  if (audience === "diagnostic") return "诊断";
  return "";
}
