import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { MemoryRouter } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IssueList } from "./IssueList";

const issue = { id: "issue-1", number: 1, short_id: "T-1", display_title: "Credential finding", status: "resolved", source: "github", rule_class: "credential", severity: "high", display_locator: "repo/file", times_seen: 4, first_seen: "2026-10-01T00:00:00Z", last_seen: "2026-10-08T00:00:00Z", assignee_member_id: null, assignee_team_id: null, regression_count: 0, resolved_at: null, resolution: null };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("IssueList", () => {
  it("renders issue rows, status and event count", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).includes("/stats") ? { days: [], counts: [] } : { items: [issue], next_cursor: null }), { status: 200 })));
    render(() => <MemoryRouter root={IssueList} />);
    expect(await screen.findByText("T-1")).toBeTruthy();
    expect(screen.getByText("resolved", { selector: ".status-badge" })).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
  });

  it("refetches with the selected status in the URL", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(() => <MemoryRouter root={IssueList} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "resolved" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("status=resolved"))).toBe(true));
  });
});
