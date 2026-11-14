import { createEffect, For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import type { Issue } from "../../api/types";
import { LoadMore } from "../../components/LoadMore";
import { RelativeTime } from "../../components/RelativeTime";
import { Sparkline } from "../../components/Sparkline";
import { StatusBadge } from "../../components/StatusBadge";
import { EmptyState, ExposureBadge, Placeholder, PriorityBadge, Redacted, RiskPill, SeverityBar } from "../../components/ui";
import { sourceLabel } from "../../components/labels";

function severity(value: string): "critical" | "high" | "medium" {
  return value === "critical" || value === "high" ? value : "medium";
}

function initials(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map((part) => part.charAt(0).toUpperCase()).join("");
}

export function IssueListRows(props: Readonly<{
  issues: readonly Issue[];
  selected: ReadonlySet<string>;
  query: string;
  nextCursor: string | null;
  loading: boolean;
  onToggle(issue: Issue, index: number, event: MouseEvent): void;
  onToggleAll(checked: boolean): void;
  onLoadMore(): void;
}>): JSX.Element {
  return <Show when={!props.loading} fallback={<div class="issue-skeletons"><Placeholder label="Loading issue rows" /><Placeholder label="Loading issue rows" /><Placeholder label="Loading issue rows" /></div>}>
    <div class="issue-list-container">
      <div class="issue-table-head">
        <label class="issue-select"><input type="checkbox" aria-label="Select all loaded issues" ref={(element) => createEffect(() => { element.indeterminate = props.selected.size > 0 && props.selected.size < props.issues.length; })} checked={props.issues.length > 0 && props.selected.size === props.issues.length} onChange={(event) => props.onToggleAll(event.currentTarget.checked)} /></label>
        <span>Issue</span><span>Trend</span><span>Events</span><span>Values</span><span>Priority</span><span>Risk</span><span>Assignee</span><span>Last seen</span><span>Age</span>
      </div>
      <For each={props.issues}>{(issue, index) => {
        const priority = () => issue.priority ?? (issue.severity === "critical" || issue.severity === "high" ? "high" : "medium");
        const assignee = () => issue.assignee;
        return <article class="issue-row">
          <label class="issue-select"><input type="checkbox" aria-label={`Select ${issue.short_id}`} checked={props.selected.has(issue.id)} onClick={(event) => props.onToggle(issue, index(), event)} onChange={() => undefined} /></label>
          <div class="issue-summary">
            <A class="issue-row-link" href={`/issues/${encodeURIComponent(issue.id)}`} aria-label={`${issue.short_id} ${issue.display_title}`} />
            <div class="issue-title"><SeverityBar severity={severity(issue.severity)} /><strong>{issue.display_title}</strong></div>
            <div class="issue-locator"><Redacted text={issue.display_locator} ruleClass={issue.rule_class} /></div>
            <div class="issue-meta"><span>{issue.short_id}</span><span>{sourceLabel(issue.source)} · {issue.rule_class.replaceAll("_", " ")}</span><Show when={issue.exposure_visibility}>{(visibility) => <ExposureBadge visibility={visibility()} compact />}</Show><Show when={issue.status !== "unresolved"}><StatusBadge status={issue.status} resolution={issue.resolution} /></Show><Show when={(issue.merged_count ?? 0) > 0}><span class="merged-count">+{issue.merged_count} merged</span></Show></div>
          </div>
          <div class="issue-trend"><Sparkline values={[...(issue.trend ?? [])]} label={`${issue.short_id} trend`} /></div>
          <div class="issue-findings" title="Total findings">{issue.times_seen}</div>
          <div class="issue-values" title="Distinct values">{issue.distinct_fingerprints}</div>
          <div class="issue-priority"><span title={issue.base_severity && issue.base_severity !== issue.severity ? `Base severity: ${issue.base_severity}` : undefined}><PriorityBadge level={priority()} /></span></div>
          <div class="issue-risk">{issue.risk_score === undefined ? "—" : <RiskPill score={issue.risk_score} />}</div>
          <div class="issue-assignee"><Show when={assignee()} fallback={<span>Unassigned</span>}>{(value) => <span class="assignee-display"><span class="avatar" title={value().display_name} aria-label={value().display_name}>{initials(value().display_name)}</span><span>{value().display_name}</span></span>}</Show></div>
          <div class="issue-last-seen"><RelativeTime iso={issue.last_seen} /></div>
          <div class="issue-age"><RelativeTime iso={issue.first_seen} /></div>
        </article>;
      }}</For>
      <Show when={props.issues.length === 0}><Show when={props.query === "is:unresolved"} fallback={<EmptyState title="No issues match your search"><p>Check the filters and supported query syntax.</p></EmptyState>}><EmptyState title="Nothing exposed right now"><p>No unresolved issues match this feed.</p><A href="/collectors">Connect or review collectors</A></EmptyState></Show></Show>
    </div>
    <LoadMore onMore={props.onLoadMore} hasMore={props.nextCursor !== null} />
  </Show>;
}
