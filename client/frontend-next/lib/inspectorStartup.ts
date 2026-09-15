/**
 * The inspector is task-scoped UI, not a durable preference.  A browser,
 * artifact or context action may open it explicitly, but a fresh desktop
 * process must always expose the Chat canvas first.
 */
export function initialInspectorOpen(_persistedValue?: string | null): boolean {
  return false;
}
