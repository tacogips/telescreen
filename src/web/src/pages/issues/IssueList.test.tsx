import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JSX } from "solid-js";
import { IssueList } from "./IssueList";

const issue = (id: string, shortId: string): Record<string, unknown> => ({
  id,
  number: 1,
  short_id: shortId,
  grouping_key: `group-${id}`,
  source: "github",
  resource_key: `repo-${id}`,
  display_locator: "repo:masked/file",
  resource_ref_json: null,
  collector_config_id: null,
  display_title: "Credential finding",
  rule_class: "credential",
  severity: "high",
  status: "unresolved",
  assignee_member_id: null,
  assignee_team_id: null,
  first_seen: "2026-10-01T00:00:00Z",
  last_seen: "2026-10-08T00:00:00Z",
  last_checked_at: "2026-10-08T00:00:00Z",
  sweep_covered: true,
  times_seen: 4,
  distinct_fingerprints: 1,
  regression_count: 0,
  resolved_at: null,
  resolution: null,
});

function root(): JSX.Element {
  return (
    <MemoryRouter
      root={() => (
        <>
          <IssueList />
          <div id="toast-region" aria-live="polite" />
        </>
      )}
    />
  );
}

function stubStorage(): void {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string): string | null => values.get(key) ?? null,
    setItem: (key: string, value: string): void => {
      values.set(key, value);
    },
    removeItem: (key: string): void => {
      values.delete(key);
    },
    clear: (): void => {
      values.clear();
    },
  } as Storage;
  vi.stubGlobal("localStorage", storage);
}

function responseFor(
  url: string,
  method: string,
  items: Record<string, unknown>[],
): Response {
  if (url.includes("/api/issues/stats")) {
    return new Response(
      JSON.stringify({ buckets: [{ start: "2026-10-01", count: 3 }] }),
      { status: 200 },
    );
  }
  if (method === "PATCH")
    return new Response(JSON.stringify(items[0]), { status: 200 });
  if (
    url.includes("/api/members") ||
    url.includes("/api/teams") ||
    url.includes("/api/workflows")
  ) {
    return new Response(JSON.stringify({ items: [] }), { status: 200 });
  }
  return new Response(JSON.stringify({ items, next_cursor: null }), {
    status: 200,
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("IssueList", () => {
  it("renders issue rows, status and finding count", async () => {
    stubStorage();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        responseFor("/api/issues", "GET", [issue("iss-01", "TELE-1")]),
      ),
    );
    render(root);
    expect(await screen.findByText("TELE-1")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
  });

  it("commits a status query to the URL-backed API request", async () => {
    stubStorage();
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) =>
        responseFor(String(input), init?.method ?? "GET", []),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(root);
    const query = await screen.findByLabelText("Search query");
    fireEvent.input(query, { target: { value: "is:resolved" } });
    fireEvent.keyDown(query, { key: "Enter" });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("status=resolved"),
        ),
      ).toBe(true),
    );
  });

  it("reactively shows a real-envelope server error after the list mounts", async () => {
    stubStorage();
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).includes("status=resolved")) {
          return new Response(
            JSON.stringify({
              error: { code: "internal_error", detail: "failed" },
            }),
            { status: 500 },
          );
        }
        return responseFor(String(input), init?.method ?? "GET", []);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(root);
    const query = await screen.findByLabelText("Search query");
    fireEvent.input(query, { target: { value: "is:resolved" } });
    fireEvent.keyDown(query, { key: "Enter" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "internal_error",
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("failed");
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("status=resolved"),
      ),
    ).toBe(true);
  });

  it("replaces the bare issues URL so Back returns to the prior entry", async () => {
    stubStorage();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => responseFor("/api/issues", "GET", [])),
    );
    const history = createMemoryHistory();
    history.set({ value: "/issues" });
    render(() => (
      <MemoryRouter
        history={history}
        root={() => (
          <>
            <IssueList />
            <div id="toast-region" aria-live="polite" />
          </>
        )}
      />
    ));

    await waitFor(() => {
      const current = history.get();
      expect(current).toContain("query=is%3Aunresolved");
      expect(current).toContain("sort=last_seen");
    });
    history.go(-1);
    await waitFor(() => expect(history.get()).toBe("/"));
  });

  it("requests one global findings chart and no per-issue statistics", async () => {
    stubStorage();
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) =>
        responseFor(String(input), init?.method ?? "GET", [
          issue("iss-01", "TELE-1"),
        ]),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(root);
    await screen.findByText("TELE-1");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/api/issues/stats?bucket=day&days=14"),
        ),
      ).toBe(true),
    );
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/api/issues/stats?"),
      ).length,
    ).toBe(1);
    expect(
      fetchMock.mock.calls.some(
        ([input]) =>
          String(input).includes("/stats") && String(input).includes("iss-01"),
      ),
    ).toBe(false);
  });

  it("resolves two selected loaded rows and reports one result toast", async () => {
    stubStorage();
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) =>
        responseFor(String(input), init?.method ?? "GET", [
          issue("iss-01", "TELE-1"),
          issue("iss-02", "TELE-2"),
        ]),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(root);
    await screen.findByText("TELE-1");
    fireEvent.click(screen.getByLabelText("Select TELE-1"));
    fireEvent.click(screen.getByLabelText("Select TELE-2"));
    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH")
          .length,
      ).toBe(2),
    );
    expect(await screen.findByText(/2 succeeded, 0 failed/)).toBeTruthy();
  });

  it("saves a view and dispatches the shell refresh event", async () => {
    stubStorage();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => responseFor("/api/issues", "GET", [])),
    );
    const prompt = vi
      .spyOn(window, "prompt")
      .mockReturnValue("Credential review");
    const onViews = vi.fn();
    window.addEventListener("telescreen:issue-views", onViews);
    render(root);
    fireEvent.click(await screen.findByRole("button", { name: "Save view" }));
    expect(prompt).toHaveBeenCalled();
    const saved =
      (globalThis.localStorage as Storage).getItem("telescreen.issueViews") ??
      "[]";
    expect(JSON.parse(saved)).toEqual([
      expect.objectContaining({
        name: "Credential review",
        query: "is:unresolved",
        sort: "last_seen",
        starred: false,
      }),
    ]);
    expect(onViews).toHaveBeenCalledOnce();
    window.removeEventListener("telescreen:issue-views", onViews);
  });

  it("shows the empty search state", async () => {
    stubStorage();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => responseFor("/api/issues", "GET", [])),
    );
    render(root);
    expect(await screen.findByText("Nothing exposed right now")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Connect or review collectors" }),
    ).toBeTruthy();
  });
});
