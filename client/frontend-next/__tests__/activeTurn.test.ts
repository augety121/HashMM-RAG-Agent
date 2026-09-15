import { afterEach, describe, expect, it, vi } from "vitest";
import { getActiveTurn, interruptActiveTurn, steerActiveTurn } from "@/lib/api";
import { useStore } from "@/lib/store";

describe("active conversation turn control", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useStore.getState().set({ token: null, refreshToken: null, backendOnline: null });
  });

  it("scopes read, steer, and interrupt to the exact encoded conversation and turn", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => new Response(
      JSON.stringify(String(input).endsWith("/active-turn")
        ? { active: true, turn: { turn_id: "turn/1", steerable: true } }
        : { accepted: true, duplicate: false, turn_id: "turn/1", message_id: "message-1", status: "interrupting" }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    useStore.getState().set({ token: "turn-access-token", backendOnline: true });

    await getActiveTurn("conversation/1");
    await steerActiveTurn("conversation/1", "turn/1", "增加风险清单", "client-1");
    await interruptActiveTurn("conversation/1", "turn/1");

    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      "/api/conversations/conversation%2F1/active-turn",
      "/api/conversations/conversation%2F1/turns/turn%2F1/steer",
      "/api/conversations/conversation%2F1/turns/turn%2F1/interrupt",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer turn-access-token" });
    }
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ content: "增加风险清单", client_message_id: "client-1" }),
    });
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: "POST" });
  });
});
