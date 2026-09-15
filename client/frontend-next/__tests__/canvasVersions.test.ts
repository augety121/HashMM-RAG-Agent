import { describe, expect, it } from "vitest";
import { canvasVersionScopeKey } from "../lib/canvasVersions";

describe("canvas version scope", () => {
  it("does not mix same-named artifacts from different conversations", () => {
    expect(canvasVersionScopeKey("conv-a", "工作画布.html"))
      .not.toBe(canvasVersionScopeKey("conv-b", "工作画布.html"));
  });

  it("keeps a deterministic local-draft scope", () => {
    expect(canvasVersionScopeKey(undefined, "draft.html")).toBe("local|draft.html");
  });
});
