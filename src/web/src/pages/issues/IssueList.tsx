import { createEffect, createResource, createSignal, onCleanup, onMount, Show, type JSX } from "solid-js";
import { useSearchParams } from "@solidjs/router";
import { ApiError, directoryApi, issuesApi, viewsApi, workflowsApi } from "../../api/client";
import type { BulkParams, BulkResult, CountsResponse, Issue, IssuePriority, IssueStatus, Member, Page, SavedView, StreamQuery, StreamTab, Team, WorkflowVersionRef } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { showToast, Tag } from "../../components/ui";
import { registerShortcuts } from "../../components/shell/shortcuts";
import type { Period } from "../../components/PeriodPicker";
import { IssueBulkActions } from "./IssueBulkActions";
import type { ArchiveRequest } from "./ArchiveMenu";
import { IssueListControls } from "./IssueListControls";
import { IssueListRows } from "./IssueListRows";
import { clearLegacyIssueViews, readLegacyIssueViews } from "./savedViews";
import { issueQueryTokens } from "./query";
import { Sparkline } from "../../components/Sparkline";
import "./issues.css";

type BulkData = Readonly<{ members: Member[]; teams: Team[]; workflows: WorkflowVersionRef[] }>;
type Sort = NonNullable<StreamQuery["sort"]>;
type Trend = "24h" | "14d";
const DEFAULT_QUERY = "is:unresolved";

function errorValue(value: unknown): ApiError {
  return value instanceof ApiError ? value : new ApiError(0, "request_failed", "The request could not be completed.");
}
function paramValue(value: string | string[] | undefined, fallback: string): string {
  return typeof value === "string" ? value : value?.[0] ?? fallback;
}
function tabFromQuery(query: string): StreamTab {
  const token = issueQueryTokens(query).find((item) => item.key === "is" || item.key === "status");
  const value = token?.value;
  return value === "for_review" || value === "regressed" || value === "escalating" || value === "archived" || value === "resolved" ? value : "unresolved";
}
function withTab(query: string, tab: StreamTab): string {
  const next = query.replace(/!?is:[^\s]+|!?status:[^\s]+/g, "").trim();
  return `${next} is:${tab}`.trim();
}
function statusOptions(issues: readonly Issue[]): IssueStatus[] {
  const candidates: readonly IssueStatus[] = ["acknowledged", "in_progress", "unresolved"];
  return candidates.filter((status) => issues.length > 0 && issues.every((issue) => status === "unresolved" ? ["resolved", "ignored", "allowed", "pending_verification", "archived"].includes(issue.status) : issue.status !== status && issue.status !== "resolved" && issue.status !== "ignored" && issue.status !== "allowed"));
}

export function IssueList(): JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = (): string => paramValue(searchParams.query, DEFAULT_QUERY);
  const sort = (): Sort => paramValue(searchParams.sort, "last_seen") as Sort;
  const period = (): Period => paramValue(searchParams.period, "all") as Period;
  const trend = (): Trend => paramValue(searchParams.trend, "14d") as Trend;
  const [startDate, setStartDate] = createSignal(paramValue(searchParams.start, ""));
  const [endDate, setEndDate] = createSignal(paramValue(searchParams.end, ""));
  const start = (): string => startDate();
  const end = (): string => endDate();
  const [error, setError] = createSignal<ApiError | null>(null);
  const [additionalIssues, setAdditionalIssues] = createSignal<Issue[]>([]);
  const [nextCursor, setNextCursor] = createSignal<string | null>(null);
  const [selected, setSelected] = createSignal<Set<string>>(new Set());
  const [allMatching, setAllMatching] = createSignal(false);
  const [activeViewId, setActiveViewId] = createSignal<string | null>(null);
  const [updatedSince, setUpdatedSince] = createSignal<string | undefined>(undefined);
  const [lastCheckedIndex, setLastCheckedIndex] = createSignal<number | null>(null);
  const [activeIndex, setActiveIndex] = createSignal(0);
  const [importComplete, setImportComplete] = createSignal(false);
  const [bulkData] = createResource(async (): Promise<BulkData> => {
    const [members, teams, workflows] = await Promise.all([directoryApi.members(), directoryApi.teams(), workflowsApi.list()]);
    return { members: members.items, teams: teams.items, workflows: workflows.items.filter((workflow) => workflow.active) };
  });
  const [views, { refetch: refetchViews }] = createResource(async (): Promise<SavedView[]> => (await viewsApi.list()).items);
  const listRequest = (): StreamQuery => ({ query: query(), period: period(), ...(period() === "custom" && start() ? { start: start() } : {}), ...(period() === "custom" && end() ? { end: end() } : {}), trend: trend(), sort: sort() });
  const listKey = (): string => JSON.stringify(listRequest());
  const [resource, { refetch }] = createResource(listKey, async (): Promise<Page<Issue>> => {
    setError(null);
    try {
      const request = listRequest();
      return await issuesApi.listQuery(request);
    } catch (caught) {
      setError(errorValue(caught));
      return { items: [], next_cursor: null };
    }
  });
  const countKey = (): string => `${query()}\n${period()}\n${updatedSince() ?? ""}`;
  const [counts, { refetch: refetchCounts }] = createResource(countKey, async (): Promise<CountsResponse> => {
    try { const request = { query: query(), period: period(), ...(period() === "custom" && start() ? { start: start() } : {}), ...(period() === "custom" && end() ? { end: end() } : {}), ...(updatedSince() ? { updated_since: updatedSince() } : {}) }; return await issuesApi.counts(request); }
    catch (caught) { setError(errorValue(caught)); return { tabs: [], updated_since_count: 0 }; }
  });
  createEffect(() => setNextCursor(resource()?.next_cursor ?? null));
  const allIssues = (): Issue[] => [...(resource()?.items ?? []), ...additionalIssues()];
  const selectedIssues = (): Issue[] => allIssues().filter((issue) => selected().has(issue.id));
  const activeCounts = (): Partial<Record<StreamTab, number>> => Object.fromEntries((counts()?.tabs ?? []).map((item) => [item.tab, item.count]));
  const matchingCount = (): number => activeCounts()[tabFromQuery(query())] ?? allIssues().length;
  const setParams = (patch: Readonly<Record<string, string | undefined>>): void => {
    setAdditionalIssues([]); setSelected(new Set<string>()); setAllMatching(false);
    setSearchParams(patch);
  };
  const setQuery = (value: string): void => setParams({ query: value || DEFAULT_QUERY });
  const setSort = (value: string): void => setParams({ sort: value });
  const setPeriod = (value: Period, nextStart?: string, nextEnd?: string): void => {
    const updates: Record<string, string | undefined> = { period: value };
    if (nextStart !== undefined) { setStartDate(nextStart); updates.start = nextStart; }
    if (nextEnd !== undefined) { setEndDate(nextEnd); updates.end = nextEnd; }
    if (value !== "custom") { setStartDate(""); setEndDate(""); updates.start = undefined; updates.end = undefined; }
    setParams(updates);
  };
  const setTrend = (value: Trend): void => setParams({ trend: value });
  const setTab = (tab: StreamTab): void => setQuery(withTab(query(), tab));
  const currentView = (): SavedView | undefined => views()?.find((view) => view.id === activeViewId());
  const dirty = (): boolean => { const active = currentView(); return active !== undefined && (active.query !== query() || active.sort !== sort()); };
  const refresh = (): void => { setAdditionalIssues([]); setError(null); void refetch(); void refetchCounts(); };
  const loadMore = async (): Promise<void> => {
    const cursor = nextCursor(); if (cursor === null) return;
    try { const next = await issuesApi.listQuery({ ...listRequest(), cursor }); setAdditionalIssues((prior) => [...prior, ...next.items]); setNextCursor(next.next_cursor); }
    catch (caught) { setError(errorValue(caught)); }
  };
  const toggleIssue = (issue: Issue, index: number, event: MouseEvent): void => {
    setAllMatching(false);
    setSelected((prior) => { const next = new Set(prior); const anchor = lastCheckedIndex(); if (event.shiftKey && anchor !== null) { for (let position = Math.min(anchor, index); position <= Math.max(anchor, index); position += 1) { const row = allIssues()[position]; if (row !== undefined) next.add(row.id); } } else if (next.has(issue.id)) next.delete(issue.id); else next.add(issue.id); return next; });
    setLastCheckedIndex(index);
  };
  const toggleAll = (checked: boolean): void => { setAllMatching(false); setSelected(checked ? new Set<string>(allIssues().map((issue) => issue.id)) : new Set<string>()); };
  const showBulkResult = (result: BulkResult): void => {
    const failedIds = new Set(result.failed.map((item) => item.issue_id));
    const failedLabels = result.failed.map((item) => allIssues().find((issue) => issue.id === item.issue_id)?.short_id ?? item.issue_id.slice(0, 8));
    showToast(failedLabels.length > 0 ? `Failed: ${failedLabels.join(", ")}` : `${result.succeeded.length} issues updated${result.truncated ? " (first 1000 matching)" : ""}`, failedIds.size > 0 ? "warning" : "success");
  };
  const runBulk = async (action: string, params?: BulkParams): Promise<void> => {
    try {
      const result = allMatching() ? await issuesApi.bulk({ query: query(), period: period(), ...(period() === "custom" && start() ? { start: start() } : {}), ...(period() === "custom" && end() ? { end: end() } : {}), action, params }) : await issuesApi.bulk({ ids: selectedIssues().map((issue) => issue.id), action, params });
      showBulkResult(result); setSelected(new Set<string>()); setAllMatching(false); refresh();
    } catch (caught) { showToast(errorValue(caught).code, "warning"); }
  };
  const runSelected = async (label: string, action: (issue: Issue) => Promise<unknown>): Promise<void> => {
    const failures: string[] = [];
    await Promise.all(selectedIssues().map(async (issue) => { try { await action(issue); } catch { failures.push(issue.short_id); } }));
    showToast(failures.length > 0 ? `Failed: ${failures.join(", ")}` : label, failures.length > 0 ? "warning" : "success");
    setSelected(new Set<string>()); setAllMatching(false); refresh();
  };
  const saveView = async (): Promise<void> => {
    const name = globalThis.window.prompt("Name this issue view"); if (name === null || name.trim() === "") return;
    try { const created = await viewsApi.create({ name: name.trim(), query: query(), sort: sort(), visibility: "personal" }); setActiveViewId(created.id); await refetchViews(); showToast("Saved issue view", "success"); }
    catch (caught) { showToast(errorValue(caught).code, "warning"); }
  };
  const applyView = (view: SavedView): void => { setActiveViewId(view.id); setParams({ query: view.query, sort: view.sort }); };
  const removeView = async (view: SavedView): Promise<void> => { if (view.visibility !== "personal") return; try { await viewsApi.delete(view.id); if (activeViewId() === view.id) setActiveViewId(null); await refetchViews(); } catch (caught) { showToast(errorValue(caught).code, "warning"); } };
  const starView = async (view: SavedView): Promise<void> => { try { if (view.starred) await viewsApi.unstar(view.id); else await viewsApi.star(view.id); await refetchViews(); } catch (caught) { showToast(errorValue(caught).code, "warning"); } };
  const defaultView = async (view: SavedView): Promise<void> => { try { await viewsApi.setDefault(view.id); await refetchViews(); } catch (caught) { showToast(errorValue(caught).code, "warning"); } };
  const resetView = (): void => { const active = currentView(); setParams({ query: active?.query ?? DEFAULT_QUERY, sort: active?.sort ?? "last_seen" }); };
  const importLegacyViews = async (): Promise<void> => {
    if (importComplete() || views.loading) return;
    setImportComplete(true);
    const legacy = readLegacyIssueViews(); if (legacy.length === 0) return;
    try {
      for (const view of legacy) { const created = await viewsApi.create({ name: view.name, query: view.query, sort: view.sort, visibility: "personal" }); if (view.starred) await viewsApi.star(created.id); }
      clearLegacyIssueViews(); await refetchViews(); showToast(`Imported ${legacy.length} saved views`, "success");
    } catch { showToast("Some saved views could not be imported; try again later", "warning"); }
  };
  onMount(() => {
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") { setUpdatedSince(new Date().toISOString()); void refetchCounts(); } }, 30000);
    onCleanup(() => window.clearInterval(interval));
    const stop = registerShortcuts("stream", [
      { key: "j", handler: () => { const links = [...document.querySelectorAll<HTMLAnchorElement>(".issue-row-link")]; const next = Math.min(activeIndex() + 1, links.length - 1); setActiveIndex(next); links[next]?.focus(); } },
      { key: "k", handler: () => { const links = [...document.querySelectorAll<HTMLAnchorElement>(".issue-row-link")]; const next = Math.max(0, activeIndex() - 1); setActiveIndex(next); links[next]?.focus(); } },
      { key: "o", handler: () => { const links = [...document.querySelectorAll<HTMLAnchorElement>(".issue-row-link")]; links[activeIndex()]?.click(); } },
      { key: "Enter", handler: () => { if (document.activeElement?.classList.contains("issue-row-link")) (document.activeElement as HTMLAnchorElement).click(); } },
      { key: "x", handler: () => { const row = allIssues()[activeIndex()]; if (row !== undefined) toggleIssue(row, activeIndex(), new MouseEvent("click")); } },
      { key: "r", handler: () => document.querySelector<HTMLButtonElement>(".bulk-action-bar [aria-label='Resolve selected issues']")?.click() },
      { key: "a", handler: () => document.querySelector<HTMLSelectElement>(".bulk-action-bar [aria-label='Assign to']")?.focus() },
      { key: "e", handler: () => document.querySelector<HTMLElement>(".archive-menu summary")?.click() },
      { key: "/", handler: () => document.querySelector<HTMLInputElement>("input[aria-label='Search query']")?.focus() },
    ]);
    onCleanup(stop);
  });
  createEffect(() => { if (!views.loading) void importLegacyViews(); });
  createEffect(() => {
    if (searchParams.query === undefined) {
      if (views.loading) return;
      const defaultView = views()?.find((view) => view.is_default);
      setSearchParams({ query: defaultView?.query ?? DEFAULT_QUERY, sort: defaultView?.sort ?? sort(), period: period(), trend: trend() }, { replace: true });
    } else if (searchParams.sort === undefined || searchParams.period === undefined || searchParams.trend === undefined) {
      setSearchParams({ query: query(), sort: sort(), period: period(), trend: trend() }, { replace: true });
    }
  });
  const bulk = (): BulkData => bulkData() ?? { members: [], teams: [], workflows: [] };
  const countsValue = (): CountsResponse => counts() ?? { tabs: [], updated_since_count: 0 };
  const archiveSelected = (request: ArchiveRequest): void => {
    if (allMatching()) { void runBulk("archive", { archive: request }); return; }
    void runSelected("Archived selected issues", (issue) => issuesApi.archive(issue.id, request));
  };
  const assign = (value: string): void => {
    if (!value) return;
    const assigneeRef = value === "__unassigned__" ? null : value;
    if (allMatching()) void runBulk("assign", { assignee_ref: assigneeRef });
    else void runSelected("Assigned selected issues", (issue) => issuesApi.update(issue.id, { assignee_ref: assigneeRef }));
  };
  const priority = (value: IssuePriority): void => { if (allMatching()) void runBulk("priority", { priority: value }); else void runSelected("Updated priority", (issue) => issuesApi.setPriority(issue.id, value)); };
  const markReviewed = (): void => { void runBulk("mark_reviewed"); };
  const resolve = (reason: "false_positive" | "manual_unverified", note: string): void => {
    if (allMatching()) { void runBulk("resolve", { reason, note }); return; }
    void runSelected("Resolved selected issues", (issue) => issuesApi.resolve(issue.id, { reason, note }));
  };
  const merge = (primaryId: string): void => {
    if (allMatching()) { showToast("Merge requires a finite selected set", "warning"); return; }
    const children = selectedIssues().filter((issue) => issue.id !== primaryId).map((issue) => issue.id);
    if (children.length === 0) { showToast("Select at least two issues to merge", "warning"); return; }
    void issuesApi.merge({ primary_id: primaryId, child_ids: children }).then(() => { showToast("Merged selected issues", "success"); setSelected(new Set<string>()); refresh(); }).catch((caught: unknown) => showToast(errorValue(caught).code, "warning"));
  };
  const startWorkflow = (name: string): void => {
    if (!name) return;
    if (allMatching()) { showToast("Start workflow requires a finite selected set", "warning"); return; }
    void runSelected("Started workflow", (issue) => workflowsApi.startRun(name, issue.id));
  };
  const remediate = (): void => {
    if (allMatching()) { void runBulk("request_verification", { verification: { action: "remove", include_linked: false } }); return; }
    void runSelected("Marked remediated", (issue) => issuesApi.remediation(issue.id, { action: "remove", include_linked: false }));
  };
  const currentIssue = (): Issue[] => allMatching() ? allIssues() : selectedIssues();
  return <section class="page-content issue-page">
    <IssueListControls query={query()} sort={sort()} period={period()} start={start()} end={end()} trend={trend()} tab={tabFromQuery(query())} counts={activeCounts()} members={bulk().members} teams={bulk().teams} views={views() ?? []} activeViewId={activeViewId()} dirty={dirty()} onQuery={setQuery} onSort={setSort} onPeriod={setPeriod} onTrend={setTrend} onTab={setTab} onSave={() => void saveView()} onReset={resetView} onApply={applyView} onRemove={(view) => void removeView(view)} onStar={(view) => void starView(view)} onDefault={(view) => void defaultView(view)} />
    <Show when={error()} keyed>{(value) => <ErrorBanner error={value} />}</Show>
    <Show when={countsValue().updated_since_count > 0}><button type="button" class="issue-refresh-banner" onClick={() => { setUpdatedSince(undefined); refresh(); }}><strong>{countsValue().updated_since_count} new or updated issues</strong><span>Refresh list</span></button></Show>
    <section class="issues-trend" aria-label="Issue findings trend"><strong>Findings, last 14 days (all issues)</strong><Sparkline values={allIssues().flatMap((issue) => [...(issue.trend ?? [])])} label="Findings, issue stream" /></section>
    <IssueListRows issues={allIssues()} selected={selected()} query={query()} nextCursor={nextCursor()} loading={resource.loading} onToggle={toggleIssue} onToggleAll={toggleAll} onLoadMore={() => void loadMore()} />
    <Show when={selected().size > 0 || allMatching()}><IssueBulkActions selectedCount={allMatching() ? matchingCount() : selected().size} matchingCount={matchingCount()} allMatching={allMatching()} query={query()} period={period()} issues={currentIssue()} statuses={statusOptions(currentIssue())} members={bulk().members} teams={bulk().teams} workflows={bulk().workflows} onStatus={(status) => void runBulk("status", { status })} onResolve={resolve} onArchive={archiveSelected} onAssign={assign} onPriority={priority} onReview={markReviewed} onMerge={merge} onSelectAllMatching={() => { setSelected(new Set<string>()); setAllMatching(true); }} onRemediate={remediate} onStartWorkflow={startWorkflow} onClear={() => { setSelected(new Set<string>()); setAllMatching(false); }} /></Show>
    <Show when={(views()?.filter((view) => view.starred).length ?? 0) > 0}><Tag variant="muted">{views()?.filter((view) => view.starred).length ?? 0} starred views</Tag></Show>
  </section>;
}
