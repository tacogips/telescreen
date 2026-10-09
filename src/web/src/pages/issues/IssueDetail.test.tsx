import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@solidjs/testing-library";
import { MemoryRouter, Route } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Toast } from "../../components/ui";
import { IssueDetail } from "./IssueDetail";

const issue = {
  id: "issue-1",
  number: 1,
  short_id: "T-1",
  grouping_key: "group-1",
  display_title: "Credential finding",
  status: "resolved",
  source: "github",
  resource_key: "repo-1",
  display_locator: "repo/file",
  resource_ref_json: null,
  collector_config_id: null,
  rule_class: "credential",
  severity: "high",
  times_seen: 4,
  distinct_fingerprints: 1,
  first_seen: "2026-10-01T00:00:00Z",
  last_seen: "2026-10-08T00:00:00Z",
  last_checked_at: "2026-10-08T00:00:00Z",
  sweep_covered: true,
  regression_count: 0,
  assignee_member_id: null,
  assignee_team_id: null,
  resolved_at: null,
  resolution: null,
};
const emptyPage = { items: [], next_cursor: null };
const eventAt = (daysAgo: number): string =>
  new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000).toISOString();
const event = (id: string, observedAt: string) => ({
  id,
  issue_id: "issue-1",
  scan_job_id: null,
  source: "github",
  resource_key: "repo-1",
  display_locator: "repo/file",
  location: "org > repo > file:1",
  rule_id: "rule-1",
  rule_class: "credential",
  detector: "rule",
  confidence: 0.9,
  masked_snippet: "value ***[4]",
  fingerprint: "abcdef123456",
  fingerprint_key_id: "key-1",
  observed_at: observedAt,
});
const events = [event("event-new", eventAt(1)), event("event-old", eventAt(2))];
const history = [
  {
    id: "history-1",
    issue_id: "issue-1",
    kind: "status_changed",
    actor_ref: "member:analyst",
    from_value: "in_progress",
    to_value: "resolved",
    note: null,
    at: "2026-10-08T00:00:00Z",
  },
];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function response(url: string, method = "GET"): Response {
  if (url.includes("/api/issues/issue-1/events"))
    return new Response(JSON.stringify({ items: events, next_cursor: null }), {
      status: 200,
    });
  if (url.includes("/api/issues/issue-1/history"))
    return new Response(JSON.stringify({ items: history, next_cursor: null }), {
      status: 200,
    });
  if (url.includes("/api/runs?") || url.includes("/api/evidence?"))
    return new Response(JSON.stringify(emptyPage), { status: 200 });
  if (
    url.includes("/api/members") ||
    url.includes("/api/teams") ||
    url.includes("/api/workflows")
  )
    return new Response(JSON.stringify({ items: [] }), { status: 200 });
  if (url.includes("/api/issues/issue-1/comments") && method === "POST")
    return new Response(JSON.stringify({ accepted: true }), { status: 200 });
  return new Response(JSON.stringify(issue), { status: 200 });
}

function renderDetail(): void {
  render(() => (
    <MemoryRouter
      root={() => (
        <>
          <IssueDetail id="issue-1" />
          <Toast />
        </>
      )}
    >
      <Route path="*" component={() => <IssueDetail id="issue-1" />} />
    </MemoryRouter>
  ));
}

describe("IssueDetail", () => {
  it("shows only allowed resolved transitions and PATCHes a reopen", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) =>
        response(String(input), init?.method),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    expect(
      await screen.findByRole("heading", { name: "Credential finding" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reopen" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Resolve" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/api/issues/issue-1") &&
            init?.method === "PATCH" &&
            String(init.body).includes('"status":"unresolved"'),
        ),
      ).toBe(true),
    );
  });

  it("renders the loaded two-day graph, history transition, and masked snippet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
        response(String(input), init?.method),
      ),
    );
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    const graph = screen.getByRole("img", {
      name: "Loaded findings, last 14 days",
    });
    const nonzeroBars = [
      ...graph.querySelectorAll('rect[fill="var(--chart-matching)"]'),
    ].filter((bar) => Number(bar.getAttribute("height")) > 0);
    expect(nonzeroBars.length).toBe(2);
    expect(screen.getByText("in_progress → resolved")).toBeTruthy();
    expect(screen.getByText("***[4]").className).toContain("redacted-mask");
  });

  it("shows evidence rejection rule classes from the contract-shaped 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (
          String(input).startsWith("/api/evidence?") &&
          init?.method === "PUT"
        )
          return new Response(
            JSON.stringify({
              error: {
                code: "evidence_rejected",
                detail: "evidence rejected",
                rule_classes: ["credential"],
              },
            }),
            { status: 422 },
          );
        if (String(input).startsWith("/api/evidence?"))
          return new Response(JSON.stringify(emptyPage), { status: 200 });
        return response(String(input), init?.method);
      }),
    );
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    fireEvent.change(screen.getByLabelText("Upload evidence"), {
      target: {
        files: [
          new File(["safe test content"], "evidence.txt", {
            type: "text/plain",
          }),
        ],
      },
    });
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Evidence rejected");
      expect(screen.getByRole("status")).toHaveTextContent("credential");
    });
  });

  it("preserves evidence 415 and 503 error codes in ErrorBanner", async () => {
    for (const [status, code] of [
      [415, "unsupported_media_type"],
      [503, "evidence_scanner_unavailable"],
    ] as const) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
          if (
            String(input).startsWith("/api/evidence?") &&
            init?.method === "PUT"
          )
            return new Response(
              JSON.stringify({ error: { code, detail: "safe static detail" } }),
              { status },
            );
          if (String(input).startsWith("/api/evidence?"))
            return new Response(JSON.stringify(emptyPage), { status: 200 });
          return response(String(input), init?.method);
        }),
      );
      renderDetail();
      await screen.findByRole("heading", { name: "Credential finding" });
      fireEvent.change(screen.getByLabelText("Upload evidence"), {
        target: {
          files: [
            new File(["safe test content"], "evidence.txt", {
              type: "text/plain",
            }),
          ],
        },
      });
      await waitFor(() =>
        expect(screen.getByRole("alert").textContent).toContain(code),
      );
      cleanup();
    }
  });

  it("posts a comment and appends it to activity", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) =>
        response(String(input), init?.method),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    fireEvent.input(screen.getByLabelText("Add a comment"), {
      target: { value: "Remediation reviewed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add a comment" }));
    expect(await screen.findByText("Remediation reviewed")).toBeTruthy();
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/comments") && init?.method === "POST",
      ),
    ).toBe(true);
  });
});
