import { afterEach, describe, expect, it, vi } from "vitest";
import { contextInspect } from "@/lib/api";
import { useStore } from "@/lib/store";

describe("context inspector authentication", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useStore.getState().set({ token: null, refreshToken: null, backendOnline: null });
  });

  it("sends the current access token when reading system context", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, blocks: [], total_chars: 0, tips: [] }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    useStore.getState().set({ token: "context-access-token", backendOnline: true });

    await contextInspect("owned-conversation");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/context/inspect?conv_id=owned-conversation");
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: "Bearer context-access-token",
      "Content-Type": "application/json",
    });
  });

  it("does not deduplicate an authenticated GET across account tokens", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, blocks: [], total_chars: 0, tips: [] }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    useStore.getState().set({ token: "owner-a", backendOnline: true });
    await contextInspect("owner-switch-conversation");
    useStore.getState().set({ token: "owner-b", backendOnline: true });
    await contextInspect("owner-switch-conversation");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect((fetchMock.mock.calls[0][1] as RequestInit).headers).toMatchObject({ Authorization: "Bearer owner-a" });
    expect((fetchMock.mock.calls[1][1] as RequestInit).headers).toMatchObject({ Authorization: "Bearer owner-b" });
  });
});
