import {
  createEffect,
  createResource,
  createSignal,
  onCleanup,
  onMount,
  Show,
  type JSX,
} from "solid-js";
import { useSearchParams } from "@solidjs/router";
import {
  ApiError,
  directoryApi,
  issuesApi,
  workflowsApi,
} from "../../api/client";
import type {
  Issue,
  IssueStatus,
  Member,
  Page,
  Team,
  WorkflowVersionRef,
} from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { MiniBarChart, Placeholder, showToast, Tag } from "../../components/ui";
import { issueQueryToParams, issueQueryTokens } from "./query";
import {
  readIssueViews,
  removeIssueView,
  saveIssueView,
  type IssueView,
} from "./savedViews";
import { IssueBulkActions } from "./IssueBulkActions";
import { IssueListControls } from "./IssueListControls";
import { IssueListRows } from "./IssueListRows";
import "./issues.css";

const TRANSITIONS: Readonly<Record<IssueStatus, readonly IssueStatus[]>> = {
  unresolved: ["acknowledged", "in_progress", "resolved", "ignored"],
  acknowledged: ["in_progress", "resolved", "ignored", "unresolved"],
  in_progress: ["resolved", "ignored", "unresolved"],
  regressed: ["acknowledged", "in_progress", "resolved", "ignored"],
  resolved: ["unresolved"],
  ignored: ["unresolved"],
};

type BulkData = Readonly<{
  members: Member[];
  teams: Team[];
  workflows: WorkflowVersionRef[];
}>;

function errorValue(value: unknown): ApiError {
  return value instanceof ApiError
    ? value
    : new ApiError(0, "request_failed", "The request could not be completed.");
}

function normalizeQuery(value: string): string {
  const parts = value.match(/[^\s\[]+\[[^\]]*\]|[^\s]+/g) ?? [];
  const result: string[] = [];
  for (const part of parts) {
    const separator = part.indexOf(":");
    const key = separator < 0 ? "" : part.slice(0, separator);
    if (["is", "severity", "rule", "source", "assigned"].includes(key)) {
      const previous = result.findIndex((item) => item.startsWith(`${key}:`));
      if (previous >= 0) result.splice(previous, 1);
    }
    result.push(part);
  }
  return result.join(" ");
}

function canTransition(issues: readonly Issue[], target: IssueStatus): boolean {
  return (
    issues.length > 0 &&
    issues.every((issue) => TRANSITIONS[issue.status].includes(target))
  );
}

export function IssueList(): JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = (): string =>
    typeof searchParams.query === "string"
      ? searchParams.query
      : (searchParams.query?.[0] ?? "is:unresolved");
  const sort = (): string =>
    typeof searchParams.sort === "string"
      ? searchParams.sort
      : (searchParams.sort?.[0] ?? "last_seen");
  const [error, setError] = createSignal<ApiError | null>(null);
  const [additionalIssues, setAdditionalIssues] = createSignal<Issue[]>([]);
  const [nextCursor, setNextCursor] = createSignal<string | null>(null);
  const [selected, setSelected] = createSignal<Set<string>>(new Set());
  const [views, setViews] = createSignal<IssueView[]>([]);
  const [activeViewId, setActiveViewId] = createSignal<string | null>(null);
  const [lastCheckedIndex, setLastCheckedIndex] = createSignal<number | null>(
    null,
  );
  const [resource, { refetch }] = createResource(
    () => `${query()}\n${sort()}`,
    async (): Promise<Page<Issue>> => {
      setError(null);
      try {
        return await issuesApi.list(
          issueQueryToParams(issueQueryTokens(query()), sort()),
        );
      } catch (caught) {
        setError(errorValue(caught));
        return { items: [], next_cursor: null };
      }
    },
  );
  const [stats] = createResource(() =>
    issuesApi.stats({ bucket: "day", days: 14 }),
  );
  createEffect(() => setNextCursor(resource()?.next_cursor ?? null));
  const [bulkData] = createResource(
    () => selected().size > 0,
    async (hasSelection): Promise<BulkData> => {
      if (!hasSelection) return { members: [], teams: [], workflows: [] };
      const [members, teams, workflows] = await Promise.all([
        directoryApi.members(),
        directoryApi.teams(),
        workflowsApi.list(),
      ]);
      return {
        members: members.items,
        teams: teams.items,
        workflows: workflows.items.filter((workflow) => workflow.active),
      };
    },
  );
  const allIssues = (): Issue[] => [
    ...(resource()?.items ?? []),
    ...additionalIssues(),
  ];
  const selectedIssues = (): Issue[] =>
    allIssues().filter((issue) => selected().has(issue.id));
  const selectedStatuses = (): IssueStatus[] =>
    [
      ...new Set(
        selectedIssues().flatMap((issue) => TRANSITIONS[issue.status]),
      ),
    ].filter((status) => canTransition(selectedIssues(), status));
  const isDirty = (): boolean => {
    const active = views().find((view) => view.id === activeViewId());
    return (
      active !== undefined &&
      (active.query !== query() || active.sort !== sort())
    );
  };
  const setQuery = (value: string): void => {
    setAdditionalIssues([]);
    setSelected(new Set<string>());
    setSearchParams({
      query: normalizeQuery(value) || "is:unresolved",
      sort: sort(),
    });
  };
  const setSort = (value: string): void => {
    setAdditionalIssues([]);
    setSelected(new Set<string>());
    setSearchParams({ query: query(), sort: value });
  };
  const setSource = (source: string): void => {
    const tokens = issueQueryTokens(query()).filter(
      (token) => !(token.kind === "filter" && token.key === "source"),
    );
    setQuery(
      [
        ...tokens.map((token) =>
          token.kind === "text" ? token.value : `${token.key}:${token.value}`,
        ),
        ...(source === "" ? [] : [`source:${source}`]),
      ].join(" "),
    );
  };
  const selectedSource = (): string => {
    const token = issueQueryTokens(query()).find(
      (item) => item.kind === "filter" && item.key === "source" && item.valid,
    );
    return token?.kind === "filter" ? token.value : "";
  };
  const refresh = (): void => {
    setAdditionalIssues([]);
    setSelected(new Set<string>());
    setError(null);
    void refetch();
  };
  const resetView = (): void => {
    const active = views().find((view) => view.id === activeViewId());
    setAdditionalIssues([]);
    setSelected(new Set<string>());
    setSearchParams({
      query: active?.query ?? "is:unresolved",
      sort: active?.sort ?? "last_seen",
    });
  };
  const loadMore = async (): Promise<void> => {
    const cursor = nextCursor();
    if (cursor === null) return;
    try {
      const params = issueQueryToParams(issueQueryTokens(query()), sort());
      params.set("cursor", cursor);
      const next = await issuesApi.list(params);
      setAdditionalIssues((prior) => [...prior, ...next.items]);
      setNextCursor(next.next_cursor);
    } catch (caught) {
      setError(errorValue(caught));
    }
  };
  const toggleIssue = (
    issue: Issue,
    index: number,
    event: MouseEvent,
  ): void => {
    setSelected((prior) => {
      const next = new Set(prior);
      const anchor = lastCheckedIndex();
      if (event.shiftKey && anchor !== null) {
        for (
          let cursor = Math.min(anchor, index);
          cursor <= Math.max(anchor, index);
          cursor += 1
        ) {
          const row = allIssues()[cursor];
          if (row !== undefined) next.add(row.id);
        }
      } else if (event.metaKey || event.ctrlKey) {
        if (next.has(issue.id)) next.delete(issue.id);
        else next.add(issue.id);
      } else if (next.has(issue.id)) next.delete(issue.id);
      else next.add(issue.id);
      return next;
    });
    setLastCheckedIndex(index);
  };
  const toggleAll = (checked: boolean): void => {
    setSelected(
      checked
        ? new Set<string>(allIssues().map((issue) => issue.id))
        : new Set<string>(),
    );
  };
  const runBulk = async (
    action: (issue: Issue) => Promise<unknown>,
    success: string,
  ): Promise<void> => {
    const results = await Promise.all(
      selectedIssues().map(async (issue) => {
        try {
          await action(issue);
          return true;
        } catch {
          return false;
        }
      }),
    );
    const succeeded = results.filter(Boolean).length;
    const failed = results.length - succeeded;
    showToast(
      `${success}: ${succeeded} succeeded, ${failed} failed`,
      failed > 0 ? "warning" : "success",
    );
    setSelected(new Set<string>());
    refresh();
  };
  const changeStatus = (status: IssueStatus): void => {
    void runBulk(
      (issue) => issuesApi.update(issue.id, { status }),
      `Updated status to ${status.replaceAll("_", " ")}`,
    );
  };
  const assign = (value: string): void => {
    if (value !== "")
      void runBulk(
        (issue) => issuesApi.update(issue.id, { assignee_ref: value }),
        "Assigned issues",
      );
  };
  const startWorkflow = (name: string): void => {
    if (name !== "")
      void runBulk(
        (issue) => workflowsApi.startRun(name, issue.id),
        "Started workflow",
      );
  };
  const saveView = (): void => {
    const name = globalThis.window.prompt("Name this issue view");
    if (name === null) return;
    const next = saveIssueView(views(), name, query(), sort());
    setViews(next);
    const created = next[next.length - 1];
    if (created !== undefined && created.name === name.trim())
      setActiveViewId(created.id);
  };
  const applyView = (view: IssueView): void => {
    setActiveViewId(view.id);
    setAdditionalIssues([]);
    setSelected(new Set<string>());
    setSearchParams({ query: view.query, sort: view.sort });
  };
  const removeView = (view: IssueView): void => {
    setViews(removeIssueView(views(), view.id));
    if (activeViewId() === view.id) setActiveViewId(null);
  };
  onMount(() => {
    setViews(readIssueViews());
    const refreshViews = (): void => {
      setViews(readIssueViews());
    };
    globalThis.window.addEventListener("telescreen:issue-views", refreshViews);
    onCleanup(() =>
      globalThis.window.removeEventListener(
        "telescreen:issue-views",
        refreshViews,
      ),
    );
  });
  createEffect(() => {
    if (searchParams.query === undefined || searchParams.sort === undefined) {
      setSearchParams({ query: query(), sort: sort() }, { replace: true });
    }
  });

  const bulk = (): BulkData =>
    bulkData() ?? { members: [], teams: [], workflows: [] };
  return (
    <section class="page-content issue-page">
      <IssueListControls
        query={query()}
        sort={sort()}
        source={selectedSource()}
        views={views()}
        activeViewId={activeViewId()}
        dirty={isDirty()}
        onQuery={setQuery}
        onSort={setSort}
        onSource={setSource}
        onSave={saveView}
        onReset={resetView}
        onApply={applyView}
        onRemove={removeView}
      />
      <Show when={error()} keyed>
        {(value) => <ErrorBanner error={value} />}
      </Show>
      <section class="issues-trend" aria-label="Issue findings trend">
        <strong>Findings, last 14 days (all issues)</strong>
        <Show
          when={stats()}
          fallback={<Placeholder label="Loading findings trend" />}
        >
          {(value) => (
            <MiniBarChart
              values={(value()?.buckets ?? []).map((bucket) => bucket.count)}
              label="Findings, last 14 days (all issues)"
              height={36}
            />
          )}
        </Show>
      </section>
      <IssueListRows
        issues={allIssues()}
        selected={selected()}
        query={query()}
        nextCursor={nextCursor()}
        loading={resource.loading}
        onToggle={toggleIssue}
        onToggleAll={toggleAll}
        onLoadMore={() => void loadMore()}
      />
      <Show when={selected().size > 0}>
        <IssueBulkActions
          selectedCount={selected().size}
          statuses={selectedStatuses()}
          members={bulk().members}
          teams={bulk().teams}
          workflows={bulk().workflows}
          onStatus={changeStatus}
          onAssign={assign}
          onStartWorkflow={startWorkflow}
          onClear={() => toggleAll(false)}
        />
      </Show>
      <Show when={views().length > 0}>
        <Tag variant="muted">
          {views().filter((view) => view.starred).length} starred views
        </Tag>
      </Show>
    </section>
  );
}
