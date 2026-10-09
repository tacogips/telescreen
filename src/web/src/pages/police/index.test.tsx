import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import PolicePage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows heartbeat age and disables a worker through the API", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: "worker-1", name: "Drive police", version: "1", mode: "pull", contract_version: "1.0", task_types: ["scan_shard"], labels: ["region=west"], concurrency: 2, status: "online", last_heartbeat_at: "2026-10-08T00:00:00Z" }], next_cursor: null }))).mockResolvedValueOnce(new Response(JSON.stringify({ id: "worker-1", name: "Drive police", version: "1", mode: "pull", contract_version: "1.0", task_types: ["scan_shard"], labels: ["region=west"], concurrency: 2, status: "disabled", last_heartbeat_at: "2026-10-08T00:00:00Z" }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  render(() => <PolicePage />);
  await screen.findByText(/Drive police/);
  expect(screen.getByText(/2026/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Disable" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/police/workers/worker-1/disable", expect.objectContaining({ method: "POST" })));
});
