/** OpenID Connect Authorization Code + PKCE browser flow. */
import { ApiError, apiFetch } from "../api/client";
import type { AuthConfig, OidcConfig } from "../api/types";
import { challengeS256, createVerifier, randomUrlSafe } from "./pkce";
import { clearPendingLogin, clearSession, getPendingLogin, getSession, safeReturnTo, setPendingLogin, setSession } from "./session";

/** Redirect to the configured identity provider using S256 PKCE. */
export async function beginLogin(config: OidcConfig, returnTo: string | null): Promise<void> {
  const state = randomUrlSafe(32); const nonce = randomUrlSafe(32); const verifier = createVerifier();
  const destination = safeReturnTo(returnTo); setPendingLogin({ state, nonce, verifier, returnTo: destination });
  const url = new URL(config.authorization_endpoint); url.searchParams.set("response_type", "code"); url.searchParams.set("client_id", config.client_id); url.searchParams.set("redirect_uri", `${location.origin}/auth/callback`); url.searchParams.set("scope", config.scopes); url.searchParams.set("state", state); url.searchParams.set("nonce", nonce); url.searchParams.set("code_challenge", await challengeS256(verifier)); url.searchParams.set("code_challenge_method", "S256"); location.assign(url.toString());
}

/** Validate the callback, exchange the code and establish an application session. */
export async function completeLogin(callback: URL): Promise<string> {
  const pending = getPendingLogin();
  try {
    if (pending === null || callback.searchParams.get("state") !== pending.state) throw new ApiError(400, "invalid_state", "The sign-in response could not be verified.");
    const config = await apiFetch<AuthConfig>("/api/auth/config");
    if (config.oidc === null) throw new ApiError(503, "idp_unavailable", "Single sign-on is not configured.");
    const response = await fetch(config.oidc.token_endpoint, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ grant_type: "authorization_code", code: callback.searchParams.get("code") ?? "", redirect_uri: `${location.origin}/auth/callback`, client_id: config.oidc.client_id, code_verifier: pending.verifier }) });
    if (!response.ok) throw new ApiError(response.status, "invalid_token", "The identity provider rejected the sign-in.");
    const token: unknown = await response.json();
    if (!isTokenResponse(token)) throw new ApiError(401, "invalid_token", "The identity provider returned an invalid response.");
    const claims = decodePayload(token.id_token);
    if (claims.nonce !== pending.nonce) throw new ApiError(401, "invalid_token", "The sign-in response could not be verified.");
    const session = { kind: "oidc" as const, access_token: token.access_token, id_token: token.id_token, ...(token.refresh_token === undefined ? {} : { refresh_token: token.refresh_token }), expires_at: Date.now() + token.expires_in * 1000 };
    setSession(session); await apiFetch("/api/auth/session", { method: "POST" }); return safeReturnTo(pending.returnTo);
  } finally { clearPendingLogin(); }
}

/** Refresh an OIDC session when it is within 30 seconds of expiry. */
export async function refreshIfNeeded(): Promise<void> {
  const session = getSession(); if (session?.kind !== "oidc" || session.expires_at === undefined || session.expires_at > Date.now() + 30_000) return;
  if (!session.refresh_token) { clearSession(); throw new ApiError(401, "invalid_token", "Your session has expired."); }
  try {
    const config = await apiFetch<AuthConfig>("/api/auth/config"); if (config.oidc === null) throw new Error("No issuer");
    const response = await fetch(config.oidc.token_endpoint, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: session.refresh_token, client_id: config.oidc.client_id }) });
    if (!response.ok) throw new Error("Refresh failed"); const value: unknown = await response.json(); if (!isRefreshResponse(value)) throw new Error("Invalid refresh response");
    setSession({ ...session, access_token: value.access_token, ...(value.refresh_token === undefined ? {} : { refresh_token: value.refresh_token }), expires_at: Date.now() + value.expires_in * 1000 });
  } catch { clearSession(); throw new ApiError(401, "invalid_token", "Your session has expired."); }
}

/** Clear the local session and return to the application login page or IdP. */
export async function logout(config?: OidcConfig | null): Promise<void> {
  const idToken = getSession()?.id_token; clearSession();
  if (config?.end_session_endpoint) { const url = new URL(config.end_session_endpoint); url.searchParams.set("client_id", config.client_id); url.searchParams.set("post_logout_redirect_uri", `${location.origin}/login`); if (idToken) url.searchParams.set("id_token_hint", idToken); location.assign(url.toString()); }
  else location.assign("/login");
}

function isTokenResponse(value: unknown): value is Readonly<{ access_token: string; id_token: string; refresh_token?: string; expires_in: number }> { return typeof value === "object" && value !== null && "access_token" in value && typeof value.access_token === "string" && "id_token" in value && typeof value.id_token === "string" && "expires_in" in value && typeof value.expires_in === "number" && (!("refresh_token" in value) || typeof value.refresh_token === "string"); }
function isRefreshResponse(value: unknown): value is Readonly<{ access_token: string; id_token?: string; refresh_token?: string; expires_in: number }> { return typeof value === "object" && value !== null && "access_token" in value && typeof value.access_token === "string" && "expires_in" in value && typeof value.expires_in === "number" && (!("id_token" in value) || typeof value.id_token === "string") && (!("refresh_token" in value) || typeof value.refresh_token === "string"); }
function decodePayload(token: string): Readonly<Record<string, unknown>> { const segment = token.split(".")[1]; if (!segment) throw new ApiError(401, "invalid_token", "The identity provider returned an invalid response."); try { return JSON.parse(atob(segment.replace(/-/g, "+").replace(/_/g, "/"))) as Readonly<Record<string, unknown>>; } catch { throw new ApiError(401, "invalid_token", "The identity provider returned an invalid response."); } }
