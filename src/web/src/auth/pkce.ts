/** WebCrypto helpers for the OpenID Connect PKCE flow. */
export function randomUrlSafe(bytes: number): string {
  const values = new Uint8Array(bytes);
  globalThis.crypto.getRandomValues(values);
  return btoa(String.fromCharCode(...values)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Create a 64-character PKCE verifier. */
export function createVerifier(): string { return randomUrlSafe(48); }

/** Derive an S256 PKCE challenge from a verifier. */
export async function challengeS256(verifier: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
