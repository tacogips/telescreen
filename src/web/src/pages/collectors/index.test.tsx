import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi } from "vitest";
import CollectorsPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderCollector(path: string = "/collectors"): void {
  const history = createMemoryHistory();
  history.set({ value: path });
  render(() => <MemoryRouter history={history}><Route path="/collectors" component={CollectorsPage} /><Route path="/collectors/:id" component={CollectorsPage} /></MemoryRouter>);
}

const config = (id: string, name: string, enabled: boolean = true) => ({ id, name, kind: "slack", trigger_mode: "both", cron: null, sweep_cron: null, selector_json: "{}", credential_ref: null, enabled, inventory_mode: "sensitive_only" });

it("shows a sweep conflict returned by the API", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [config("c1", "Slack")], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "conflict", detail: "Conflict" } }), { status: 409 }));
  renderCollector();
  await screen.findByText("Slack");
  fireEvent.click(screen.getByRole("button", { name: "Sweep now" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/collectors/c1/sweep", expect.objectContaining({ method: "POST" })));
  expect(await screen.findByText("A sweep is already running")).toBeInTheDocument();
});

it("shows the API error when Run now fails", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [config("c1", "Slack")], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "internal_error", detail: "failed" } }), { status: 500 }));
  renderCollector();
  fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("internal_error");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/collectors/c1/run", expect.objectContaining({ method: "POST" })));
});

it("sends selector JSON as a string when creating a collector", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "c1" })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  renderCollector();
  fireEvent.click(await screen.findByRole("button", { name: "Connect a source" }));
  fireEvent.input(screen.getByLabelText("Name"), { target: { value: "Drive" } });
  fireEvent.input(screen.getByLabelText("Selector JSON"), { target: { value: '{"team":"blue"}' } });
  fireEvent.click(screen.getByRole("button", { name: "Save collector" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/collectors", expect.objectContaining({ method: "POST", body: expect.stringContaining('"selector_json":"{\\"team\\":\\"blue\\"}"') })));
});

it("loads map jobs once and selects the newest sweep per collector", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [config("c1", "Slack")], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [
      { id: "old", origin: "collector_sweep", status: "succeeded", collector_config_id: "c1", created_at: "2026-01-01T00:00:00Z" },
      { id: "new", origin: "collector_sweep", status: "running", collector_config_id: "c1", created_at: "2026-02-01T00:00:00Z" },
    ], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "new", origin: "collector_sweep", status: "running", collector_config_id: "c1", counts: {}, partitions_done: 2, partitions_total: 4, findings_so_far: 1, paused_until: null, failed_shards: [], result_json: null })));
  renderCollector();
  await screen.findByText("2/4");
  expect(fetchMock.mock.calls.filter(([url]) => String(url) === "/api/map-jobs")).toHaveLength(1);
  expect(fetchMock).toHaveBeenCalledWith("/api/map-jobs/new", expect.anything());
});

it("sends the full collector body when disabling a config", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [config("c1", "Slack")], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "c1" })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [config("c1", "Slack", false)], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  renderCollector();
  fireEvent.click(await screen.findByRole("button", { name: "Disable" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/collectors/c1", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "Slack", kind: "slack", trigger_mode: "both", cron: null, sweep_cron: null, selector_json: "{}", credential_ref: null, enabled: false, inventory_mode: "sensitive_only" }) })));
});

it("sends inventory_mode when changing a collector to Off", async () => {
  const collector = { ...config("c1", "Slack"), inventory_mode: "all" };
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/collectors") return Response.json({ items: [collector], next_cursor: null });
    if (url === "/api/map-jobs") return Response.json({ items: [], next_cursor: null });
    if (url === "/api/collectors/c1" && init?.method === "PATCH") return Response.json({ id: "c1" });
    return Response.json({ items: [collector], next_cursor: null });
  });
  vi.stubGlobal("confirm", vi.fn(() => true));
  renderCollector();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Inventory mode"), { target: { value: "off" } });
  fireEvent.click(screen.getByRole("button", { name: "Save collector" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/collectors/c1", expect.objectContaining({ method: "PATCH", body: expect.stringContaining('"inventory_mode":"off"') })));
});

it("shows collector health and event-only metadata", async () => {
  vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ ...config("c1", "Drive"), health: { status: "warning", conditions: ["watch_expiring"], last_success_at: "2026-10-09T00:00:00Z", event_only: true } }], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  renderCollector();
  expect(await screen.findByText("warning")).toBeInTheDocument();
  expect(screen.getByText("Event only")).toBeInTheDocument();
  expect(screen.getByText("Enabled")).toBeInTheDocument();
});

it("loads the coverage tab with only resource id prefixes and linked issue ids", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/api/collectors") return new Response(JSON.stringify({ items: [config("c1", "Drive")], next_cursor: null }));
    if (url === "/api/map-jobs") return new Response(JSON.stringify({ items: [], next_cursor: null }));
    if (url === "/api/collectors/c1/coverage") return new Response(JSON.stringify({ since: "2026-10-01T00:00:00Z", counts: [{ state: "unsupported", reason: "binary", count: 1 }], unscannable_total: 1 }));
    if (url.startsWith("/api/collectors/c1/coverage/resources")) return new Response(JSON.stringify({ items: [{ resource_id: "abcdef0123456789", source: "gdrive", state: "unsupported", reason: "binary", truncated_bytes: 0, exposure_visibility: "public", first_seen_at: "2026-10-01T00:00:00Z", last_attempt_at: "2026-10-02T00:00:00Z", issues: [{ issue_id: "issue-1", short_id: "TELE-1" }] }], next_cursor: null }));
    return new Response(JSON.stringify({ error: { code: "not_found", detail: "Not found" } }), { status: 404 });
  });
  renderCollector("/collectors/c1?tab=coverage");
  fireEvent.click(await screen.findByRole("button", { name: "Coverage" }));
  expect(await screen.findByText("unsupported · binary")).toBeInTheDocument();
  expect(screen.getByText("abcdef01")).toBeInTheDocument();
  expect(screen.getByText("TELE-1")).toBeInTheDocument();
  expect(screen.queryByText("abcdef0123456789")).toBeNull();
});
