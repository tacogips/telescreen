import { describe, expect, it } from "vitest";
import { challengeS256, createVerifier } from "./pkce";

describe("PKCE helpers", () => {
  it("matches the RFC 7636 S256 vector", async () => {
    await expect(challengeS256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).resolves.toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });
  it("creates a 64-character URL-safe verifier", () => {
    const value = createVerifier(); expect(value).toHaveLength(64); expect(value).toMatch(/^[A-Za-z0-9_-]+$/);
  });
});
