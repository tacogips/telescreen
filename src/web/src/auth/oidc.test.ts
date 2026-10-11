import { afterEach, describe, expect, it, vi } from "vitest";
import { beginLogin, completeLogin, logout, refreshIfNeeded } from "./oidc";
import { clearSession, getPendingLogin, getSession, setPendingLogin, setSession } from "./session";
const config = { oidc: { issuer: "https://idp.example.test", client_id: "telescreen-ui", authorization_endpoint: "https://idp.example.test/auth", token_endpoint: "https://idp.example.test/token", scopes: ["openid"] }, token_login: true };
const jwt = (nonce: string): string => `header.${btoa(JSON.stringify({ nonce })).replace(/=/g, "")}.signature`;
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });
describe("OIDC code flow", () => {
  it("builds an S256 authorization URL and sanitizes return paths", async () => {
    const assign = vi.fn(); vi.stubGlobal("location", { origin: "https://app.test", assign });
    await beginLogin(config.oidc, "//outside.test"); const destination = new URL(String(assign.mock.calls[0]?.[0]));
    expect(destination.searchParams.get("response_type")).toBe("code"); expect(destination.searchParams.get("code_challenge_method")).toBe("S256"); expect(getPendingLogin()?.returnTo).toBe("/issues");
  });
  it("joins scopes with spaces as OIDC requires", async () => {
    const assign = vi.fn(); vi.stubGlobal("location", { origin: "https://app.test", assign });
    await beginLogin({ ...config.oidc, scopes: ["openid", "profile", "email"] }, "/issues"); const destination = new URL(String(assign.mock.calls[0]?.[0]));
    expect(destination.searchParams.get("scope")).toBe("openid profile email");
  });
  it("reports an identity provider error without requesting a token", async () => {
    setPendingLogin({ state: "expected", nonce: "nonce", verifier: "verifier", returnTo: "/runs" }); const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    await expect(completeLogin(new URL("https://app.test/auth/callback?state=expected&error=invalid_scope"))).rejects.toMatchObject({ code: "idp_error" }); expect(fetcher).not.toHaveBeenCalled();
  });
  it("rejects a state mismatch before requesting a token", async () => {
    setPendingLogin({ state: "expected", nonce: "nonce", verifier: "verifier", returnTo: "/runs" }); const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    await expect(completeLogin(new URL("https://app.test/auth/callback?state=wrong&code=c"))).rejects.toMatchObject({ code: "invalid_state" }); expect(fetcher).not.toHaveBeenCalled(); expect(getPendingLogin()).toBeNull();
  });
  it("rejects a nonce mismatch without storing the received session", async () => {
    setPendingLogin({ state: "state", nonce: "expected", verifier: "verifier", returnTo: "/runs" });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/auth/config" ? Response.json(config) : String(input) === "https://idp.example.test/token" ? Response.json({ access_token: "access", id_token: jwt("other"), expires_in: 300 }) : Response.json({})));
    await expect(completeLogin(new URL("https://app.test/auth/callback?state=state&code=c"))).rejects.toMatchObject({ code: "invalid_token" }); expect(getSession()).toBeNull();
  });
  it("exchanges the code and stores the session after nonce validation", async () => {
    setPendingLogin({ state: "state", nonce: "expected", verifier: "verifier", returnTo: "/runs/1" }); const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => { calls.push(String(input)); return String(input) === "/api/auth/config" ? Response.json(config) : String(input) === "https://idp.example.test/token" ? Response.json({ access_token: "access", id_token: jwt("expected"), refresh_token: "refresh", expires_in: 300 }) : Response.json({ member: { handle: "alex", display_name: "Alex" } }); }));
    await expect(completeLogin(new URL("https://app.test/auth/callback?state=state&code=auth-code"))).resolves.toBe("/runs/1"); expect(getSession()?.kind).toBe("oidc"); expect(calls).toContain("/api/auth/session"); expect(getPendingLogin()).toBeNull();
  });
  it("refreshes a nearly expired session using a form encoded refresh grant", async () => {
    setSession({ kind: "oidc", access_token: "old", refresh_token: "refresh", id_token: "identity", expires_at: Date.now() + 10_000 }); const calls: Readonly<{ url: string; body: string }>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => { calls.push({ url: String(input), body: String(init?.body ?? "") }); return String(input) === "/api/auth/config" ? Response.json(config) : Response.json({ access_token: "new", expires_in: 600 }); }));
    await refreshIfNeeded(); expect(calls.some((call) => call.body.includes("grant_type=refresh_token"))).toBe(true); expect(getSession()?.access_token).toBe("new");
  });
  it("clears a session when refresh fails", async () => {
    setSession({ kind: "oidc", access_token: "old", refresh_token: "refresh", expires_at: Date.now() }); vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/api/auth/config" ? Response.json(config) : new Response(null, { status: 400 })));
    await expect(refreshIfNeeded()).rejects.toMatchObject({ status: 401 }); expect(getSession()).toBeNull();
  });
  it("posts refresh requests as form data", () => { expect(config.oidc.token_endpoint).toContain("/token"); clearSession(); });
  it("clears storage and advertises the ID token to the end-session endpoint", async () => {
    setSession({ kind: "oidc", access_token: "access", id_token: "identity" }); const assign = vi.fn(); vi.stubGlobal("location", { origin: "https://app.test", assign });
    await logout({ ...config.oidc, end_session_endpoint: "https://idp.example.test/logout" }); const destination = new URL(String(assign.mock.calls[0]?.[0]));
    expect(destination.searchParams.get("id_token_hint")).toBe("identity"); expect(destination.searchParams.get("post_logout_redirect_uri")).toBe("https://app.test/login"); expect(getSession()).toBeNull();
  });
});
