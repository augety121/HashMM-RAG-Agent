"use client";

import { useState } from "react";
import {
  nextEffortMode,
  nextRetrievalMode,
  normalizeEffortMode,
  runModeFlags,
  type EffortMode,
  type ExplicitRunMode,
  type RetrievalMode,
} from "@/lib/workMethod";

export function useWorkMethod() {
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("auto");
  const [effortMode, setEffortMode] = useState<EffortMode>(() => {
    if (typeof window === "undefined") return "standard";
    return normalizeEffortMode(localStorage.getItem("hmm_effort"));
  });
  const [runMode, setRunMode] = useState<ExplicitRunMode>("auto");
  const flags = runModeFlags(runMode);

  const cycleEffort = () => setEffortMode((current) => {
    const next = nextEffortMode(current);
    try { localStorage.setItem("hmm_effort", next); } catch { /* storage is optional */ }
    return next;
  });
  const cycleRetrieval = () => setRetrievalMode(nextRetrievalMode);
  return {
    retrievalMode,
    setRetrievalMode,
    cycleRetrieval,
    effortMode,
    setEffortMode,
    cycleEffort,
    runMode,
    setRunMode,
    browserMode: flags.browser,
    deepMode: flags.deep,
    cuMode: flags.computer,
    setBrowserMode: (active: boolean) => setRunMode(active ? "browser" : "auto"),
    setDeepMode: (value: boolean | ((current: boolean) => boolean)) => {
      setRunMode((current) => {
        const next = typeof value === "function" ? value(current === "deep") : value;
        return next ? "deep" : "auto";
      });
    },
    setCuMode: (value: boolean | ((current: boolean) => boolean)) => {
      setRunMode((current) => {
        const next = typeof value === "function" ? value(current === "computer") : value;
        return next ? "computer" : "auto";
      });
    },
  };
}
