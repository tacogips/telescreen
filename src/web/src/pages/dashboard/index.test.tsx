import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mockApi } from "../settings/testUtils";
import DashboardPage from "./index";

const storage = new Map<string, string>();
beforeEach(() => { storage.clear(); vi.stubGlobal("localStorage", { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const dashboard = {
  period: "14d", open_issues: 12, findings: 30, buckets: [], open_by_exposure: [], open_by_source: [],
  archived_count: 2, escalating_count: 1, unassigned_high_priority: 4, mttr_seconds: null, mtta_seconds: null,
  top_issues: [{ id: "issue-1", short_id: "TELE-1", display_title: "Masked issue", findings: 5 }],
};

function renderDashboard(): void {
  const history = createMemoryHistory(); history.set({ value: "/dashboard" });
  render(() => <MemoryRouter history={history}><Route path="/dashboard" component={DashboardPage} /></MemoryRouter>);
}

function handler(url: string): Response | undefined {
  if (url.startsWith("/api/dashboard?")) return Response.json(dashboard);
  if (url === "/api/collectors") return Response.json({ items: [{ id: "collector-1", active: true }] });
  if (url === "/api/integrations") return Response.json({ items: [{ kind: "slack", configured: true, status: "ok" }] });
  if (url === "/api/members") return Response.json({ items: [{ active: true }, { active: true }] });
  if (url === "/api/alert-rules") return Response.json({ items: [{ id: "alert-1" }] });
  return undefined;
}

it("renders dashboard tiles, stream links, null mean times and the 14d default", async () => {
  const fetchMock = mockApi(["issues.read", "collectors.read", "integrations.read", "users.read", "alerts.read"], handler);
  renderDashboard();
  expect(await screen.findByText("TELE-1 · Masked issue")).toBeInTheDocument();
  expect(screen.getAllByText("-")).toHaveLength(2);
  expect(screen.getByRole("link", { name: /Unassigned high priority/ })).toHaveAttribute("href", "/issues?query=is:unresolved assigned:none priority:high");
  expect(await screen.findByText("Connect a collector")).toBeInTheDocument();
  expect(screen.getByText("Configure notifications")).toBeInTheDocument();
  expect(screen.getByText("Invite users")).toBeInTheDocument();
  expect(screen.getByText("Create an alert rule")).toBeInTheDocument();
  expect(screen.getAllByLabelText("Complete")).toHaveLength(4);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/dashboard?period=14d", expect.anything()));
  fireEvent.change(screen.getByLabelText("Period"), { target: { value: "7d" } });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/dashboard?period=7d", expect.anything()));
  fireEvent.change(screen.getByLabelText("Period"), { target: { value: "custom" } });
  fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("End date"), { target: { value: "2026-10-13" } });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/dashboard?period=custom&start=2026-10-01&end=2026-10-13", expect.anything()));
});

it("hides checklist endpoints without their read permissions and persists dismissal", async () => {
  const fetchMock = mockApi(["issues.read", "collectors.read", "users.read"], handler);
  renderDashboard();
  fireEvent.click(await screen.findByRole("button", { name: "Getting started" }));
  expect(await screen.findByText("Connect a collector")).toBeInTheDocument();
  expect(screen.getByText("Invite users")).toBeInTheDocument();
  expect(screen.queryByText("Configure notifications")).toBeNull();
  expect(screen.queryByText("Create an alert rule")).toBeNull();
  expect(fetchMock).not.toHaveBeenCalledWith("/api/integrations", expect.anything());
  expect(fetchMock).not.toHaveBeenCalledWith("/api/alert-rules", expect.anything());
  fireEvent.click(screen.getByRole("button", { name: "Dismiss checklist" }));
  expect(storage.get("telescreen.onboarding.dismissed")).toBe("true");
});

it("shows the overdue data tile for data readers and hides it when the count request is forbidden", async () => {
  const success = mockApi(["issues.read", "data.read"], (url) => {
    const base = handler(url);
    if (base !== undefined) return base;
    if (url === "/api/data/counts") return Response.json({ all: 5, present: 4, deleted: 1, incomplete: 2, overdue: 3 });
    return undefined;
  });
  renderDashboard();
  expect(await screen.findByText("Overdue data")).toBeInTheDocument();
  const overdueLink = screen.getByText("Overdue data").closest("a");
  expect(overdueLink).not.toBeNull();
  expect(overdueLink as HTMLAnchorElement).toHaveAttribute("href", "/data?tab=overdue");
  expect(success).toHaveBeenCalledWith("/api/data/counts", expect.anything());
  cleanup(); vi.restoreAllMocks();

  mockApi(["issues.read", "data.read"], (url) => {
    const base = handler(url);
    if (base !== undefined) return base;
    if (url === "/api/data/counts") return Response.json({ error: { code: "forbidden", detail: "Forbidden" } }, { status: 403 });
    return undefined;
  });
  renderDashboard();
  await screen.findByText("TELE-1 · Masked issue");
  await waitFor(() => expect(screen.queryByText("Overdue data")).toBeNull());
});
