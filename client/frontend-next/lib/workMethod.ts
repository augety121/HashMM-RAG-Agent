export type RetrievalMode = "auto" | "naive" | "kg" | "mix" | "global";
export type EffortMode = "fast" | "standard" | "max";
export type ExplicitRunMode = "auto" | "browser" | "deep" | "computer";

export type WorkMethod = {
  retrieval: RetrievalMode;
  effort: EffortMode;
  runMode: ExplicitRunMode;
};

export const DEFAULT_WORK_METHOD: WorkMethod = {
  retrieval: "auto",
  effort: "standard",
  runMode: "auto",
};

const RETRIEVAL_SEQUENCE: RetrievalMode[] = ["auto", "mix", "naive", "kg", "global"];
const EFFORT_SEQUENCE: EffortMode[] = ["standard", "max", "fast"];

export function nextRetrievalMode(current: RetrievalMode): RetrievalMode {
  const index = RETRIEVAL_SEQUENCE.indexOf(current);
  return RETRIEVAL_SEQUENCE[(index + 1) % RETRIEVAL_SEQUENCE.length];
}

export function nextEffortMode(current: EffortMode): EffortMode {
  const index = EFFORT_SEQUENCE.indexOf(current);
  return EFFORT_SEQUENCE[(index + 1) % EFFORT_SEQUENCE.length];
}

export function normalizeEffortMode(value: unknown): EffortMode {
  return value === "fast" || value === "max" ? value : "standard";
}

export function isCustomizedWorkMethod(method: WorkMethod): boolean {
  return method.retrieval !== "auto"
    || method.effort !== "standard"
    || method.runMode !== "auto";
}

export function runModeFlags(mode: ExplicitRunMode) {
  return {
    browser: mode === "browser",
    deep: mode === "deep",
    computer: mode === "computer",
  };
}
