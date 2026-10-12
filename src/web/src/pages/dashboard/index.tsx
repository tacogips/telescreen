import { A } from "@solidjs/router";
import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { apiFetch, dashboardApi } from "../../api/client";
import type { DashboardResponse } from "../../api/types";
import { PeriodPicker, type Period } from "../../components/PeriodPicker";
import { TopBar } from "../../components/shell/TopBar";
import { EmptyState, Panel, Table } from "../../components/ui";
import { OnboardingChecklist } from "./OnboardingChecklist";
import "./dashboard.css";

type CountBucket = Readonly<{ start: string; critical: number; high: number; medium: number; new_issues: number; resolved_issues: number }>;
type CountGroup = Readonly<{ key: string; count: number }>;
type TopIssue = Readonly<{ id: string; short_id: string; display_title: string; findings: number }>;
type DashboardSelection = Readonly<{ period: Period; start?: string; end?: string }>;
type DashboardView = Readonly<{
  period: string; open_issues: number; findings: number; buckets: readonly CountBucket[];
  open_by_exposure: readonly CountGroup[]; open_by_source: readonly CountGroup[];
  archived_count: number; escalating_count: number; unassigned_high_priority: number;
  mttr_seconds: number | null; mtta_seconds: number | null; top_issues: readonly TopIssue[];
}>;

function countGroups(value: unknown): CountGroup[] {
  if (Array.isArray(value)) return value.flatMap((item): CountGroup[] => {
    if (Array.isArray(item) && typeof item[0] === "string" && typeof item[1] === "number") return [{ key: item[0], count: item[1] }];
    if (typeof item !== "object" || item === null || Array.isArray(item)) return [];
    const group = item as Readonly<Record<string, unknown>>;
    const key = [group.key, group.label, group.exposure, group.visibility, group.source, group.category].find((candidate): candidate is string => typeof candidate === "string");
    const count = [group.count, group.issues, group.issue_count].find((candidate): candidate is number => typeof candidate === "number");
    return key === undefined || count === undefined ? [] : [{ key, count }];
  });
  if (typeof value === "object" && value !== null) return Object.entries(value).flatMap(([key, count]): CountGroup[] => typeof count === "number" ? [{ key, count }] : []);
  return [];
}

function dashboardView(value: DashboardResponse): DashboardView {
  const expanded = value as DashboardResponse & Partial<DashboardView>;
  const raw = value as unknown as Readonly<Record<string, unknown>>;
  return {
    period: expanded.period, open_issues: expanded.open_issues ?? 0, findings: expanded.findings ?? 0,
    buckets: expanded.buckets ?? [], open_by_exposure: countGroups(raw.open_by_exposure), open_by_source: countGroups(raw.open_by_source),
    archived_count: expanded.archived_count ?? 0, escalating_count: expanded.escalating_count ?? 0,
    unassigned_high_priority: expanded.unassigned_high_priority ?? 0, mttr_seconds: expanded.mttr_seconds ?? null,
    mtta_seconds: expanded.mtta_seconds ?? null, top_issues: expanded.top_issues ?? [],
  };
}

function hours(value: number | null): string { return value === null ? "-" : (value / 3600).toFixed(1); }

function wasOnboardingDismissed(): boolean { return typeof localStorage !== "undefined" && localStorage.getItem("telescreen.onboarding.dismissed") === "true"; }

function TrendChart(props: Readonly<{ buckets: readonly CountBucket[] }>): JSX.Element {
  const max = Math.max(1, ...props.buckets.flatMap((bucket) => [bucket.critical + bucket.high + bucket.medium, bucket.new_issues, bucket.resolved_issues]));
  const width = Math.max(1, props.buckets.length) * 22;
  return <svg class="dashboard-chart" role="img" aria-label="Issue severity and new versus resolved trend" viewBox={`0 0 ${width} 132`}>
    <For each={props.buckets}>{(bucket, index) => {
      const x = index() * 22;
      const critical = bucket.critical / max * 90;
      const high = bucket.high / max * 90;
      const medium = bucket.medium / max * 90;
      const newHeight = bucket.new_issues / max * 90;
      const resolvedHeight = bucket.resolved_issues / max * 90;
      return <g><rect x={x} y={110 - medium} width="8" height={medium} fill="var(--warning)" />
        <rect x={x} y={110 - medium - high} width="8" height={high} fill="var(--danger)" />
        <rect x={x} y={110 - medium - high - critical} width="8" height={critical} fill="var(--danger-strong)" />
        <rect x={x + 10} y={110 - newHeight} width="4" height={newHeight} fill="var(--chart-matching)" />
        <rect x={x + 16} y={110 - resolvedHeight} width="4" height={resolvedHeight} fill="var(--success)" /></g>;
    }}</For>
  </svg>;
}

function GroupBars(props: Readonly<{ title: string; groups: readonly CountGroup[] }>): JSX.Element {
  const max = Math.max(1, ...props.groups.map((item) => item.count));
  return <Panel title={props.title}><Show when={props.groups.length > 0} fallback={<p>No open issues.</p>}>
    <ul class="dashboard-bars"><For each={props.groups}>{(item) => <li><span>{item.key}</span><span class="dashboard-bar-track"><span style={{ width: `${item.count / max * 100}%` }} /></span><strong>{item.count}</strong></li>}</For></ul>
  </Show></Panel>;
}

/** Overview metrics and onboarding checklist. */
export default function DashboardPage(): JSX.Element {
  const [selection, setSelection] = createSignal<DashboardSelection>({ period: "14d" });
  const [dashboard] = createResource(selection, async (selected): Promise<DashboardView> => {
    if (selected.period === "custom") {
      if (selected.start === undefined || selected.end === undefined) return dashboardView(await dashboardApi.get("14d"));
      const query = new URLSearchParams({ period: "custom", start: selected.start, end: selected.end });
      return dashboardView(await apiFetch<DashboardResponse>(`/api/dashboard?${query.toString()}`));
    }
    return dashboardView(await dashboardApi.get(selected.period));
  });
  const changePeriod = (period: Period, start?: string, end?: string): void => {
    if (period !== "custom") { setSelection({ period }); return; }
    const current = selection();
    setSelection({ period, ...(start === undefined ? current.start === undefined ? {} : { start: current.start } : { start }), ...(end === undefined ? current.end === undefined ? {} : { end: current.end } : { end }) });
  };
  const [showOnboarding, setShowOnboarding] = createSignal(!wasOnboardingDismissed());
  const dismiss = (): void => { if (typeof localStorage !== "undefined") localStorage.setItem("telescreen.onboarding.dismissed", "true"); setShowOnboarding(false); };
  return <section class="page-content dashboard-page">
    <TopBar.Slot name="title">Dashboard</TopBar.Slot>
    <TopBar.Slot name="actions"><button class="button-secondary" onClick={() => setShowOnboarding(true)}>Getting started</button></TopBar.Slot>
    <PeriodPicker value={selection().period} onChange={changePeriod} />
    <Show when={dashboard.error === undefined} fallback={<p role="alert">Dashboard could not be loaded.</p>}>
      <Show when={!dashboard.loading} fallback={<p>Loading dashboard…</p>}>
        <div class="dashboard-tiles">
          <A class="dashboard-tile" href="/issues?query=is:unresolved"><span>Open issues</span><strong>{dashboard()?.open_issues ?? 0}</strong></A>
          <A class="dashboard-tile" href="/issues?query=is:unresolved assigned:none priority:high"><span>Unassigned high priority</span><strong>{dashboard()?.unassigned_high_priority ?? 0}</strong></A>
          <A class="dashboard-tile" href="/issues?query=is:archived"><span>Archived</span><strong>{dashboard()?.archived_count ?? 0}</strong></A>
          <A class="dashboard-tile" href="/issues?query=is:escalating"><span>Escalating</span><strong>{dashboard()?.escalating_count ?? 0}</strong></A>
          <A class="dashboard-tile" href="/issues?query=is:resolved"><span>MTTR (hours)</span><strong>{hours(dashboard()?.mttr_seconds ?? null)}</strong></A>
          <A class="dashboard-tile" href="/issues?query=is:unresolved"><span>MTTA (hours)</span><strong>{hours(dashboard()?.mtta_seconds ?? null)}</strong></A>
        </div>
        <Panel title="Issue trends"><div class="dashboard-legend"><span>Severity</span><span>New</span><span>Resolved</span></div><TrendChart buckets={dashboard()?.buckets ?? []} /></Panel>
        <div class="dashboard-group-grid"><GroupBars title="Open by exposure" groups={dashboard()?.open_by_exposure ?? []} /><GroupBars title="Open by source" groups={dashboard()?.open_by_source ?? []} /></div>
        <Panel title="Top issues"><Show when={(dashboard()?.top_issues.length ?? 0) > 0} fallback={<EmptyState title="No top issues"><p>No issues were reported in this period.</p></EmptyState>}>
          <Table><thead><tr><th>Issue</th><th>Findings</th></tr></thead><tbody><For each={dashboard()?.top_issues}>{(issue) => <tr><td><A href={`/issues/${encodeURIComponent(issue.id)}`}>{issue.short_id} · {issue.display_title}</A></td><td>{issue.findings}</td></tr>}</For></tbody></Table>
        </Show></Panel>
      </Show>
    </Show>
    <Show when={showOnboarding()}><OnboardingChecklist onDismiss={dismiss} /></Show>
  </section>;
}
