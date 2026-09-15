import { afterEach, describe, expect, it, vi } from "vitest";
import { refreshSupabaseTokenDetailed } from "../lib/supabase";

afterEach(() => vi.unstubAllGlobals());

describe("Supabase refresh classification", () => {
  it("preserves the session when the identity provider is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith("/api/auth/supabase-config")) throw new TypeError("offline");
      throw new TypeError("network unavailable");
    }));
    await expect(refreshSupabaseTokenDetailed("refresh-token")).resolves.toEqual({ status: "unavailable" });
  });

  it("only marks explicit 400/401 responses as a revoked refresh token", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith("/api/auth/supabase-config")) throw new TypeError("use fallback");
      return new Response("{}", { status: 400 });
    }));
    await expect(refreshSupabaseTokenDetailed("revoked-token")).resolves.toEqual({ status: "rejected" });
  });
});
