/** Browser session storage for static and OpenID Connect credentials. */
export type Session = Readonly<{ kind: "token" | "oidc"; access_token: string; refresh_token?: string; id_token?: string; expires_at?: number }>;
export type PendingLogin = Readonly<{ state: string; nonce: string; verifier: string; returnTo: string }>;
const SESSION_KEY = "telescreen.session";
const TOKEN_KEY = "telescreen.token";
const PENDING_KEY = "telescreen.oidc.pending";

/** Read the current session, migrating the legacy static token once. */
export function getSession(): Session | null {
  const stored = globalThis.sessionStorage?.getItem(SESSION_KEY);
  if (stored !== null && stored !== undefined) {
    try { const value: unknown = JSON.parse(stored); if (isSession(value)) return value; } catch { clearSession(); }
  }
  const legacy = globalThis.sessionStorage?.getItem(TOKEN_KEY);
  if (legacy) { setSession({ kind: "token", access_token: legacy }); globalThis.sessionStorage.removeItem(TOKEN_KEY); return { kind: "token", access_token: legacy }; }
  return null;
}
/** Store a browser session. */
export function setSession(session: Session): void { globalThis.sessionStorage?.setItem(SESSION_KEY, JSON.stringify(session)); globalThis.sessionStorage?.removeItem(TOKEN_KEY); }
/** Clear all authentication state. */
export function clearSession(): void { globalThis.sessionStorage?.removeItem(SESSION_KEY); globalThis.sessionStorage?.removeItem(TOKEN_KEY); globalThis.sessionStorage?.removeItem(PENDING_KEY); }
/** Save the one-time PKCE transaction. */
export function setPendingLogin(value: PendingLogin): void { globalThis.sessionStorage?.setItem(PENDING_KEY, JSON.stringify(value)); }
/** Read the one-time PKCE transaction. */
export function getPendingLogin(): PendingLogin | null {
  try { const value: unknown = JSON.parse(globalThis.sessionStorage?.getItem(PENDING_KEY) ?? "null"); return isPendingLogin(value) ? value : null; } catch { return null; }
}
/** Remove the one-time PKCE transaction. */
export function clearPendingLogin(): void { globalThis.sessionStorage?.removeItem(PENDING_KEY); }
/** Restrict post-login navigation to same-origin absolute paths. */
export function safeReturnTo(value: string | null): string { return value !== null && value.startsWith("/") && !value.startsWith("//") ? value : "/issues"; }
function isSession(value: unknown): value is Session { return typeof value === "object" && value !== null && "kind" in value && (value.kind === "token" || value.kind === "oidc") && "access_token" in value && typeof value.access_token === "string"; }
function isPendingLogin(value: unknown): value is PendingLogin { return typeof value === "object" && value !== null && "state" in value && typeof value.state === "string" && "nonce" in value && typeof value.nonce === "string" && "verifier" in value && typeof value.verifier === "string" && "returnTo" in value && typeof value.returnTo === "string"; }
