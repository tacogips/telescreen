import { cleanup, render, screen } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi } from "vitest";
import WorkflowsPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderWorkflows(): void {
  const history = createMemoryHistory(); history.set({ value: "/workflows" });
  render(() => <MemoryRouter history={history}><Route path="/workflows" component={WorkflowsPage} /></MemoryRouter>);
}

function mockWorkflowListFetch(): void {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/workflows") return Response.json({ items: [
      { id: "wf-01-v1", name: "Incident response", version: 1, active: false, trigger_events: ["old_trigger"], last_run_at: null, runs_30d: { running: 0, waiting: 0, succeeded: 99, failed: 0, cancelled: 0 } },
      { id: "wf-01-v2", name: "Incident response", version: 2, active: true, trigger_events: ["issue_created", "schedule"], last_run_at: "2026-10-10T00:00:00Z", runs_30d: { running: 0, waiting: 1, succeeded: 3, failed: 1, cancelled: 0 } },
      { id: "wf-02", name: "Quiet workflow", version: 1, active: false, trigger_events: [], last_run_at: null, runs_30d: { running: 0, waiting: 0, succeeded: 0, failed: 0, cancelled: 0 } },
    ] });
    return Response.json({ items: [] });
  });
}

it("renders server trigger events and 30-day counts without fetching runs", async () => {
  mockWorkflowListFetch();
  const fetchMock = vi.mocked(globalThis.fetch);
  renderWorkflows();
  expect(await screen.findByText("Incident response")).toBeInTheDocument();
  expect(screen.getByText("issue_created")).toBeInTheDocument();
  expect(screen.getByText("schedule")).toBeInTheDocument();
  expect(screen.queryByText("old_trigger")).not.toBeInTheDocument();
  expect(screen.getByText("5")).toBeInTheDocument();
  expect(screen.getByText("waiting 1")).toBeInTheDocument();
  expect(screen.getByText("succeeded 3")).toBeInTheDocument();
  expect(screen.getByText("failed 1")).toBeInTheDocument();
  expect(document.querySelector('time[datetime="2026-10-10T00:00:00.000Z"]')).not.toBeNull();
  expect(screen.getByRole("columnheader", { name: "Runs (30 days)" })).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith("/api/runs"))).toBe(false);
});

it("shows the empty trigger and never-run fallbacks", async () => {
  mockWorkflowListFetch();
  const fetchMock = vi.mocked(globalThis.fetch);
  renderWorkflows();
  expect(await screen.findByText("Quiet workflow")).toBeInTheDocument();
  expect(screen.getByText("-")).toBeInTheDocument();
  expect(screen.getByText("Never")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith("/api/runs"))).toBe(false);
});
