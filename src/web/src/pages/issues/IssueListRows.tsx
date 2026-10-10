import { createEffect, For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import type { Issue } from "../../api/types";
import { LoadMore } from "../../components/LoadMore";
import { RelativeTime } from "../../components/RelativeTime";
import { StatusBadge } from "../../components/StatusBadge";
import {
  EmptyState,
  ExposureBadge,
  Placeholder,
  PriorityBadge,
  Redacted,
  RiskPill,
  SeverityBar,
} from "../../components/ui";
import { sourceLabel } from "../../components/labels";

function severity(value: string): "critical" | "high" | "medium" {
  if (value === "critical" || value === "high") return value;
  return "medium";
}

export function IssueListRows(
  props: Readonly<{
    issues: readonly Issue[];
    selected: ReadonlySet<string>;
    query: string;
    nextCursor: string | null;
    loading: boolean;
    onToggle(issue: Issue, index: number, event: MouseEvent): void;
    onToggleAll(checked: boolean): void;
    onLoadMore(): void;
  }>,
): JSX.Element {
  return (
    <Show
      when={!props.loading}
      fallback={
        <div class="issue-skeletons">
          <Placeholder label="Loading issue rows" />
          <Placeholder label="Loading issue rows" />
          <Placeholder label="Loading issue rows" />
        </div>
      }
    >
      <div class="issue-list-container">
        <div class="issue-table-head">
          <label class="issue-select">
            <input
              type="checkbox"
              aria-label="Select all loaded issues"
              ref={(element) =>
                createEffect(() => {
                  element.indeterminate =
                    props.selected.size > 0 &&
                    props.selected.size < props.issues.length;
                })
              }
              checked={
                props.issues.length > 0 &&
                props.selected.size === props.issues.length
              }
              onChange={(event) =>
                props.onToggleAll(event.currentTarget.checked)
              }
            />
          </label>
          <span>Issue</span>
          <span>Last seen</span>
          <span>Age</span>
          <span>Findings</span>
          <span>Risk</span>
          <span>Priority</span>
          <span>Assignee</span>
        </div>
        <For each={props.issues}>
          {(issue, index) => (
            <article class="issue-row">
              <label class="issue-select">
                <input
                  type="checkbox"
                  aria-label={`Select ${issue.short_id}`}
                  checked={props.selected.has(issue.id)}
                  onClick={(event) => props.onToggle(issue, index(), event)}
                  onChange={() => undefined}
                />
              </label>
              <div class="issue-summary">
                <A
                  class="issue-row-link"
                  href={`/issues/${encodeURIComponent(issue.id)}`}
                  aria-label={`${issue.short_id} ${issue.display_title}`}
                />
                <div class="issue-title">
                  <SeverityBar severity={severity(issue.severity)} />
                  <strong>
                    {issue.rule_class.replaceAll("_", " ")}:{" "}
                    {issue.display_title}
                  </strong>
                </div>
                <div class="issue-locator">
                  <Redacted
                    text={issue.display_locator}
                    ruleClass={issue.rule_class}
                  />
                </div>
                <div class="issue-meta">
                  <span>{issue.short_id}</span>
                  <span>
                    {sourceLabel(issue.source)} ·{" "}
                    {issue.display_locator.split(/[/:]/).slice(-1)[0]}
                  </span>
                  <Show when={issue.exposure_visibility}>
                    {(visibility) => (
                      <ExposureBadge visibility={visibility()} compact />
                    )}
                  </Show>
                  <span title="Scan coverage">
                    {issue.sweep_covered ? "Scanned" : "Unscanned"}
                  </span>
                  <Show when={issue.status !== "unresolved"}>
                    <StatusBadge
                      status={issue.status}
                      resolution={issue.resolution}
                    />
                  </Show>
                </div>
              </div>
              <div class="issue-last-seen">
                <RelativeTime iso={issue.last_seen} />
              </div>
              <div class="issue-age">
                <RelativeTime iso={issue.first_seen} />
              </div>
              <div class="issue-findings">
                <span title="Since first seen">{issue.times_seen}</span>
              </div>
              <div class="issue-risk">
                {issue.risk_score === undefined ? "—" : <RiskPill score={issue.risk_score} />}
              </div>
              <div class="issue-priority">
                <span title={issue.base_severity && issue.base_severity !== issue.severity ? `Base severity: ${issue.base_severity}` : undefined}>
                  <PriorityBadge level={severity(issue.severity)} />
                </span>
              </div>
              <div class="issue-assignee">
                {issue.assignee_member_id ??
                  issue.assignee_team_id ??
                  "Unassigned"}
              </div>
            </article>
          )}
        </For>
        <Show when={props.issues.length === 0}>
          <Show
            when={props.query === "is:unresolved"}
            fallback={
              <EmptyState title="No issues match your search">
                <p>Check the filters and supported query syntax.</p>
              </EmptyState>
            }
          >
            <EmptyState title="Nothing exposed right now">
              <p>No unresolved issues match this feed.</p>
              <A href="/collectors">Connect or review collectors</A>
            </EmptyState>
          </Show>
        </Show>
      </div>
      <LoadMore onMore={props.onLoadMore} hasMore={props.nextCursor !== null} />
    </Show>
  );
}
