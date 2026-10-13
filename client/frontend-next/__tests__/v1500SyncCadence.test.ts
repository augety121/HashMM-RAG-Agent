import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

describe("V1500 cross-device reconciliation contract", () => {
  it("uses visibility-aware bounded polling instead of a seven-second loop", () => {
    const source = fs.readFileSync(path.join(process.cwd(), "components", "App.tsx"), "utf8");
    expect(source).not.toContain("setInterval(tick, 7000)");
    expect(source).toContain('document.visibilityState === "hidden" ? 5 * 60_000 : 30_000');
    expect(source).toContain('window.addEventListener("online", reconcileNow)');
  });

  it("bounds API requests and retries only safe reads", () => {
    const source = fs.readFileSync(path.join(process.cwd(), "lib", "api.ts"), "utf8");
    expect(source).toContain("async function fetchWithPolicy");
    expect(source).toContain('const maxAttempts = method === "GET" ? 2 : 1');
    expect(source).toContain('method === "GET" ? 15_000 : 60_000');
    expect(source).toContain("[429, 502, 503, 504]");
  });
});
