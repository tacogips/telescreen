import { afterEach, describe, expect, it } from "vitest";
import { clearSession, getSession, safeReturnTo, setSession } from "./session";
afterEach(() => sessionStorage.clear());
describe("browser session", () => {
  it("migrates the legacy static token to a session once", () => {
    sessionStorage.setItem("telescreen.token", "legacy-value"); expect(getSession()).toEqual({ kind: "token", access_token: "legacy-value" }); expect(sessionStorage.getItem("telescreen.token")).toBeNull();
  });
  it("accepts only same-origin absolute return paths", () => {
    expect(safeReturnTo("//evil")).toBe("/issues"); expect(safeReturnTo("https://x")).toBe("/issues"); expect(safeReturnTo("/runs/1")).toBe("/runs/1");
  });
  it("clears token and pending transaction storage", () => { setSession({ kind: "token", access_token: "session-value" }); clearSession(); expect(getSession()).toBeNull(); });
});
