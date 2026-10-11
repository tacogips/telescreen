/** Test helpers for the settings pages: routed render and a fetch mock with an /api/auth/me permission set. */
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { render } from "@solidjs/testing-library";
import { vi, type MockInstance } from "vitest";
import { setSession } from "../../auth/session";
import SettingsPage from "./index";

export type FetchHandler = (url: string, init: RequestInit | undefined) => Response | undefined;

/** `permissions === null` renders without a session, so `/api/auth/me` is never requested. */
export function mockApi(permissions: readonly string[] | null, handler: FetchHandler): MockInstance<typeof fetch> {
  if (permissions !== null) setSession({ kind: "token", access_token: "test-session" });
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/auth/me") return Response.json({ kind: "token", member: { handle: "alex", display_name: "Alex Morgan" }, role: "admin", permissions: permissions ?? [] });
    return handler(url, init) ?? Response.json({ error: { code: "not_found", detail: "Not found." } }, { status: 404 });
  });
}

export function renderSettings(path: string): void {
  const history = createMemoryHistory();
  history.set({ value: path });
  render(() => <MemoryRouter history={history}><Route path="/settings" component={SettingsPage} /><Route path="/settings/:id" component={SettingsPage} /><Route path="/settings/:id/:sub" component={SettingsPage} /></MemoryRouter>);
}

export async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

export function requestBody(init: RequestInit | undefined): unknown {
  return typeof init?.body === "string" ? JSON.parse(init.body) as unknown : undefined;
}
