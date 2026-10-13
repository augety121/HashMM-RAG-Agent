import type { RuntimeCapability, RuntimeCapabilityState } from "./api";

export type CapabilityTone = "neutral" | "success" | "warning" | "error";

export function capabilityPresentation(state: RuntimeCapabilityState): { label: string; tone: CapabilityTone } {
  switch (state) {
    case "ready": return { label: "可直接使用", tone: "success" };
    case "setup_required": return { label: "需要配置", tone: "warning" };
    case "degraded": return { label: "部分可用", tone: "warning" };
    case "disabled": return { label: "已关闭", tone: "neutral" };
    case "unavailable": return { label: "尚未接通", tone: "error" };
  }
}

export function capabilitySurfaceLabels(surfaces: Record<string, string>): string[] {
  const labels: Record<string, string> = { chat: "对话", desktop: "桌面端", app: "App" };
  return Object.keys(surfaces).filter(key => Boolean(surfaces[key])).map(key => labels[key] || key);
}

export function isCapabilityVisible(
  capability: RuntimeCapability,
  role: "user" | "admin" | "viewer" | undefined,
): boolean {
  if (capability.visibility === "user") return true;
  return role === "admin";
}

export function capabilityTruthPresentation(capability: RuntimeCapability): {
  label: string;
  tone: CapabilityTone;
} {
  if (capability.production_ready) return { label: "已核验可用", tone: "success" };
  if (capability.availability === "degraded") return { label: "部分可用", tone: "warning" };
  if (capability.state === "disabled") return { label: "已关闭", tone: "neutral" };
  return { label: "尚未接通", tone: "error" };
}
