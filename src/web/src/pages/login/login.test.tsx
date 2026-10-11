import { cleanup, render, screen } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi } from "vitest";
import LoginPage from ".";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); });
it("shows SSO and token choices according to auth configuration", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ oidc: { issuer: "https://idp.example.test", client_id: "ui", authorization_endpoint: "https://idp.example.test/auth", token_endpoint: "https://idp.example.test/token", scopes: "openid" }, token_login: true })));
  render(() => <MemoryRouter><Route path="/" component={() => <LoginPage/>}/></MemoryRouter>);
  expect(await screen.findByRole("button", { name: "Sign in with SSO" })).toBeInTheDocument(); expect(screen.getByLabelText("API token")).toBeInTheDocument();
});
it("shows the no-role message", () => { const history = createMemoryHistory(); history.set({ value: "/login?error=no_role" }); render(() => <MemoryRouter history={history}><Route path="/login" component={() => <LoginPage/>}/></MemoryRouter>); expect(screen.getByRole("alert")).toHaveTextContent("Your account has no Telescreen role. Ask an administrator."); });
it("hides both sign-in methods when the server disables them", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ oidc: null, token_login: false })));
  render(() => <MemoryRouter><Route path="/" component={() => <LoginPage/>}/></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Sign in to Telescreen" })).toBeInTheDocument(); expect(screen.queryByRole("button", { name: "Sign in with SSO" })).toBeNull(); expect(screen.queryByLabelText("API token")).toBeNull();
});
