"use client";

export type ChatCapability = "browser" | "computer" | "canvas" | "team";

export const CAPABILITY_PREF_KEYS: Record<ChatCapability, string> = {
  browser: "hmm_cap_browser",
  computer: "hmm_cap_computer",
  canvas: "hmm_cap_canvas",
  team: "hmm_cap_team",
};

export const CAPABILITY_PREFS_EVENT = "hashmm-capability-preferences";

export function readCapabilityPrefs(): Record<ChatCapability, boolean> {
  const defaults: Record<ChatCapability, boolean> = {
    browser: true,
    computer: true,
    canvas: true,
    team: true,
  };
  if (typeof window === "undefined") return defaults;
  try {
    for (const key of Object.keys(defaults) as ChatCapability[]) {
      defaults[key] = window.localStorage.getItem(CAPABILITY_PREF_KEYS[key]) !== "0";
    }
  } catch {
    // Private browsing or a locked storage area should not break Chat.
  }
  return defaults;
}

export function writeCapabilityPref(capability: ChatCapability, enabled: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(CAPABILITY_PREF_KEYS[capability], enabled ? "1" : "0");
    window.dispatchEvent(new CustomEvent(CAPABILITY_PREFS_EVENT, {
      detail: { capability, enabled },
    }));
  } catch {
    // The UI remains usable even when persistence is unavailable.
  }
}
