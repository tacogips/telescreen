import { createEffect, createResource, createSignal, For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import { ApiError, apiFetch, issuesApi } from "../../api/client";
import type { Issue, IssueStats, Page } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { LoadMore } from "../../components/LoadMore";
import { RelativeTime } from "../../components/RelativeTime";
import { Sparkline } from "../../components/Sparkline";
import { StatusBadge } from "../../components/StatusBadge";

function StatsCell(props: Readonly<{ issue: Issue }>): JSX.Element {
  const [stats] = createResource(props.issue.id, async (id): Promise<IssueStats> => {
    return apiFetch<IssueStats>(`/api/issues/${encodeURIComponent(id)}/stats?bucket=day&days=14`);
  });
  return <Show when={stats()} fallback={<span aria-label="Loading activity">…</span>}>
    {(value) => <Sparkline values={value().counts} label={`14-day activity for ${props.issue.short_id}`} />}
  </Show>;
}

export function IssueList(): JSX.Element {
  const [status, setStatus] = createSignal("");
  const [source, setSource] = createSignal("");
  const [ruleClass, setRuleClass] = createSignal("");
  const [severity, setSeverity] = createSignal("");
  const [assignee, setAssignee] = createSignal("");
  const [query, setQuery] = createSignal("");
  const [sort, setSort] = createSignal("last_seen");
  const [nextCursor, setNextCursor] = createSignal<string | null>(null);
  const [pages, setPages] = createSignal<Issue[]>([]);
  const [loadKey, setLoadKey] = createSignal(0);
  const [page] = createResource(loadKey, async (): Promise<Page<Issue>> => {
    const params = new URLSearchParams();
    if (status()) params.set("status", status());
    if (source()) params.set("source", source());
    if (ruleClass()) params.set("rule_class", ruleClass());
    if (severity()) params.set("severity", severity());
    if (assignee()) params.set("assignee", assignee());
    if (query()) params.set("q", query());
    params.set("sort", sort());
    return issuesApi.list(params);
  });
  const [error, setError] = createSignal<ApiError | null>(null);
  createEffect(() => setNextCursor(page()?.next_cursor ?? null));

  const refresh = (): void => {
    setPages([]);
    setNextCursor(null);
    setError(null);
    setLoadKey((value) => value + 1);
  };
  const loadMore = async (): Promise<void> => {
    const cursorValue = nextCursor();
    if (cursorValue === null) return;
    setError(null);
    try {
      const params = new URLSearchParams({ sort: sort(), cursor: cursorValue });
      for (const [key, value] of [["status", status()], ["source", source()], ["rule_class", ruleClass()], ["severity", severity()], ["assignee", assignee()], ["q", query()]] as const) {
        if (value) params.set(key, value);
      }
      const following = await issuesApi.list(params);
      setPages((items) => [...items, ...following.items]);
      setNextCursor(following.next_cursor);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed."));
    }
  };
  const shownIssues = (): Issue[] => [...(page()?.items ?? []), ...pages()];

  return <section class="page-content">
    <div class="page-heading"><div><p class="eyebrow">Triage</p><h1>Issues</h1></div></div>
    <form class="filters" onSubmit={(event) => { event.preventDefault(); refresh(); }}>
      <label>Status<select aria-label="Status" value={status()} onChange={(event) => { setStatus(event.currentTarget.value); refresh(); }}>
        <option value="">All statuses</option><For each={["unresolved", "acknowledged", "in_progress", "resolved", "ignored", "regressed"]}>{(value) => <option value={value}>{value.replaceAll("_", " ")}</option>}</For>
      </select></label>
      <label>Source<input aria-label="Source" value={source()} onInput={(event) => setSource(event.currentTarget.value)} /></label>
      <label>Rule class<input aria-label="Rule class" value={ruleClass()} onInput={(event) => setRuleClass(event.currentTarget.value)} /></label>
      <label>Severity<input aria-label="Severity" value={severity()} onInput={(event) => setSeverity(event.currentTarget.value)} /></label>
      <label>Assignee<input aria-label="Assignee" value={assignee()} onInput={(event) => setAssignee(event.currentTarget.value)} /></label>
      <label>Search<input aria-label="Search issues" value={query()} onInput={(event) => setQuery(event.currentTarget.value)} /></label>
      <label>Sort<select aria-label="Sort" value={sort()} onChange={(event) => { setSort(event.currentTarget.value); refresh(); }}>
        <option value="last_seen">Last seen</option><option value="first_seen">First seen</option><option value="times_seen">Events</option>
      </select></label>
      <button class="button-primary" type="submit">Apply filters</button>
    </form>
    <ErrorBanner error={error()} />
    <Show when={!page.loading} fallback={<p>Loading issues…</p>}>
      <div class="table-wrap"><table><thead><tr><th>Issue</th><th>Status</th><th>First seen</th><th>Last seen</th><th>Events</th><th>14 days</th></tr></thead>
        <tbody><For each={shownIssues()}>{(issue) => <tr>
          <td><A href={`/issues/${encodeURIComponent(issue.id)}`}><strong>{issue.short_id}</strong> {issue.display_title}</A></td>
          <td><StatusBadge status={issue.status} /></td><td><RelativeTime iso={issue.first_seen} /></td><td><RelativeTime iso={issue.last_seen} /></td><td>{issue.times_seen}</td><td><StatsCell issue={issue} /></td>
        </tr>}</For></tbody></table></div>
      <Show when={shownIssues().length === 0}><p>No issues match these filters.</p></Show>
      <LoadMore onMore={() => void loadMore()} hasMore={nextCursor() !== null} />
    </Show>
  </section>;
}
