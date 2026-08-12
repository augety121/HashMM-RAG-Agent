import { normalizeInspectorUrl } from "../lib/browserInspector";
import { describe, expect, it } from "vitest";

describe("embedded browser URL boundary", () => {
  it("normalizes hostnames and keeps http(s)", () => {
    expect(normalizeInspectorUrl("example.com/a")).toBe("https://example.com/a");
    expect(normalizeInspectorUrl("http://localhost:3000/test")).toBe("http://localhost:3000/test");
  });

  it("rejects active or privileged schemes and credentials", () => {
    expect(normalizeInspectorUrl("javascript:alert(1)")).toBeNull();
    expect(normalizeInspectorUrl("file:///C:/Windows/win.ini")).toBeNull();
    expect(normalizeInspectorUrl("https://user:pass@example.com")).toBeNull();
  });
});
