import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JSX } from "solid-js";
import { IssueList } from "./IssueList";

type IssueFixture = Record<string, unknown>;
const issue = (id: string, shortId: string, extra: IssueFixture = {}): IssueFixture => ({
  id, number: Number(shortId.replace("TELE-", "")), short_id: shortId, grouping_key: `group-${id}`, source: "github", resource_key: `repo-${id}`, display_locator: "repo:masked/file", resource_ref_json: null, collector_config_id: null, display_title: "Credential finding", rule_class: "credential", severity: "high", status: "unresolved", assignee_member_id: null, assignee_team_id: null, assignee: null, first_seen: "2026-10-01T00:00:00Z", last_seen: "2026-10-08T00:00:00Z", last_checked_at: "2026-10-08T00:00:00Z", sweep_covered: true, times_seen: 4, distinct_fingerprints: 2, regression_count: 0, resolved_at: null, resolution: null, priority: "high", trend: [1, 3, 2], ...extra,
});
const counts = { tabs: [{ tab: "unresolved", count: 57 }, { tab: "for_review", count: 12 }, { tab: "regressed", count: 3 }, { tab: "escalating", count: 2 }, { tab: "archived", count: 1 }, { tab: "resolved", count: 4 }], updated_since_count: 0 };
const saved = { id: "view-1", name: "Review", query: "is:unresolved", sort: "last_seen", visibility: "personal", starred: false, is_default: false };

function root(): JSX.Element {
  return <MemoryRouter root={() => <><IssueList /><div id="toast-region" aria-live="polite" /></>} />;
}
function stubStorage(initial: string | null = null): Storage {
  const values = new Map<string, string>();
  if (initial !== null) values.set("telescreen.issueViews", initial);
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key), clear: () => values.clear(), key: (index: number) => [...values.keys()][index] ?? null, get length() { return values.size; } } as Storage;
  vi.stubGlobal("localStorage", storage);
  return storage;
}
function responseFor(url: string, method: string, items: IssueFixture[], requestBody?: string): Response {
  if (url.includes("/api/issues/counts")) return new Response(JSON.stringify(counts), { status: 200 });
  if (url === "/api/views" && method === "GET") return new Response(JSON.stringify({ items: [] }), { status: 200 });
  if (url === "/api/views" && method === "POST") return new Response(JSON.stringify({ ...saved, ...(requestBody ? JSON.parse(requestBody) : {}) }), { status: 201 });
  if (url.includes("/api/views/") && method === "POST") return new Response(JSON.stringify(saved), { status: 200 });
  if (url === "/api/issues/bulk") { const body = requestBody ? JSON.parse(requestBody) as { ids?: string[] } : {}; return new Response(JSON.stringify({ succeeded: body.ids ?? ["iss-1"], failed: [], truncated: false }), { status: 200 }); }
  if (url.includes("/api/members") || url.includes("/api/teams") || url.includes("/api/workflows")) return new Response(JSON.stringify({ items: [] }), { status: 200 });
  return new Response(JSON.stringify({ items, next_cursor: null }), { status: 200 });
}
function installFetch(items: IssueFixture[], hook?: (url: string, method: string, body?: string) => Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? "GET"; const body = typeof init?.body === "string" ? init.body : undefined;
    return hook?.(url, method, body) ?? responseFor(url, method, items, body);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
async function lastBulkBody(fetchMock: ReturnType<typeof vi.fn>): Promise<unknown> {
  await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/issues/bulk")).toHaveLength(1));
  const request = fetchMock.mock.calls.find(([input]) => String(input) === "/api/issues/bulk");
  return JSON.parse(String(request?.[1]?.body)) as unknown;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("IssueList stream", () => {
  it("B3: defaults to is:unresolved and displays a regressed issue", async () => {
    stubStorage();
    const fetchMock = installFetch([issue("iss-regressed", "TELE-3", { status: "regressed" })]);
    render(root);
    expect(await screen.findByText("TELE-3")).toBeTruthy();
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("query=is%3Aunresolved"))).toBe(true));
  });

  it("B4: renders the assignee display name and initials but not the member ref", async () => {
    stubStorage(); installFetch([issue("iss-1", "TELE-1", { assignee_member_id: "mem-01", assignee: { kind: "member", ref: "mem-01", display_name: "Alice Example" } })]);
    render(root);
    expect(await screen.findByText("Alice Example")).toBeTruthy();
    expect(screen.getByLabelText("Alice Example")).toHaveTextContent("AE");
    expect(document.body.textContent).not.toContain("mem-01");
  });

  it("B5: sends a negated source token verbatim", async () => {
    stubStorage(); const fetchMock = installFetch([]); render(root);
    const input = await screen.findByLabelText("Search query");
    fireEvent.input(input, { target: { value: "!source:slack" } }); fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => new URL(String(input), "http://localhost").searchParams.get("query") === "is:unresolved !source:slack")).toBe(true));
  });

  it("keeps tabs, period, seven sorts and trend in URL-backed API requests", async () => {
    stubStorage(); const fetchMock = installFetch([]); render(root);
    expect([...screen.getByLabelText("Sort").querySelectorAll("option")].map((option) => option.getAttribute("value"))).toEqual(["last_seen", "first_seen", "times_seen", "values", "risk", "priority", "trending"]);
    fireEvent.click(await screen.findByRole("tab", { name: /Archived/ }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => new URL(String(input), "http://localhost").searchParams.get("query") === "is:archived")).toBe(true));
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "14d" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => new URL(String(input), "http://localhost").searchParams.get("period") === "14d")).toBe(true));
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "trending" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => new URL(String(input), "http://localhost").searchParams.get("sort") === "trending")).toBe(true));
    fireEvent.change(screen.getByLabelText("Trend window"), { target: { value: "24h" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => { const params = new URL(String(input), "http://localhost").searchParams; return params.get("period") === "14d" && params.get("sort") === "trending" && params.get("trend") === "24h"; })).toBe(true));
    expect(screen.getByRole("tab", { name: /Archived/ })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps custom period dates in the URL and query requests", async () => {
    stubStorage(); const fetchMock = installFetch([]); render(root);
    fireEvent.change(await screen.findByLabelText("Period"), { target: { value: "custom" } });
    const starts = await screen.findAllByLabelText("Start date");
    const ends = screen.getAllByLabelText("End date");
    fireEvent.change(starts[starts.length - 1] as HTMLElement, { target: { value: "2026-10-01" } });
    fireEvent.change(ends[ends.length - 1] as HTMLElement, { target: { value: "2026-10-12" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => { const params = new URL(String(input), "http://localhost").searchParams; return params.get("period") === "custom" && params.get("start") === "2026-10-01" && params.get("end") === "2026-10-12"; })).toBe(true));
  });

  it("selects all matching query+period and reports bulk failures by issue id", async () => {
    stubStorage(); const fetchMock = installFetch([issue("iss-1", "TELE-1")], (url, method, body) => {
      if (url === "/api/issues/bulk") return new Response(JSON.stringify({ succeeded: [], failed: [{ issue_id: "iss-1", code: "invalid_transition" }], truncated: false }), { status: 200 });
      return responseFor(url, method, [issue("iss-1", "TELE-1")], body);
    });
    render(root); fireEvent.click(await screen.findByLabelText("Select TELE-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Select all 57 matching" }));
    fireEvent.click(await screen.findByRole("button", { name: "Acknowledge" }));
    expect(await lastBulkBody(fetchMock)).toEqual({ query: "is:unresolved", period: "all", action: "status", params: { status: "acknowledged" } });
    expect(await screen.findByText("Failed: TELE-1")).toBeTruthy();
  });

  it("sends priority params for all matching issues", async () => {
    stubStorage(); const fetchMock = installFetch([issue("iss-1", "TELE-1")]); render(root);
    fireEvent.click(await screen.findByLabelText("Select TELE-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Select all 57 matching" }));
    fireEvent.change(screen.getByLabelText("Set priority"), { target: { value: "high" } });
    expect(await lastBulkBody(fetchMock)).toEqual({ query: "is:unresolved", period: "all", action: "priority", params: { priority: "high" } });
  });

  it("sends the assignee ref for all matching issues", async () => {
    stubStorage(); const fetchMock = installFetch([issue("iss-1", "TELE-1")], (url, method, body) => {
      if (url.startsWith("/api/members")) return new Response(JSON.stringify({ items: [{ id: "mem-01", handle: "alice", display_name: "Alice Example", email: null, slack_user_id: null, active: true }], next_cursor: null }), { status: 200 });
      return responseFor(url, method, [issue("iss-1", "TELE-1")], body);
    }); render(root);
    fireEvent.click(await screen.findByLabelText("Select TELE-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Select all 57 matching" }));
    await waitFor(() => expect([...screen.getByLabelText("Assign to").querySelectorAll("option")].some((option) => option.value === "member:alice")).toBe(true));
    fireEvent.change(screen.getByLabelText("Assign to"), { target: { value: "member:alice" } });
    expect(await lastBulkBody(fetchMock)).toEqual({ query: "is:unresolved", period: "all", action: "assign", params: { assignee_ref: "member:alice" } });
  });

  it("sends a null assignee ref when all matching issues are unassigned", async () => {
    stubStorage(); const fetchMock = installFetch([issue("iss-1", "TELE-1")]); render(root);
    fireEvent.click(await screen.findByLabelText("Select TELE-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Select all 57 matching" }));
    fireEvent.change(screen.getByLabelText("Assign to"), { target: { value: "__unassigned__" } });
    expect(await lastBulkBody(fetchMock)).toEqual({ query: "is:unresolved", period: "all", action: "assign", params: { assignee_ref: null } });
  });

  it("sends the archive threshold for all matching issues", async () => {
    stubStorage(); const fetchMock = installFetch([issue("iss-1", "TELE-1")]); render(root);
    fireEvent.click(await screen.findByLabelText("Select TELE-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Select all 57 matching" }));
    fireEvent.click(screen.getByText("Archive"));
    fireEvent.input(screen.getByLabelText("Archive finding threshold"), { target: { value: "17" } });
    fireEvent.click(screen.getByRole("button", { name: "Until N more findings" }));
    expect(await lastBulkBody(fetchMock)).toEqual({ query: "is:unresolved", period: "all", action: "archive", params: { archive: { kind: "until_findings", count: 17 } } });
  });

  it("sends resolution reason and note for all matching issues", async () => {
    stubStorage(); const fetchMock = installFetch([issue("iss-1", "TELE-1")]); render(root);
    fireEvent.click(await screen.findByLabelText("Select TELE-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Select all 57 matching" }));
    fireEvent.click(screen.getByRole("button", { name: "Resolve selected issues" }));
    fireEvent.change(screen.getByLabelText("Resolve reason"), { target: { value: "false_positive" } });
    fireEvent.input(screen.getByLabelText("Resolution note"), { target: { value: "Confirmed benign" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm resolve" }));
    expect(await lastBulkBody(fetchMock)).toEqual({ query: "is:unresolved", period: "all", action: "resolve", params: { reason: "false_positive", note: "Confirmed benign" } });
  });

  it("keeps remediation selection-only when all matching issues are selected", async () => {
    stubStorage(); const fetchMock = installFetch([issue("iss-1", "TELE-1")]); render(root);
    fireEvent.click(await screen.findByLabelText("Select TELE-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Select all 57 matching" }));
    fireEvent.click(screen.getByRole("button", { name: "Mark remediated" }));
    expect(await screen.findByText("Remediation requires a finite selected set")).toBeTruthy();
    expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/issues/bulk")).toBe(false);
  });

  it("archives selected issues with the chosen bounded condition", async () => {
    stubStorage(); const fetchMock = installFetch([issue("iss-1", "TELE-1")]); render(root);
    fireEvent.click(await screen.findByLabelText("Select TELE-1"));
    fireEvent.click(screen.getByText("Archive"));
    fireEvent.click(await screen.findByRole("button", { name: "For 1h" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/issues/iss-1/archive" && JSON.parse(String(init?.body)).kind === "for_duration" && JSON.parse(String(init?.body)).duration_minutes === 60)).toBe(true));
  });

  it("merges a selected set once with the selected primary and child ids", async () => {
    stubStorage(); const fetchMock = installFetch([issue("iss-1", "TELE-1"), issue("iss-2", "TELE-2")]); render(root);
    fireEvent.click(await screen.findByLabelText("Select TELE-1")); fireEvent.click(screen.getByLabelText("Select TELE-2"));
    fireEvent.click(await screen.findByRole("button", { name: "Merge selected" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/issues/merge")).toHaveLength(1));
    const merge = fetchMock.mock.calls.find(([input]) => String(input) === "/api/issues/merge");
    expect(JSON.parse(String(merge?.[1]?.body))).toEqual({ primary_id: "iss-1", ids: ["iss-2"] });
  });

  it("imports legacy local views once and clears the key only after successful server writes", async () => {
    const storage = stubStorage(JSON.stringify([{ id: "old-1", name: "Legacy one", query: "is:unresolved", sort: "last_seen", starred: false }, { id: "old-2", name: "Legacy two", query: "source:slack", sort: "risk", starred: true }]));
    const fetchMock = installFetch([]); render(root);
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input, init]) => String(input) === "/api/views" && init?.method === "POST")).toHaveLength(2));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input, init]) => String(input).endsWith("/star") && init?.method === "PUT")).toHaveLength(1));
    await waitFor(() => expect(storage.getItem("telescreen.issueViews")).toBeNull());
  });

  it("retains the legacy key when a server view import fails", async () => {
    const legacy = JSON.stringify([{ id: "old-1", name: "Legacy one", query: "is:unresolved", sort: "last_seen", starred: false }]);
    const storage = stubStorage(legacy); installFetch([], (url, method, body) => url === "/api/views" && method === "POST" ? new Response(JSON.stringify({ error: { code: "request_failed", detail: "failed" } }), { status: 500 }) : responseFor(url, method, [], body));
    render(root);
    await waitFor(() => expect(screen.getByText("Some saved views could not be imported; try again later")).toBeTruthy());
    expect(storage.getItem("telescreen.issueViews")).toBe(legacy);
  });

  it("keeps existing issue ordering while the refresh banner waits for a click", async () => {
    stubStorage(); vi.useFakeTimers();
    let countCalls = 0;
    const fetchMock = installFetch([issue("iss-1", "TELE-1"), issue("iss-2", "TELE-2")], (url, method, body) => {
      if (url.includes("/api/issues/counts")) { countCalls += 1; return new Response(JSON.stringify({ ...counts, updated_since_count: countCalls > 1 ? 3 : 0 }), { status: 200 }); }
      return responseFor(url, method, [issue("iss-1", "TELE-1"), issue("iss-2", "TELE-2")], body);
    });
    render(root); await screen.findByText("TELE-1");
    await vi.advanceTimersByTimeAsync(30000);
    await waitFor(() => expect(screen.getByText("3 new or updated issues")).toBeTruthy());
    const rowText = [...document.querySelectorAll(".issue-row")].map((row) => row.textContent);
    expect(rowText[0]).toContain("TELE-1"); expect(rowText[1]).toContain("TELE-2");
    fireEvent.click(screen.getByRole("button", { name: /3 new or updated issues/ }));
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/api/issues?"))).toBe(true);
  });

  it("registers stream shortcuts while editable targets remain unaffected", async () => {
    stubStorage(); installFetch([issue("iss-1", "TELE-1"), issue("iss-2", "TELE-2")]); render(root);
    await screen.findByText("TELE-1");
    const input = screen.getByLabelText("Search query"); input.focus(); fireEvent.keyDown(input, { key: "/" });
    expect(document.activeElement).toBe(input);
    fireEvent.keyDown(document.body, { key: "/" });
    expect(document.activeElement).toBe(input);
  });

  it("replaces the bare issues URL with the default stream query", async () => {
    stubStorage(); installFetch([]);
    const history = createMemoryHistory(); history.set({ value: "/issues" });
    render(() => <MemoryRouter history={history} root={() => <IssueList />} />);
    await waitFor(() => expect(history.get()).toContain("query=is%3Aunresolved"));
  });
});
