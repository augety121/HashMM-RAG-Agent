import { beforeEach, describe, expect, it, vi } from "vitest";
import { executeConversationCode } from "../lib/api";
import { setTokens, useStore } from "../lib/store";

beforeEach(() => {
  localStorage.clear();
  useStore.setState({ token: null, refreshToken: null });
  vi.restoreAllMocks();
});

describe("conversation execution contract", () => {
  it("returns a stable status and uses the active account token", async () => {
    setTokens("header.payload.signature", "refresh");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer header.payload.signature");
      return new Response(JSON.stringify({ ok: true, status: "done", output: "42" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(executeConversationCode("conv/one", "print(42)")).resolves.toMatchObject({
      ok: true,
      status: "done",
      output: "42",
    });
    expect(String(fetchMock.mock.calls[0][0])).toContain("conv%2Fone/execute");
  });
});
