import { cleanup, fireEvent, render, screen, waitFor, within } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi, type MockInstance } from "vitest";
import { setSession } from "../../auth/session";
import DataPage from "./index";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

const assets = [
  { id: "asset-a", short_id: "DATA-1", number: 1, name: "Active list", source: "github", display_locator: "repo/items", kind: "list", origin: "discovered", state: "present", item_count: 4, item_trend: [2, 3, 4], open_issue_count: 1, owner: null, deletion_due: null, overdue: false, incomplete: false, first_seen_at: "2026-10-01T00:00:00Z", last_seen_at: null, deleted_at: null, collector_config_id: "collector-1" },
  { id: "asset-b", short_id: "DATA-2", number: 2, name: "Expired list", source: "github", display_locator: "repo/expired", kind: "list", origin: "registered", state: "present", item_count: 2, item_trend: [3, 2], open_issue_count: 0, owner: null, deletion_due: "2026-10-01", overdue: true, incomplete: true, first_seen_at: "2026-10-01T00:00:00Z", last_seen_at: "2026-10-12T00:00:00Z", deleted_at: null, collector_config_id: null },
];

type DeferredFetch = Readonly<{ matches: (url: string) => boolean; response: Promise<Response>; started: () => void }>;

function setup(permissions: readonly string[] = ["data.read"], deferredFetches: readonly DeferredFetch[] = []): MockInstance<typeof fetch> {
  setSession({ kind: "token", access_token: "test-session" });
  const history = createMemoryHistory(); history.set({ value: "/data" });
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const deferred = deferredFetches.find((candidate) => candidate.matches(url));
    if (deferred !== undefined && url.startsWith("/api/data/assets")) {
      deferred.started();
      return deferred.response;
    }
    if (url === "/api/auth/me") return Response.json({ kind: "token", member: { handle: "alex", display_name: "Alex" }, role: "admin", permissions });
    if (url.startsWith("/api/data/counts")) return Response.json({ all: 2, present: 2, deleted: 0, incomplete: 1, overdue: 1 });
    if (url.startsWith("/api/data/assets")) {
      const query = new URL(url, "http://local").searchParams;
      let selected = assets;
      if (query.get("tab") === "overdue") selected = selected.filter((asset) => asset.overdue);
      if (query.get("q")) selected = selected.filter((asset) => asset.name.toLowerCase().includes((query.get("q") ?? "").toLowerCase()));
      return Response.json({ items: selected, next_cursor: null });
    }
    if (url === "/api/members") return Response.json({ items: [{ id: "member-1", handle: "alex", display_name: "Alex", email: null, slack_user_id: null, active: true }], next_cursor: null });
    if (url.startsWith("/api/data/export")) return new Response("short_id,name\nDATA-1,Active list", { headers: { "Content-Type": "text/csv" } });
    return Response.json({ error: { code: "not_found", detail: "Not found." } }, { status: 404 });
  });
}

function renderData(): void {
  const history = createMemoryHistory(); history.set({ value: "/data" });
  render(() => <MemoryRouter history={history}><Route path="/data" component={DataPage} /><Route path="/data/:id" component={DataPage} /></MemoryRouter>);
}

it("shows counts, filters overdue rows and narrows a search", async () => {
  setup(["data.read", "data.write"]); renderData();
  expect(await screen.findByText("Active list")).toBeInTheDocument();
  const activeRow = screen.getByText("Active list").closest("tr") as HTMLTableRowElement;
  expect(activeRow.textContent?.replace(/\s+/g, " ").trim()).toContain("Active list repo/items");
  expect(activeRow.cells[7]?.textContent).toBe("—");
  expect(within(activeRow).queryByText("Overdue")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: /Overdue/ }));
  expect(await screen.findByText("Expired list")).toBeInTheDocument();
  const overdueRow = screen.getByText("Expired list").closest("tr") as HTMLTableRowElement;
  expect(within(overdueRow).getByText("Overdue")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText("Active list")).toBeNull());
  fireEvent.input(screen.getByLabelText("Search data"), { target: { value: "missing" } });
  await waitFor(() => expect(screen.queryByText("Expired list")).toBeNull());
});

it("keeps the search input focused and tabs mounted while list requests reload", async () => {
  const makeDeferred = (): Readonly<{ fetch: DeferredFetch; resolve: (response: Response) => void }> => {
    let resolve: (response: Response) => void = () => { throw new Error("Deferred response was not initialized."); };
    const response = new Promise<Response>((complete) => { resolve = complete; });
    return { fetch: { matches: () => false, response, started: () => undefined }, resolve };
  };
  const searchRequest = makeDeferred();
  const overdueRequest = makeDeferred();
  let searchStarted = false;
  let overdueStarted = false;
  const fetches: readonly DeferredFetch[] = [
    { ...searchRequest.fetch, matches: (url) => url.includes("q=E") && !url.includes("tab=overdue"), started: () => { searchStarted = true; } },
    { ...overdueRequest.fetch, matches: (url) => url.includes("tab=overdue"), started: () => { overdueStarted = true; } },
  ];
  setup(["data.read"], fetches);
  renderData();
  await screen.findByText("Active list");

  const search = screen.getByLabelText("Search data");
  search.focus();
  fireEvent.input(search, { target: { value: "E" } });
  await waitFor(() => expect(searchStarted).toBe(true));
  expect(screen.getByLabelText("Search data")).toBe(search);
  expect(document.activeElement).toBe(search);
  expect(screen.getByRole("tab", { name: /Overdue/ })).toBeInTheDocument();
  searchRequest.resolve(Response.json({ items: [assets[1]], next_cursor: null }));
  expect(await screen.findByText("Expired list")).toBeInTheDocument();
  expect(screen.getByLabelText("Search data")).toBe(search);
  expect(document.activeElement).toBe(search);

  fireEvent.click(screen.getByRole("tab", { name: /Overdue/ }));
  await waitFor(() => expect(overdueStarted).toBe(true));
  expect(screen.getByRole("tab", { name: /Overdue/ })).toBeInTheDocument();
  overdueRequest.resolve(Response.json({ items: [assets[1]], next_cursor: null }));
  expect(await screen.findByText("Expired list")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Overdue/ })).toBeInTheDocument();
});

it("exports a CSV and hides registration from a viewer", async () => {
  const fetchMock = setup();
  const createObjectUrl = vi.fn(() => "blob:test");
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  renderData();
  await screen.findByText("Active list");
  expect(screen.queryByRole("link", { name: "Register data" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith("/api/data/export"))).toBe(true));
  expect(createObjectUrl).toHaveBeenCalledOnce();
});

it("shows an Overdue tag only for overdue assets", async () => {
  setup();
  renderData();
  const activeRow = (await screen.findByText("Active list")).closest("tr") as HTMLTableRowElement;
  const overdueRow = screen.getByText("Expired list").closest("tr") as HTMLTableRowElement;
  expect(within(activeRow).queryByText("Overdue")).toBeNull();
  expect(within(overdueRow).getByText("Overdue")).toBeInTheDocument();
});
