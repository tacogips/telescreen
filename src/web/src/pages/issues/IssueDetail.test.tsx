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
  base_severity: "medium",
  risk_score: 82,
  exposure_visibility: "public",
  policy_name: "public-secret",
  policy_version: 2,
  exposure: {
    visibility: "public",
    external_principal_count: 2,
    shared_drive: false,
    channel_kind: null,
    repo_visibility: "public",
    is_fork: false,
  },
  exposure_changed_at: null,
  asset_tier: "high",
  assignee_ref: null,
  assignee: null,
  owner: { present: false, member_ref: null },
  active_exception: null,
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
let issueStatus = "resolved";
let commentPosted = false;
let activityReads = 0;
let mergedInto: Readonly<{ id: string; short_id: string }> | null = null;
let mergedChildren: readonly Readonly<{ id: string; short_id: string; source: string; display_locator: string; status: string }>[] = [];
let assignedPerson: Readonly<{ kind: "member"; ref: string; display_name: string }> | null = null;
let canEditComment = true;
let exposureMissing = false;
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
  issueStatus = "resolved";
  commentPosted = false;
  activityReads = 0;
  mergedInto = null;
  mergedChildren = [];
  assignedPerson = null;
  canEditComment = true;
  exposureMissing = false;
});

function response(url: string, method = "GET"): Response {
  const selected = /\/api\/issues\/issue-1\/events\/([^/?]+)$/.exec(url);
  if (selected !== null) {
    const selector = decodeURIComponent(selected[1]!);
    const index = selector === "oldest" ? 1 : selector === "latest" || selector === "recommended" ? 0 : events.findIndex((item) => item.id === selector);
    const eventValue = events[index] ?? events[0]!;
    return new Response(JSON.stringify({ event: eventValue, previous_id: index === 0 ? events[1]!.id : null, next_id: index === 1 ? events[0]!.id : null, position: index === 1 ? 1 : 2, total: 120 }), { status: 200 });
  }
  if (url.includes("/api/issues/issue-1/stats")) return new Response(JSON.stringify({ buckets: [{ start: "2026-10-01T00:00:00Z", count: 12 }, { start: "2026-10-02T00:00:00Z", count: 8 }] }), { status: 200 });
  if (url.includes("/api/issues/issue-1/tags")) return new Response(JSON.stringify({ items: [{ key: "detector", value: "rule", count: 83 }, { key: "source", value: "github", count: 17 }], total: 200 }), { status: 200 });
  if (url.includes("/api/issues/issue-1/activity")) {
    activityReads += 1;
    const items = commentPosted ? [{ id: "activity-comment", kind: "commented", actor: { kind: "member", ref: "member:alice", display_name: "Alice Example" }, body: "Remediation reviewed", at: "2026-10-08T00:00:00Z", comment: { id: "comment-1", body: "Remediation reviewed", edited_at: null, deleted: false, can_edit: canEditComment } }] : [];
    return new Response(JSON.stringify({ items }), { status: 200 });
  }
  if (url.includes("/api/issues/issue-1/external-issues")) return new Response(JSON.stringify({ items: [] }), { status: 200 });
  if (url.includes("/api/external-issues/github/repos")) return new Response(JSON.stringify({ items: [] }), { status: 200 });
  if (url.includes("/api/issues/issue-1/events"))
    return new Response(JSON.stringify({ items: events, next_cursor: null }), {
      status: 200,
    });
  if (url.includes("/api/issues/issue-1/history"))
    return new Response(JSON.stringify({ items: history, next_cursor: null }), {
      status: 200,
    });
  if (url.includes("/api/issues/issue-1/occurrences"))
    return new Response(JSON.stringify({
      items: [
        { issue_id: "issue-2", short_id: "T-2", source: "github", display_locator: "repo/other", status: "unresolved", severity: "critical", exposure_visibility: "public" },
        { issue_id: "issue-3", short_id: "T-3", source: "slack", display_locator: "team/channel", status: "pending_verification", severity: "high", exposure_visibility: "external_shared" },
      ],
      key_versions: [1],
    }), { status: 200 });
  if (url.includes("/api/runs?") || url.includes("/api/evidence?"))
    return new Response(JSON.stringify(emptyPage), { status: 200 });
  if (
    url.includes("/api/members") ||
    url.includes("/api/teams") ||
    url.includes("/api/workflows")
  )
    return new Response(JSON.stringify({ items: url.includes("/api/members") ? [{ id: "member-1", handle: "alice", display_name: "Alice Example", email: null, slack_user_id: null, active: true }] : [] }), { status: 200 });
  if (url.endsWith("/api/issues/issue-1/archive") && method === "POST") {
    issueStatus = "archived";
    return new Response(JSON.stringify({ ...issue, status: issueStatus }), { status: 200 });
  }
  if (url.endsWith("/api/issues/issue-1/unarchive") && method === "POST") {
    issueStatus = "unresolved";
    return new Response(JSON.stringify({ ...issue, status: issueStatus }), { status: 200 });
  }
  if (url.includes("/api/issues/issue-1/comments") && method === "POST") {
    commentPosted = true;
    return new Response(JSON.stringify({ accepted: true }), { status: 200 });
  }
  if (url.includes("/api/issues/issue-1/comments/comment-1") && method === "PATCH") return new Response(JSON.stringify({ id: "comment-1" }), { status: 200 });
  if (url.includes("/api/issues/issue-1/comments/comment-1") && method === "DELETE") return new Response(null, { status: 204 });
  return new Response(JSON.stringify({ ...issue, ...(exposureMissing ? { exposure: null } : {}), status: issueStatus, assignee: assignedPerson, merged_into: mergedInto, merged_children: mergedChildren, subscription: { subscribed: false, reason: null }, suggested_assignees: [] }), { status: 200 });
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
  it("archives for 24h with the server duration_minutes contract", async () => {
    issueStatus = "unresolved";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      response(String(input), init?.method),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    fireEvent.click(screen.getByText("Archive"));
    fireEvent.click(screen.getByRole("button", { name: "For 24h" }));

    let archiveCall: (typeof fetchMock.mock.calls)[number] | undefined;
    await waitFor(() => {
      archiveCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/api/issues/issue-1/archive") &&
          init?.method === "POST",
      );
      expect(archiveCall).toBeDefined();
    });
    const body: Readonly<Record<string, unknown>> = JSON.parse(
      String(archiveCall?.[1]?.body),
    );
    expect(body.kind).toBe("for_duration");
    expect(body.duration_minutes).toBe(1440);
    expect(body).not.toHaveProperty("duration_days");
  });

  it("shows Unarchive for an archived issue and sends POST /unarchive", async () => {
    issueStatus = "archived";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      response(String(input), init?.method),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    fireEvent.click(screen.getByRole("button", { name: "Unarchive" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/api/issues/issue-1/unarchive") &&
            init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

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

  it("renders endpoint stats and tag percentages, history transition, and masked snippet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
        response(String(input), init?.method),
      ),
    );
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    const graph = screen.getByRole("img", {
      name: "Findings over selected period",
    });
    const nonzeroBars = [
      ...graph.querySelectorAll('rect[fill="var(--chart-matching)"]'),
    ].filter((bar) => Number(bar.getAttribute("height")) > 0);
    expect(nonzeroBars.length).toBe(2);
    expect(screen.getByText("detector: rule · 41.5%")).toBeTruthy();
    expect(screen.getByText("in_progress → resolved")).toBeTruthy();
    expect(screen.getByText("***[4]").className).toContain("redacted-mask");
  });

  it("renders an issue whose source reported no exposure instead of crashing", async () => {
    exposureMissing = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
        response(String(input), init?.method),
      ),
    );
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    expect(screen.getByText("Exposure unknown: the source reported no sharing data.")).toBeTruthy();
  });

  it("renders /stats and /tags in the server contract shape", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/issues/issue-1/stats")) {
        return new Response(JSON.stringify({
          items: [
            { at: "2026-10-01T00:00:00+00:00", count: 12 },
            { at: "2026-10-02T00:00:00+00:00", count: 8 },
          ],
          total: 20,
        }), { status: 200 });
      }
      if (url.includes("/api/issues/issue-1/tags")) {
        return new Response(JSON.stringify({
          total: 200,
          tags: [{
            key: "detector",
            values: [{ value: "rule", count: 83, percent: 41.5 }],
            other_count: 0,
          }],
        }), { status: 200 });
      }
      return response(url, init?.method);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();

    expect(
      await screen.findByRole("heading", { name: "Credential finding" }),
    ).toBeTruthy();
    expect(screen.getByText("detector: rule · 41.5%")).toBeTruthy();
    expect(fetchMock.mock.calls.some(([input]) =>
      String(input).includes("/api/issues/issue-1/stats"),
    )).toBe(true);
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

  it("posts a comment then refetches activity with the actor", async () => {
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
    expect(screen.getAllByText("Remediation reviewed").length).toBe(1);
    expect(screen.getAllByText("Alice Example").length).toBeGreaterThan(1);
    expect(activityReads).toBeGreaterThan(1);
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/comments") && init?.method === "POST",
      ),
    ).toBe(true);
  });

  it("uses selector endpoint ids for Oldest and Previous and shows group position", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => response(String(input), init?.method));
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    expect(screen.getByText("2 of 120")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/events/event-old"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Oldest" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/events/oldest"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/events/event-new"))).toBe(true));
  });

  it("renders a display-name assignee and an exactly named More button", async () => {
    assignedPerson = { kind: "member", ref: "member:alice", display_name: "Alice Example" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => response(String(input), init?.method)));
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    expect(screen.getByLabelText("Alice Example")).toBeTruthy();
    expect(screen.getAllByText("Alice Example").length).toBeGreaterThan(1);
    expect(screen.getByRole("button", { name: /^More$/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^More$/ }));
    expect(screen.getByRole("menuitem", { name: "Copy permalink" })).toBeTruthy();
  });

  it("shows merged child navigation and unmerge actions", async () => {
    mergedInto = { id: "issue-primary", short_id: "T-9" };
    mergedChildren = [{ id: "issue-child", short_id: "T-10", source: "github", display_locator: "repo/child", status: "unresolved" }];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => response(String(input), init?.method)));
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    expect(screen.getByRole("link", { name: "T-9" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Unmerge all" })).toBeTruthy();
  });

  it("shows comment edit/delete only when allowed and suggests @ mentions", async () => {
    commentPosted = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => response(String(input), init?.method)));
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    expect(await screen.findByRole("button", { name: "Edit" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
    fireEvent.input(screen.getByLabelText("Add a comment"), { target: { value: "@al" } });
    expect(await screen.findByRole("option", { name: /@alice/ })).toBeTruthy();
  });

  it("hides comment edit/delete when the activity item is not editable", async () => {
    commentPosted = true;
    canEditComment = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => response(String(input), init?.method)));
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    expect(await screen.findByText("Remediation reviewed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("submits an external link and keeps GitHub create disabled without repositories", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => response(String(input), init?.method));
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    fireEvent.click(screen.getByRole("button", { name: /^More$/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Link external issue" }));
    fireEvent.input(screen.getByLabelText("External issue URL"), { target: { value: "https://github.example.test/org/repo/issues/9" } });
    fireEvent.input(screen.getByLabelText("External issue title"), { target: { value: "Tracking issue" } });
    const submitLink = screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement;
    expect(submitLink.disabled).toBe(false);
    fireEvent.submit(screen.getByRole("dialog"));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input).endsWith("/external-issues") && init?.method === "POST" && JSON.parse(String(init.body)).title === "Tracking issue")).toBe(true));
    fireEvent.click(screen.getByRole("menuitem", { name: "Create GitHub issue" }));
    expect((screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("requires an exception reason and posts its duration", async () => {
    issueStatus = "unresolved";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/exceptions") && init?.method === "POST")
        return new Response(JSON.stringify({ id: "exc-1" }), { status: 200 });
      return response(String(input), init?.method);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    fireEvent.click(screen.getByRole("button", { name: "Request exception..." }));
    const submit = screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.input(screen.getByLabelText("Reason"), { target: { value: "Reviewed" } });
    expect(submit.disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Exception duration"), { target: { value: "7d" } });
    fireEvent.click(submit);
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith("/exceptions") && init?.method === "POST" &&
      JSON.stringify(JSON.parse(String(init.body))) === JSON.stringify({ reason: "Reviewed", duration: "7d" }),
    )).toBe(true));
  });

  it("posts forever exceptions through the typed duration client", async () => {
    issueStatus = "unresolved";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/exceptions") && init?.method === "POST")
        return new Response(JSON.stringify({ id: "exc-forever" }), { status: 200 });
      return response(String(input), init?.method);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    fireEvent.click(screen.getByRole("button", { name: "Request exception..." }));
    fireEvent.input(screen.getByLabelText("Reason"), { target: { value: "Reviewed" } });
    fireEvent.change(screen.getByLabelText("Exception duration"), { target: { value: "forever" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith("/exceptions") && init?.method === "POST" &&
      JSON.stringify(JSON.parse(String(init.body))) === JSON.stringify({ reason: "Reviewed", duration: "forever" }),
    )).toBe(true));
  });

  it("requires a false-positive note and sends the dedicated resolve request", async () => {
    issueStatus = "unresolved";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/resolve") && init?.method === "POST")
        return new Response(JSON.stringify(issue), { status: 200 });
      return response(String(input), init?.method);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    fireEvent.click(screen.getByRole("button", { name: "Resolve options" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Resolve as false positive..." }));
    const submit = screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.input(screen.getByLabelText("Reason"), { target: { value: "Not a credential" } });
    fireEvent.click(submit);
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith("/resolve") && init?.method === "POST" &&
      JSON.parse(String(init.body)).reason === "false_positive" &&
      JSON.parse(String(init.body)).note === "Not a credential",
    )).toBe(true));
  });

  it("shows include-linked only for credentials and posts remediation", async () => {
    issueStatus = "unresolved";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/remediation") && init?.method === "POST")
        return new Response(JSON.stringify({ issue_ids: ["issue-1"], rescans_enqueued: 1 }), { status: 200 });
      return response(String(input), init?.method);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    fireEvent.click(screen.getByRole("button", { name: "Mark remediated..." }));
    expect(screen.getByLabelText("Include linked occurrences")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Include linked occurrences"));
    fireEvent.change(screen.getByLabelText("Remediation action"), { target: { value: "restrict" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith("/remediation") && init?.method === "POST" &&
      JSON.parse(String(init.body)).action === "restrict" &&
      JSON.parse(String(init.body)).include_linked === true,
    )).toBe(true));
  });

  it("shows verifying and policy banners and two linked credential occurrences", async () => {
    issueStatus = "pending_verification";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      response(String(input), init?.method),
    ));
    renderDetail();
    await screen.findByRole("heading", { name: "Credential finding" });
    expect(screen.getByText("Verifying: waiting for a rescan of the resource")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Linked occurrences (2)" })).toBeTruthy();
    expect(screen.getByText(/key rotation starts new groups/i)).toBeTruthy();
    expect(screen.getByText(/T-2 · github · repo\/other/)).toBeTruthy();
    expect(screen.getByText(/T-3 · slack · team\/channel/)).toBeTruthy();
  });

  it("shows the allowed policy banner", async () => {
    issueStatus = "allowed";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      response(String(input), init?.method),
    ));
    renderDetail();
    expect(await screen.findByText("Allowed by policy public-secret v2")).toBeTruthy();
  });

  it("renders an exception-expired history label", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/history"))
        return new Response(JSON.stringify({ items: [{ ...history[0], kind: "exception_expired" }], next_cursor: null }), { status: 200 });
      return response(String(input), init?.method);
    }));
    renderDetail();
    await screen.findAllByText("Exception expired");
  });
});
