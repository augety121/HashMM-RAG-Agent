export interface TodoManifestVersion {
  id: string;
  revision: number;
}

export function normalizeTodoManifestVersion(value: {
  manifest_id?: string;
  revision?: number;
}): TodoManifestVersion | null {
  const id = String(value.manifest_id || "").trim();
  if (!id) return null;
  const revision = Number.isFinite(Number(value.revision))
    ? Math.max(0, Number(value.revision))
    : 0;
  return { id, revision };
}

export function shouldAcceptTodoManifest(
  current: TodoManifestVersion | null,
  incoming: TodoManifestVersion | null,
): boolean {
  if (!current) return true;
  if (!incoming) return false;
  return incoming.id === current.id && incoming.revision >= current.revision;
}

export function flattenRelativeAttachmentName(
  relativePath: string,
  fallbackName: string,
): string {
  return String(relativePath || fallbackName)
    .replace(/^[./\\]+/, "")
    .replace(/[\\/]+/g, "__")
    .replace(/[<>:"|?*\u0000-\u001f]/g, "_")
    .slice(-220) || fallbackName;
}
