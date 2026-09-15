// Shared parent relationships for desktop workspaces.  Keeping this map out
// of the individual views makes sidebar highlighting and breadcrumb back
// actions agree on the same navigation contract.
export const DESKTOP_PARENTS: Record<string, string> = {
  "work-detail": "work-active",
  routing: "hub-agents", evolution: "hub-agents", discovery: "hub-agents",
  usage: "hub-operations", runs: "hub-operations", quality: "hub-operations", scheduled: "hub-operations",
  audit: "hub-operations", collab: "hub-operations", advanced: "hub-operations", selftest: "hub-operations",
  docstudio: "hub-knowledge", memory: "hub-knowledge",
  backend: "hub-device", terminal: "hub-device", remote: "hub-device",
  workbench: "plugins", files: "workbench",
};

export function desktopParentView(view: string | null | undefined): string | null {
  if (!view) return null;
  return DESKTOP_PARENTS[view] || null;
}
