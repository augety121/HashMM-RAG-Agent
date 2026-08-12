import { useStore } from "./store";
import type { BrowserEvidenceLocator } from "./desktop";

/** Only http(s) links are allowed into the embedded browser surface. */
export function normalizeInspectorUrl(value: string): string | null {
  const input = String(value || "").trim();
  if (!input) return null;
  try {
    const parsed = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(input) ? input : `https://${input}`);
    if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password) return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

/** Single entry point used by Chat links and browser controls. */
export function openBrowserInInspector(value: string, title?: string): boolean {
  const url = normalizeInspectorUrl(value);
  if (!url) return false;
  try { localStorage.setItem("hmm_right_panel", "1"); } catch { /* */ }
  useStore.getState().set({
    rightPanelOpen: true,
    inspectorTab: "browser",
    browserPanel: { url, title: title || "" },
  });
  return true;
}

/** Open a persisted EvidenceRef and relocate/highlight its original page quote. */
export function openEvidenceInInspector(input: {
  url: string; title?: string; evidenceId: string; convId: string;
  locator?: BrowserEvidenceLocator; verifyOnOpen?: boolean;
}): boolean {
  const url = normalizeInspectorUrl(input.url);
  if (!url || !input.evidenceId || !input.convId) return false;
  try { localStorage.setItem("hmm_right_panel", "1"); } catch { /* */ }
  useStore.getState().set({
    rightPanelOpen: true,
    inspectorTab: "browser",
    browserPanel: {
      url,
      title: input.title || "",
      evidenceId: input.evidenceId,
      convId: input.convId,
      locator: input.locator,
      verifyOnOpen: input.verifyOnOpen !== false,
    },
  });
  return true;
}
