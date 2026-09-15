import { describe, expect, it } from "vitest";
import {
  DEFAULT_WORK_METHOD,
  isCustomizedWorkMethod,
  nextEffortMode,
  nextRetrievalMode,
  runModeFlags,
} from "@/lib/workMethod";
import { canSeeProductSurface, productSurfaceLabel } from "@/lib/productSurface";

describe("V477 work method domain", () => {
  it("keeps execution modes mutually exclusive", () => {
    expect(runModeFlags("browser")).toEqual({
      browser: true,
      deep: false,
      computer: false,
    });
    expect(runModeFlags("computer")).toEqual({
      browser: false,
      deep: false,
      computer: true,
    });
  });

  it("uses deterministic preference cycles and customization state", () => {
    expect(nextRetrievalMode("auto")).toBe("mix");
    expect(nextRetrievalMode("global")).toBe("auto");
    expect(nextEffortMode("standard")).toBe("max");
    expect(nextEffortMode("fast")).toBe("standard");
    expect(isCustomizedWorkMethod(DEFAULT_WORK_METHOD)).toBe(false);
    expect(isCustomizedWorkMethod({ ...DEFAULT_WORK_METHOD, runMode: "deep" })).toBe(true);
  });
});

describe("V478 product surface policy", () => {
  it("keeps admin and diagnostic surfaces out of the user product", () => {
    expect(canSeeProductSurface("user", "user")).toBe(true);
    expect(canSeeProductSurface("admin", "user")).toBe(false);
    expect(canSeeProductSurface("diagnostic", "user")).toBe(false);
    expect(canSeeProductSurface("admin", "admin")).toBe(true);
    expect(canSeeProductSurface("diagnostic", "admin")).toBe(true);
    expect(productSurfaceLabel("diagnostic")).toBe("诊断");
  });
});
