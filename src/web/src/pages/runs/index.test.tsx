import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { MemoryRouter, Route } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import RunsPage from "./index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("RunsPage", () => {
  it("resolves definition ids to workflow names and sends status filters", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.startsWith("/api/workflows")) return Response.json({ items: [{ id: "wf-01", name: "incident", version: 2, active: true }] });
      return Response.json({ items: [{ id: "run-01", definition_id: "wf-01", issue_id: null, status: "running", trigger: "manual", started_at: "2026-10-08T00:00:00Z", finished_at: null }] });
    }));
    render(() => <MemoryRouter><Route path="*" component={RunsPage} /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "incident" })).toHaveAttribute("href", "/runs/run-01");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "waiting" } });
    await waitFor(() => expect(calls.some((url) => url === "/api/runs?status=waiting")).toBe(true));
  });
});
