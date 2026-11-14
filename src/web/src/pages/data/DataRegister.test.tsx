import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi, type MockInstance } from "vitest";
import { setSession } from "../../auth/session";
import DataPage from "./index";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

function setup(): MockInstance<typeof fetch> {
  setSession({ kind: "token", access_token: "test-session" });
  const asset = { id: "registered-1", short_id: "DATA-8", number: 8, name: "Registered list", source: "manual", display_locator: "inventory/items", kind: "list", origin: "registered", state: "present", item_count: null, item_trend: [], open_issue_count: 0, owner: null, deletion_due: null, overdue: false, incomplete: false, first_seen_at: "2026-10-13T00:00:00Z", last_seen_at: "2026-10-13T00:00:00Z", deleted_at: null, collector_config_id: null, sweep_covered: false, created_by: null, value_count: 1, fields: [], missing_required: [], open_issues: [] };
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/auth/me") return Response.json({ kind: "token", member: { handle: "alex", display_name: "Alex" }, role: "responder", permissions: ["data.read", "data.write"] });
    if (url === "/api/data/fields") return Response.json({ items: [{ id: "field-1", key: "purpose", label: "Purpose", description: null, field_type: "text", options: [], required: true, position: 0, role: "purpose", archived: false, value_count: 0, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" }] });
    if (url === "/api/data/fields/validate-values") {
      const values = JSON.parse(String(init?.body)).values as Record<string, unknown>;
      return Response.json(values.purpose ? { valid: true, errors: [] } : { valid: false, errors: [{ field: "purpose", code: "required" }] });
    }
    if (url === "/api/data/assets" && init?.method === "POST") return Response.json(asset, { status: 201 });
    if (url === "/api/data/assets/registered-1") return Response.json(asset);
    if (url.includes("/events") || url.includes("/items")) return Response.json({ items: [], next_cursor: null });
    if (url.includes("item-series")) return Response.json({ points: [] });
    if (url === "/api/members" || url === "/api/teams") return Response.json({ items: [], next_cursor: null });
    return Response.json({ error: { code: "not_found", detail: "Not found." } }, { status: 404 });
  });
}

it("validates required metadata before registration, then navigates after success", async () => {
  const fetchMock = setup();
  const history = createMemoryHistory(); history.set({ value: "/data/new" });
  render(() => <MemoryRouter history={history}><Route path="/data" component={DataPage} /><Route path="/data/:id" component={DataPage} /></MemoryRouter>);
  fireEvent.input(await screen.findByLabelText("Name"), { target: { value: "Registered list" } });
  fireEvent.input(screen.getByLabelText("Source"), { target: { value: "manual" } });
  fireEvent.input(screen.getByLabelText("Locator"), { target: { value: "inventory/items" } });
  fireEvent.click(screen.getByRole("button", { name: "Register data" }));
  expect(await screen.findByText("This field is required.")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/data/assets" && init?.method === "POST")).toBe(false);
  fireEvent.input(screen.getByLabelText("Purpose"), { target: { value: "Operations" } });
  fireEvent.click(screen.getByRole("button", { name: "Register data" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/data/assets" && init?.method === "POST")).toBe(true));
  expect(await screen.findByText("First seen")).toBeInTheDocument();
});
