import { render, screen, fireEvent, within, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Component } from "solid-js";
import { ApiError, apiFetch, setToken } from "./api/client";
import { AppShell } from "./components/shell/AppShell";
import { setTheme } from "./components/shell/theme";
import { AREAS, AppRoutes, AreaPage, type AreaKey } from "./routes";

const values = new Map<string, string>();
const storage = {
  getItem: (key: string): string | null => values.get(key) ?? null,
  setItem: (key: string, value: string): void => { values.set(key, value); },
  removeItem: (key: string): void => { values.delete(key); },
  clear: (): void => values.clear(),
};
vi.stubGlobal("localStorage", storage);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.stubGlobal("localStorage", storage);
  setToken(null);
  storage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("web shell", () => {
  it("renders the rail labels in order and the skip link", () => {
    render(() => <MemoryRouter root={AppShell}><AppRoutes /></MemoryRouter>);
    const rail = within(screen.getByRole("navigation", { name: "Primary navigation" }));
    const labels = rail.getAllByRole("link").slice(1).map((link) => link.textContent?.trim());
    expect(labels).toEqual(["Issues", "Search", "Workflows", "Police", "Collectors", "Escalation", "Audit", "Assistant", "Settings"]);
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
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
    render(() => <AreaPage area={AREAS[1]!} pages={new Map()} />);
    expect(screen.getByRole("heading", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByText("This page is not available yet.")).toBeInTheDocument();
  });

  it("renders a page from the injected page map", () => {
    const SearchPage: Component = () => <p>Search results</p>;
    const pageModules = new Map<AreaKey, Component>([["search", SearchPage]]);
    render(() => <AreaPage area={AREAS[1]!} pages={pageModules} />);
    expect(screen.getByText("Search results")).toBeInTheDocument();
  });

  it("toggles the panel, refreshes saved views, and tolerates invalid storage", () => {
    storage.setItem("telescreen.issueViews", "not-json");
    render(() => <MemoryRouter root={AppShell}><AppRoutes /></MemoryRouter>);
    expect(screen.queryByText("My view")).not.toBeInTheDocument();
    storage.setItem("telescreen.issueViews", JSON.stringify([{ id: "v1", name: "My view", query: "is:unresolved", sort: "last_seen" }]));
    window.dispatchEvent(new Event("telescreen:issue-views"));
    expect(screen.getByText("My view")).toBeInTheDocument();
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
