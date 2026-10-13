import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  bindRequestToCurrentSession,
  ensureFreshToken,
  executeConversationCode,
  validateBackendSession,
} from "../lib/api";
import { saveAuth, setTokens, useStore } from "../lib/store";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  useStore.setState({
    token: null,
    refreshToken: null,
    user: null,
    authSessionState: "anonymous",
    loginOpen: false,
    sessions: [],
    sid: null,
    backendOnline: true,
  });
});

afterEach(() => {
  delete (window as any).hashmmDesktop;
});

function jwt(exp: number, subject: string): string {
  const encode = (value: object) => btoa(JSON.stringify(value))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  return `${encode({ alg: "none", typ: "JWT" })}.${encode({ exp, sub: subject })}.signature`;
}

describe("desktop auth session lifecycle", () => {
  it("rebinds a prebuilt authenticated request after proactive rotation", () => {
    const init = {
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer stale-access",
      },
    };

    const rebound = bindRequestToCurrentSession(init, "fresh-access");

    expect(new Headers(rebound?.headers).get("Authorization")).toBe("Bearer fresh-access");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer stale-access");
  });

  it("does not attach the active account to an anonymous login request", () => {
    const init = { method: "POST", headers: { "Content-Type": "application/json" } };
    const rebound = bindRequestToCurrentSession(init, "active-access");

    expect(new Headers(rebound?.headers).has("Authorization")).toBe(false);
  });

  it("replaces prior-account credentials and closes the login modal atomically", () => {
    localStorage.setItem("hmm_refresh", "prior-account-refresh");
    useStore.setState({ refreshToken: "prior-account-refresh", loginOpen: true });

    saveAuth("new-access", { id: "user-2", username: "user-2", role: "user" }, "new-refresh");

    expect(localStorage.getItem("hmm_token")).toBe("new-access");
    expect(localStorage.getItem("hmm_refresh")).toBe("new-refresh");
    expect(useStore.getState().refreshToken).toBe("new-refresh");
    expect(useStore.getState().loginOpen).toBe(false);
  });

  it("keeps the desktop refresh credential in the main-process vault", async () => {
    const authSessionSave = vi.fn(async () => ({ ok: true, stored: true }));
    Object.defineProperty(window, "hashmmDesktop", {
      configurable: true,
      value: { authSessionSave, authSessionClear: vi.fn(async () => ({ ok: true })) },
    });
    localStorage.setItem("hmm_refresh", "legacy-renderer-secret");

    saveAuth("desktop-access", { id: "desktop-user", username: "desktop-user", role: "user" }, "desktop-refresh");
    await Promise.resolve();

    expect(authSessionSave).toHaveBeenCalledWith("desktop-refresh", "desktop-user");
    expect(localStorage.getItem("hmm_refresh")).toBeNull();
    expect(useStore.getState().refreshToken).toBeNull();
  });

  it("uses the main-process single-flight refresh without exposing the rotated secret", async () => {
    const expired = jwt(1, "desktop-user");
    const fresh = jwt(Math.floor(Date.now() / 1000) + 3600, "desktop-user");
    const authSessionRefresh = vi.fn(async () => ({
      ok: true, status: "accepted", token: fresh,
    }));
    Object.defineProperty(window, "hashmmDesktop", {
      configurable: true,
      value: { authSessionRefresh },
    });
    useStore.setState({
      token: expired,
      refreshToken: null,
      user: { id: "desktop-user", username: "desktop-user", role: "user" },
      authSessionState: "authenticated",
    });
    localStorage.setItem("hmm_token", expired);

    await ensureFreshToken();

    expect(authSessionRefresh).toHaveBeenCalledTimes(1);
    expect(useStore.getState().token).toBe(fresh);
    expect(useStore.getState().refreshToken).toBeNull();
    expect(localStorage.getItem("hmm_refresh")).toBeNull();
  });

  it("never reuses a stale refresh credential when a login response omits one", () => {
    localStorage.setItem("hmm_refresh", "prior-account-refresh");
    useStore.setState({ refreshToken: "prior-account-refresh", loginOpen: true });

    saveAuth("access-without-refresh", { id: "user-3", username: "user-3", role: "user" });

    expect(localStorage.getItem("hmm_refresh")).toBeNull();
    expect(useStore.getState().refreshToken).toBeNull();
    expect(useStore.getState().loginOpen).toBe(false);
  });

  it("persists rotated refresh credentials but preserves one when no update was returned", () => {
    setTokens("access-1", "refresh-1");
    setTokens("access-2");
    expect(localStorage.getItem("hmm_refresh")).toBe("refresh-1");

    setTokens("access-3", "refresh-2");
    expect(localStorage.getItem("hmm_refresh")).toBe("refresh-2");
    expect(useStore.getState().refreshToken).toBe("refresh-2");
    expect(useStore.getState().authSessionState).toBe("authenticated");
  });

  it("distinguishes offline-valid from a server-confirmed reauthentication boundary", async () => {
    const expired = jwt(1, "user-1");
    setTokens(expired, "refresh-1");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));

    await ensureFreshToken();
    expect(useStore.getState().token).toBe(expired);
    expect(useStore.getState().authSessionState).toBe("offline-valid");
    expect(useStore.getState().loginOpen).toBe(false);

    useStore.getState().requireReauth();
    expect(useStore.getState().token).toBeNull();
    expect(useStore.getState().authSessionState).toBe("reauth-required");
    expect(useStore.getState().loginOpen).toBe(true);
  });

  it("sends the rotated access token on the request that triggered proactive refresh", async () => {
    const expired = jwt(1, "user-1");
    const fresh = jwt(Math.floor(Date.now() / 1000) + 3600, "user-1");
    setTokens(expired, "refresh-1");

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/refresh") {
        return new Response(JSON.stringify({ token: fresh, refresh_token: "refresh-2" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${fresh}`);
      return new Response(JSON.stringify({ ok: true, status: "done", output: "ready" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(executeConversationCode("conversation-1", "print('ready')")).resolves.toMatchObject({
      ok: true,
      status: "done",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useStore.getState().refreshToken).toBe("refresh-2");
  });

  it("does not let an older in-flight refresh overwrite a newer login session", async () => {
    const expired = jwt(1, "old-user");
    const oldFresh = jwt(Math.floor(Date.now() / 1000) + 3600, "old-user");
    const newAccess = jwt(Math.floor(Date.now() / 1000) + 3600, "new-user");
    setTokens(expired, "old-refresh");

    let releaseRefresh!: () => void;
    const gate = new Promise<void>(resolve => { releaseRefresh = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) !== "/api/auth/refresh") throw new Error("unexpected request");
      await gate;
      return new Response(JSON.stringify({ token: oldFresh, refresh_token: "old-refresh-rotated" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));

    const refreshing = ensureFreshToken();
    await Promise.resolve();
    useStore.setState({ token: newAccess, refreshToken: "new-refresh" });
    localStorage.setItem("hmm_token", newAccess);
    localStorage.setItem("hmm_refresh", "new-refresh");
    releaseRefresh();
    await refreshing;

    expect(useStore.getState().token).toBe(newAccess);
    expect(useStore.getState().refreshToken).toBe("new-refresh");
    expect(localStorage.getItem("hmm_refresh")).toBe("new-refresh");
  });

  it("does not rotate directly with Supabase after a reachable backend rejects refresh", async () => {
    const expired = jwt(1, "user-1");
    setTokens(expired, "refresh-rejected-by-backend");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/auth/refresh");
      return new Response(JSON.stringify({ detail: "invalid refresh" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await ensureFreshToken();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(useStore.getState().token).toBe(expired);
    expect(useStore.getState().refreshToken).toBe("refresh-rejected-by-backend");
  });

  it("distinguishes an identity mismatch from an offline backend before login commit", async () => {
    const contract = () => new Response(JSON.stringify({
      schema: "hashmm.identity-contract.v1",
      provider: "supabase",
      provider_ready: true,
      project_ref: "project-a",
      backend_url: "https://hashmm.example.test",
    }), { status: 200, headers: { "Content-Type": "application/json" } });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("identity-contract") ? contract() : new Response("{}", { status: 401 })
    ));
    await expect(validateBackendSession("supabase-access")).resolves.toBe("rejected");

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("identity-contract") ? contract() : new Response("{}", { status: 503 })
    ));
    await expect(validateBackendSession("supabase-access")).resolves.toBe("unavailable");
  });

  it("accepts a backend-verified session when optional identity diagnostics are incomplete", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("identity-contract")
        ? new Response(JSON.stringify({
          schema: "hashmm.identity-contract.v1",
          provider: "supabase",
          provider_ready: false,
        }), { status: 200, headers: { "Content-Type": "application/json" } })
        : new Response(JSON.stringify({ id: "sb-user" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
    ));

    await expect(validateBackendSession("supabase-access")).resolves.toBe("accepted");
  });
});
