/** Scope canvas history by conversation and filename to prevent cross-chat mixing. */
export function canvasVersionScopeKey(convId: string | undefined, filename: string): string {
  return `${convId || "local"}|${filename}`;
}
