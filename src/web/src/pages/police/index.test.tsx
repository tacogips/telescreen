import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import PolicePage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows stale and disabled health, worker details, and disables through the API", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [
      { id: "pol-01", name: "Drive police", version: "1", mode: "pull", task_types: ["scan_shard"], labels: ["region=west"], concurrency: 2, status: "active", last_heartbeat_at: "2026-10-08T00:00:00Z" },
      { id: "pol-02", name: "Disabled police", version: "2", mode: "push", task_types: [], labels: [], concurrency: 1, status: "disabled", last_heartbeat_at: null },
    ], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "pol-01", status: "disabled" })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  render(() => <PolicePage />);
  await screen.findByText("Drive police");
  expect(document.querySelector("time")?.getAttribute("title")).toContain("2026");
  expect(screen.getByText("Stale")).toBeInTheDocument();
  expect(screen.getByText("Disabled")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Drive police"));
  expect(await screen.findByRole("dialog", { name: "Police worker" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Disable worker" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/police/workers/pol-01/disable", expect.objectContaining({ method: "POST" })));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
});

it("shows the API error when disabling a worker fails", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [
      { id: "pol-01", name: "Drive police", version: "1", mode: "pull", task_types: ["scan_shard"], labels: [], concurrency: 1, status: "active", last_heartbeat_at: null },
    ], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "internal_error", detail: "failed" } }), { status: 500 }));
  render(() => <PolicePage />);
  fireEvent.click(await screen.findByText("Drive police"));
  fireEvent.click(await screen.findByRole("button", { name: "Disable worker" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("internal_error");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/police/workers/pol-01/disable", expect.objectContaining({ method: "POST" })));
});

it("uses reported Police health and exposes police_silent in the tooltip", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [
    { id: "pol-03", name: "Silent worker", version: "3", mode: "pull", task_types: ["scan_shard"], labels: [], concurrency: 1, status: "active", last_heartbeat_at: new Date().toISOString(), health: { status: "failing", conditions: ["police_silent"] } },
  ], next_cursor: null })));
  render(() => <PolicePage />);
  expect(await screen.findByText("failing")).toBeInTheDocument();
  expect(screen.getByText("failing").closest("[title]")).toHaveAttribute("title", "police_silent");
});
