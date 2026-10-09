import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import CollectorsPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const config = (id: string, name: string, enabled: boolean = true) => ({ id, name, kind: "slack", trigger_mode: "both", cron: null, sweep_cron: null, selector_json: "{}", credential_ref: null, enabled });

it("shows a sweep conflict returned by the API", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [config("c1", "Slack")], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "conflict", detail: "Conflict" } }), { status: 409 }));
  render(() => <CollectorsPage />);
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
  render(() => <CollectorsPage />);
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
  render(() => <CollectorsPage />);
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
  render(() => <CollectorsPage />);
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
  render(() => <CollectorsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Disable" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/collectors/c1", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "Slack", kind: "slack", trigger_mode: "both", cron: null, sweep_cron: null, selector_json: "{}", credential_ref: null, enabled: false }) })));
});
