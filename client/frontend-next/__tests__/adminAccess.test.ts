import { describe, expect, it } from "vitest";
import { resolveAdminSurface } from "@/lib/adminAccess";

describe("administrator surface role boundary", () => {
  it("does not flash administrator controls before identity resolves", () => {
    expect(resolveAdminSurface(null, "admin")).toBe("loading");
  });

  it("uses a cached role only when the backend is actually offline", () => {
    expect(resolveAdminSurface("offline", "admin")).toBe("admin");
    expect(resolveAdminSurface("offline", "user")).toBe("user");
  });

  it("does not trust a cached administrator role after token rejection", () => {
    expect(resolveAdminSurface("invalid", "admin")).toBe("invalid");
  });

  it("uses the verified server role when available", () => {
    expect(resolveAdminSurface({ role: "admin" }, "user")).toBe("admin");
    expect(resolveAdminSurface({ role: "user" }, "admin")).toBe("user");
  });
});

