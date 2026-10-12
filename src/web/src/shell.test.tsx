import { render, screen, fireEvent, within, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Component } from "solid-js";
import { ApiError, apiFetch, setToken } from "./api/client";
import { AppShell } from "./components/shell/AppShell";
import { setTheme } from "./components/shell/theme";
import { AREAS, AppRoutes, AreaPage, type AreaKey } from "./routes";
import { SecondaryPanel } from "./components/shell/SecondaryPanel";

const values = new Map<string, string>();
const storage = {
  getItem: (key: string): string | null => values.get(key) ?? null,
  setItem: (key: string, value: string): void => { values.set(key, value); },
  removeItem: (key: string): void => { values.delete(key); },
  clear: (): void => values.clear(),
};
vi.stubGlobal("localStorage", storage);

beforeEach(() => setToken("shell-test-session"));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.stubGlobal("localStorage", storage);
  setToken(null);
  storage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("web shell", () => {
  it("hides Users and roles when the account lacks users.read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ kind: "token", member: { handle: "sam", display_name: "Sam Lee" }, role: "viewer", permissions: ["templates.read"] })));
    render(() => <MemoryRouter><Route path="/" component={() => <SecondaryPanel area="settings" collapsed={false} onToggle={() => undefined}/>} /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("link", { name: "Notification templates" })).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "Users and roles" })).toBeNull();
  });
  it("shows the v1.3 Settings links in the secondary panel by permission", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ kind: "token", member: { handle: "sam", display_name: "Sam Lee" }, role: "viewer", permissions: ["templates.read", "profile.self", "ownership.read"] })));
    const viewer = render(() => <MemoryRouter><Route path="/" component={() => <SecondaryPanel area="settings" collapsed={false} onToggle={() => undefined}/>} /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("link", { name: "Profile" })).toHaveAttribute("href", "/settings/profile"));
    expect(screen.getByRole("link", { name: "Ownership rules" })).toHaveAttribute("href", "/settings/ownership");
    expect(screen.queryByRole("link", { name: "Integrations" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Tokens" })).toBeNull();
    viewer.unmount();
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ kind: "token", member: { handle: "ada", display_name: "Ada Admin" }, role: "admin", permissions: ["templates.read", "users.read", "profile.self", "integrations.read", "tokens.read", "ownership.read"] })));
    render(() => <MemoryRouter><Route path="/" component={() => <SecondaryPanel area="settings" collapsed={false} onToggle={() => undefined}/>} /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("link", { name: "Integrations" })).toHaveAttribute("href", "/settings/integrations"));
    expect(screen.getByRole("link", { name: "Tokens" })).toHaveAttribute("href", "/settings/tokens");
    expect(screen.getByRole("link", { name: "Profile" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ownership rules" })).toBeInTheDocument();
  });
  it("renders the rail labels in order and the skip link", () => {
    render(() => <MemoryRouter root={AppShell}><AppRoutes /></MemoryRouter>);
    const rail = within(screen.getByRole("navigation", { name: "Primary navigation" }));
    const labels = rail.getAllByRole("link").slice(1).map((link) => link.querySelector(":scope > span")?.textContent?.trim());
    expect(labels).toEqual(["Dashboard", "Issues", "Alerts", "My tasks", "Search", "Workflows", "Police", "Collectors", "Policies", "Escalation", "Audit", "Assistant", "Settings"]);
    expect(screen.getByRole("link", { name: "Assigned to me" })).toHaveAttribute("href", "/issues?query=assigned%3Ame");
    expect(screen.getByRole("link", { name: "Allowed" })).toHaveAttribute("href", "/issues?query=is%3Aallowed");
    expect(screen.getByRole("link", { name: "Verifying" })).toHaveAttribute("href", "/issues?query=is%3Apending_verification");
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
  });

  it("shows the combined attention badge on My tasks", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).startsWith("/api/human-tasks")
      ? Response.json({ items: [{ id: "t1" }, { id: "t2" }] })
      : Response.json({ items: [{ id: "e1" }] })));
    render(() => <MemoryRouter root={AppShell}><AppRoutes /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("3").closest(".rail-count")).toHaveAttribute("aria-label", "3 items need attention"));
  });

  it("marks Workflows active on /runs and updates active state after navigation", async () => {
    const history = createMemoryHistory();
    history.set({ value: "/runs" });
    render(() => <MemoryRouter history={history} root={AppShell}><AppRoutes /></MemoryRouter>);
    const rail = within(screen.getByRole("navigation", { name: "Primary navigation" }));
    expect(rail.getByRole("link", { name: "Workflows" })).toHaveAttribute("aria-current", "location");
    expect(rail.getByRole("link", { name: "Issues" })).not.toHaveAttribute("aria-current");
    fireEvent.click(rail.getByRole("link", { name: "Settings" }));
    await waitFor(() => expect(rail.getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "location"));
    expect(rail.getByRole("link", { name: "Workflows" })).not.toHaveAttribute("aria-current");
  });

  it("updates the secondary panel title and landmark after navigation", async () => {
    const history = createMemoryHistory();
    history.set({ value: "/issues" });
    render(() => <MemoryRouter history={history} root={AppShell}><Route path="/issues" component={() => <div />} /><Route path="/police" component={() => <div />} /></MemoryRouter>);
    expect(screen.getByRole("complementary", { name: "Issues navigation" })).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("navigation", { name: "Primary navigation" })).getByRole("link", { name: "Police" }));
    await waitFor(() => expect(screen.getByRole("complementary", { name: "Police navigation" })).toBeInTheDocument());
    expect(within(screen.getByRole("complementary", { name: "Police navigation" })).getByText("Police", { selector: "strong" })).toBeInTheDocument();
  });

  it("redirects both chat routes to Assistant and preserves query strings", async () => {
    const history = createMemoryHistory();
    history.set({ value: "/chat?context=%2Fissues" });
    const view = render(() => <MemoryRouter history={history} root={AppShell}><AppRoutes /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Assistant" })).toBeInTheDocument());
    expect(history.get()).toBe("/assistant?context=%2Fissues");
    view.unmount();
    const detailHistory = createMemoryHistory();
    detailHistory.set({ value: "/chat/abc?context=%2Fsearch" });
    render(() => <MemoryRouter history={detailHistory} root={AppShell}><AppRoutes /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Assistant" })).toBeInTheDocument());
    expect(detailHistory.get()).toBe("/assistant?context=%2Fsearch");
  });

  it("shows the unavailable state when the injected page map lacks an area", () => {
    render(() => <AreaPage area={AREAS.find((area) => area.key === "search")!} pages={new Map()} />);
    expect(screen.getByRole("heading", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByText("This page is not available yet.")).toBeInTheDocument();
  });

  it("renders a page from the injected page map", () => {
    const SearchPage: Component = () => <p>Search results</p>;
    const pageModules = new Map<AreaKey, Component>([["search", SearchPage]]);
    render(() => <AreaPage area={AREAS.find((area) => area.key === "search")!} pages={pageModules} />);
    expect(screen.getByText("Search results")).toBeInTheDocument();
  });

  it("does not put local saved views in navigation and toggles the panel", () => {
    storage.setItem("telescreen.issueViews", JSON.stringify([{ id: "v1", name: "My view", query: "is:unresolved", sort: "last_seen" }]));
    render(() => <MemoryRouter root={AppShell}><AppRoutes /></MemoryRouter>);
    expect(screen.queryByText("My view")).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(storage.getItem("telescreen.panelCollapsed")).toBe("true");
  });

  it("decodes safe API error fields into ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "validation_error", detail: "The input is invalid." } }), { status: 422, headers: { "Content-Type": "application/json" } })));
    await expect(apiFetch("/api/issues")).rejects.toMatchObject({ name: "ApiError", status: 422, code: "validation_error", detail: "The input is invalid." } satisfies Partial<ApiError>);
  });

  it("sends the stored bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    setToken("session-token");
    await apiFetch<{ status: string }>("/api/health");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer session-token");
  });

  it("persists explicit themes and removes system override", () => {
    setTheme("dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(storage.getItem("telescreen.theme")).toBe("dark");
    setTheme("system");
    expect(document.documentElement).not.toHaveAttribute("data-theme");
    expect(storage.getItem("telescreen.theme")).toBe("system");
  });
});
