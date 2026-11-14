import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route } from "@solidjs/router";
import { IssueDetail } from "./IssueDetail";

const issue = { id: "issue-1", number: 1, short_id: "T-1", display_title: "Credential finding", status: "resolved", source: "github", rule_class: "credential", severity: "high", display_locator: "repo/file", times_seen: 4, first_seen: "2026-10-01T00:00:00Z", last_seen: "2026-10-08T00:00:00Z", assignee_member_id: null, assignee_team_id: null, regression_count: 0, resolved_at: null, resolution: null };
const emptyPage = { items: [], next_cursor: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function response(url: string): Response {
  if (url.endsWith("/events") || url.endsWith("/history") || url.endsWith("/runs") || url.includes("/evidence?")) return new Response(JSON.stringify(emptyPage), { status: 200 });
  return new Response(JSON.stringify(issue), { status: 200 });
}

describe("IssueDetail", () => {
  it("shows only allowed resolved transitions and PATCHes a transition", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") return response(String(input));
      return response(String(input));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(() => <MemoryRouter root={() => <IssueDetail id="issue-1" />}><Route path="*" component={() => <IssueDetail id="issue-1" />} /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Credential finding" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reopen" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "acknowledged" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input).endsWith("/api/issues/issue-1") && init?.method === "PATCH" && String(init.body).includes('"status":"unresolved"'))).toBe(true));
  });

  it("shows evidence rejection rule classes from the contract-shaped 422", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith("/api/evidence?") && init?.method === "PUT") return new Response(JSON.stringify({ error: { code: "evidence_rejected", detail: "evidence rejected", rule_classes: ["credential"] } }), { status: 422 });
      if (String(input).startsWith("/api/evidence?")) return new Response(JSON.stringify(emptyPage), { status: 200 });
      return response(String(input));
    }));
    render(() => <MemoryRouter root={() => <IssueDetail id="issue-1" />}><Route path="*" component={() => <IssueDetail id="issue-1" />} /></MemoryRouter>);
    await screen.findByRole("heading", { name: "Credential finding" });
    const file = new File(["safe test content"], "evidence.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Upload evidence"), { target: { files: [file] } });
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("Evidence rejected");
      expect(screen.getByRole("status").textContent).toContain("credential");
    });
  });

  it("preserves evidence 415 and 503 error codes in ErrorBanner", async () => {
    for (const [status, code] of [[415, "unsupported_media_type"], [503, "evidence_scanner_unavailable"]] as const) {
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).startsWith("/api/evidence?") && init?.method === "PUT") return new Response(JSON.stringify({ error: { code, detail: "safe static detail" } }), { status });
        if (String(input).startsWith("/api/evidence?")) return new Response(JSON.stringify(emptyPage), { status: 200 });
        return response(String(input));
      }));
      render(() => <MemoryRouter root={() => <IssueDetail id="issue-1" />}><Route path="*" component={() => <IssueDetail id="issue-1" />} /></MemoryRouter>);
      await screen.findByRole("heading", { name: "Credential finding" });
      const file = new File(["safe test content"], "evidence.txt", { type: "text/plain" });
      fireEvent.change(screen.getByLabelText("Upload evidence"), { target: { files: [file] } });
      await waitFor(() => expect(screen.getByRole("alert").textContent).toContain(code));
      cleanup();
    }
  });
});
