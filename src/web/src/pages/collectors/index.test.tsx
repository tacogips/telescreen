import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import CollectorsPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows the active-sweep conflict returned by the API", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: "c1", name: "Slack", kind: "slack", trigger_mode: "both", cron: null, sweep_cron: null, sweep_max_concurrency: 2, selector_json: "{}", credential_ref: null, enabled: true }], next_cursor: null }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null }))).mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "conflict", detail: "Conflict" } }), { status: 409 }));
  render(() => <CollectorsPage />);
  await screen.findByText("Slack");
  fireEvent.click(screen.getByRole("button", { name: "Sweep now" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/collectors/c1/sweep", expect.objectContaining({ method: "POST" })));
  expect(await screen.findByText("A sweep is already running.")).toBeInTheDocument();
});

it("sends selector_json as a string when creating a collector", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null }))).mockResolvedValueOnce(new Response(JSON.stringify({ id: "c1", name: "Drive", kind: "gdrive", trigger_mode: "schedule", cron: null, sweep_cron: null, sweep_max_concurrency: 2, selector_json: "{\"team\":\"blue\"}", credential_ref: null, enabled: true }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  render(() => <CollectorsPage />);
  fireEvent.input(screen.getByLabelText("Name"), { target: { value: "Drive" } });
  fireEvent.input(screen.getByLabelText("Selector JSON"), { target: { value: '{"team":"blue"}' } });
  fireEvent.click(screen.getByRole("button", { name: "Save collector" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/collectors", expect.objectContaining({ method: "POST", body: expect.stringContaining('"selector_json":"{\\"team\\":\\"blue\\"}"') })));
});
