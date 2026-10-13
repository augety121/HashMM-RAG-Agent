import { describe, expect, it } from "vitest";
import { initialInspectorOpen } from "@/lib/inspectorStartup";

describe("inspector startup policy", () => {
  it("starts closed even when an older process persisted an open inspector", () => {
    expect(initialInspectorOpen("1")).toBe(false);
    expect(initialInspectorOpen("true")).toBe(false);
    expect(initialInspectorOpen(null)).toBe(false);
  });
});
