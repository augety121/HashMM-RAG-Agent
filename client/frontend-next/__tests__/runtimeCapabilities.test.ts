import { describe, expect, it } from "vitest";
import {
  capabilityPresentation,
  capabilitySurfaceLabels,
  capabilityTruthPresentation,
  isCapabilityVisible,
} from "../lib/runtimeCapabilities";
import type { RuntimeCapability } from "../lib/api";

const capability = (overrides: Partial<RuntimeCapability> = {}): RuntimeCapability => ({
  id: "browser_use",
  title: "浏览器",
  description: "",
  state: "ready",
  reason: "",
  enabled: true,
  wired: true,
  requires_desktop: true,
  active_tools: ["browser_open"],
  missing_tools: [],
  surfaces: { chat: "direct" },
  entrypoints: [],
  tests: [],
  availability: "available",
  visibility: "user",
  diagnostic_only: false,
  production_ready: true,
  ...overrides,
});

describe("runtime capability presentation", () => {
  it("does not present setup or missing wiring as ready", () => {
    expect(capabilityPresentation("ready")).toEqual({ label: "可直接使用", tone: "success" });
    expect(capabilityPresentation("setup_required").label).toBe("需要配置");
    expect(capabilityPresentation("unavailable")).toEqual({ label: "尚未接通", tone: "error" });
  });

  it("uses product-facing surface labels", () => {
    expect(capabilitySurfaceLabels({ chat: "direct", desktop: "native", app: "relay" }))
      .toEqual(["对话", "桌面端", "App"]);
  });

  it("does not expose diagnostics to ordinary users or confuse ready state with evidence", () => {
    expect(isCapabilityVisible(capability(), "user")).toBe(true);
    expect(isCapabilityVisible(capability({ visibility: "diagnostic" }), "user")).toBe(false);
    expect(isCapabilityVisible(capability({ visibility: "diagnostic" }), "admin")).toBe(true);
    expect(capabilityTruthPresentation(capability())).toEqual({
      label: "已核验可用",
      tone: "success",
    });
    expect(capabilityTruthPresentation(capability({
      production_ready: false,
      availability: "unavailable",
    }))).toEqual({ label: "尚未接通", tone: "error" });
  });
});
