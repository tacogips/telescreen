import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi, type MockInstance } from "vitest";
import { setSession } from "../../auth/session";
import DataPage from "./index";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

const detail = {
  id: "data-asset-01", short_id: "DATA-1", number: 1, name: "Example list", source: "github", display_locator: "repo/items", kind: "list", origin: "registered", state: "present", item_count: 12, item_trend: [10, 12], open_issue_count: 1, owner: null, deletion_due: null, overdue: false, incomplete: true, first_seen_at: "2026-10-01T00:00:00Z", last_seen_at: null, deleted_at: null, collector_config_id: null,
  sweep_covered: false, created_by: null, value_count: 0, missing_required: ["purpose"], open_issues: [{ issue_id: "issue-1", short_id: "TELE-1" }],
  fields: [{ key: "purpose", label: "Purpose", field_type: "text", required: true, role: "purpose", options: [], value_json: null }],
};
const event = { id: "event-1", kind: "discovered", occurred_at: null, recorded_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(), actor: { kind: "system", ref: "system", display_name: "System" }, items_added: null, items_removed: null, item_count_after: null, values_added: null, values_removed: null, value_count_after: null, field_key: null, old_value: null, new_value: null, note: null };

function setup(): MockInstance<typeof fetch> {
  setSession({ kind: "token", access_token: "test-session" });
  let assetState: "present" | "deleted" = "present";
  const currentDetail = () => ({ ...detail, state: assetState, deleted_at: assetState === "deleted" ? "2026-10-13T00:00:00Z" : null });
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/auth/me") return Response.json({ kind: "token", member: { handle: "alex", display_name: "Alex" }, role: "responder", permissions: ["data.read", "data.write"] });
    if (url === "/api/data/assets/data-asset-01" && (init?.method === undefined || init.method === "GET")) return Response.json(currentDetail());
    if (url === "/api/data/assets/data-asset-01/mark-deleted" && init?.method === "POST") { assetState = "deleted"; return Response.json(currentDetail()); }
    if (url === "/api/data/assets/data-asset-01/restore" && init?.method === "POST") { assetState = "present"; return Response.json(currentDetail()); }
    if (url.startsWith("/api/data/assets/data-asset-01/events") && (init?.method === undefined || init.method === "GET")) return Response.json({ items: [event], next_cursor: null });
    if (url.startsWith("/api/data/assets/data-asset-01/events") && init?.method === "POST") return Response.json({ ...event, kind: "manual_change", occurred_at: JSON.parse(String(init.body)).occurred_at });
    if (url.startsWith("/api/data/assets/data-asset-01/item-series")) return Response.json({ points: [{ at: "2026-10-10T00:00:00Z", item_count: 10 }, { at: "2026-10-11T00:00:00Z", item_count: 11 }, { at: "2026-10-12T00:00:00Z", item_count: 12 }] });
    if (url.startsWith("/api/data/assets/data-asset-01/items")) return Response.json({ items: [{ id: "item-1", kind: "value", key_prefix: "a1b2c3d4e5f6", first_seen_at: "2026-10-01T00:00:00Z", removed_at: null }], next_cursor: null });
    if (url.startsWith("/api/data/assets/data-asset-01")) return Response.json(detail);
    if (url === "/api/members" || url === "/api/teams") return Response.json({ items: [], next_cursor: null });
    if (url === "/api/data/fields/validate-values") return Response.json({ valid: false, errors: [{ field: "purpose", code: "required" }] });
    return Response.json({ error: { code: "not_found", detail: "Not found." } }, { status: 404 });
  });
}

function renderDetail(): void {
  const history = createMemoryHistory(); history.set({ value: "/data/data-asset-01" });
  render(() => <MemoryRouter history={history}><Route path="/data/:id" component={DataPage} /></MemoryRouter>);
}

it("renders lifecycle history, values, incomplete fields and related issue links", async () => {
  setup(); renderDetail();
  expect(await screen.findByText("Required information is missing: purpose")).toBeInTheDocument();
  expect(await screen.findByText("discovered")).toBeInTheDocument();
  expect(screen.getByText("discovered").closest("li")?.textContent?.replace(/\s+/g, " ").trim()).toContain("S System discovered");
  expect(screen.getByText(/2 hours ago/)).toBeInTheDocument();
  expect(screen.getByText("Last seen").nextElementSibling?.textContent).toBe("—");
  expect(screen.queryByText(/days? ago/)).toBeNull();
  expect(screen.getByLabelText("Breadcrumb").textContent?.replace(/\s+/g, " ").trim()).toBe("Data / DATA-1");
  expect(screen.getByRole("img", { name: "Item count over time" })).toHaveClass("item-count-chart");
  fireEvent.click(screen.getByRole("tab", { name: "Values" }));
  expect(await screen.findByText("a1b2c3d4e5f6")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "TELE-1" })).toHaveAttribute("href", "/issues/issue-1");
});

it("marks an asset deleted and then offers Restore", async () => {
  const fetchMock = setup(); renderDetail();
  fireEvent.click(await screen.findByRole("button", { name: "Mark deleted" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/data/assets/data-asset-01/mark-deleted" && init?.method === "POST")).toBe(true));
  expect(await screen.findByRole("button", { name: "Restore" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Mark deleted" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Restore" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/data/assets/data-asset-01/restore" && init?.method === "POST")).toBe(true));
  expect(await screen.findByRole("button", { name: "Mark deleted" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Restore" })).not.toBeInTheDocument();
});

it("sends occurred_at for manual events and blocks invalid metadata PUT", async () => {
  const fetchMock = setup(); renderDetail();
  fireEvent.click(await screen.findByRole("button", { name: "Record event" }));
  fireEvent.input(screen.getByLabelText("Event note"), { target: { value: "Reviewed" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Record event" })[1]!);
  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/data/assets/data-asset-01/events" && init?.method === "POST" && typeof init.body === "string" && JSON.parse(init.body).occurred_at)).toBe(true));
  const eventRequest = fetchMock.mock.calls.find(([input, init]) => String(input) === "/api/data/assets/data-asset-01/events" && init?.method === "POST");
  const occurredAt = Date.parse(String(JSON.parse(String(eventRequest?.[1]?.body)).occurred_at));
  expect(Math.abs(occurredAt - Date.now())).toBeLessThan(120_000);
  await screen.findByRole("button", { name: "Save values" });
  fireEvent.click(screen.getByRole("button", { name: "Save values" }));
  expect(await screen.findByText("This field is required.")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/data/assets/data-asset-01/fields" && init?.method === "PUT")).toBe(false);
});
