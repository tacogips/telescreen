import { For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import {
  Ban,
  CircleCheck,
  CircleHelp,
  CircleX,
  ClipboardList,
  Clock3,
  Eye,
  RefreshCw,
  ScanSearch,
} from "lucide-solid";
import type { IssueEvent } from "../../../api/types";
import { PeriodPicker, type Period } from "../../../components/PeriodPicker";
import { StatusBadge } from "../../../components/StatusBadge";
import { RelativeTime } from "../../../components/RelativeTime";
import {
  Button,
  ExposureBadge,
  FoldSection,
  MiniBarChart,
  Redacted,
  Tag,
  Timeline,
} from "../../../components/ui";
import { historyLabel, type DetailData } from "./types";

function jumpTo(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
}

function historyIcon(kind: string): JSX.Element {
  if (kind === "exposure_changed") return <Eye size={14} aria-hidden="true" />;
  if (kind === "policy_reevaluated") return <RefreshCw size={14} aria-hidden="true" />;
  if (kind === "verification_requested") return <ScanSearch size={14} aria-hidden="true" />;
  if (kind === "verification_failed" || kind === "exception_rejected")
    return <CircleX size={14} aria-hidden="true" />;
  if (kind === "verification_inconclusive") return <CircleHelp size={14} aria-hidden="true" />;
  if (kind === "exception_requested") return <ClipboardList size={14} aria-hidden="true" />;
  if (kind === "exception_approved") return <CircleCheck size={14} aria-hidden="true" />;
  if (kind === "exception_revoked") return <Ban size={14} aria-hidden="true" />;
  if (kind === "exception_expired") return <Clock3 size={14} aria-hidden="true" />;
  return <span aria-hidden="true">•</span>;
}

export function IssueDetailSections(
  props: Readonly<{
    detail: DetailData;
    selectedEvent: IssueEvent | undefined;
    selection: DetailData["selection"];
    activeTab: string;
    period: string;
    onPeriod(value: string): void;
    onNavigate(selector: string): void;
    onUnmerge(ids?: readonly string[]): void;
    onUnmergeChild(primaryId: string, childId: string): void;
    onDeleteExternal(linkId: string): void;
    onUpload(event: Event & { currentTarget: HTMLInputElement }): void;
    onActiveTab(tab: string): void;
  }>,
): JSX.Element {
  const location = (): string =>
    props.selectedEvent?.location ?? props.detail.issue.display_locator;
  const frames = (): string[] =>
    location()
      .split(/\s*>\s*|\/|:/)
      .filter(Boolean);
  const credential = (): boolean =>
    ["credential", "token", "private_key", "password"].includes(
      props.detail.issue.rule_class,
    );
  return (
    <div class="issue-detail-main">
      <section class="finding-chart-block">
        <h3>Findings over time</h3>
        <PeriodPicker value={props.period as Period} onChange={(value) => props.onPeriod(value)} />
        <MiniBarChart
          values={props.detail.stats.buckets.map((bucket) => bucket.count)}
          label="Findings over selected period"
          height={48}
        />
        <div class="finding-tags">
          <For each={props.detail.tags.items}>
            {(item) => (
              <Tag variant={item.key === "detector" ? "muted" : "info"}>
                {item.key}: {item.value} · {item.percent.toFixed(1)}%
              </Tag>
            )}
          </For>
        </div>
      </section>
      <nav class="finding-navigation" aria-label="Finding navigation">
        <div role="tablist">
          <For each={["Findings", "Evidence", "Runs"]}>
            {(tab) => (
              <button
                role="tab"
                aria-selected={props.activeTab === tab}
                onClick={() => {
                  props.onActiveTab(tab);
                  jumpTo(tab.toLowerCase());
                }}
              >
                {tab}
              </button>
            )}
          </For>
        </div>
        <div class="finding-nav-controls">
          <Button
            size="xs"
            onClick={() => props.onNavigate("recommended")}
          >
            Recommended
          </Button>
          <Button size="xs" onClick={() => props.onNavigate("oldest")}>Oldest</Button>
          <Button
            size="xs"
            onClick={() => props.onNavigate("latest")}
          >
            Latest
          </Button>
          <Button size="xs" onClick={() => { const id = props.selection?.previous_id; if (id !== null && id !== undefined) props.onNavigate(id); }}>
            Previous
          </Button>
          <Button size="xs" onClick={() => { const id = props.selection?.next_id; if (id !== null && id !== undefined) props.onNavigate(id); }}>
            Next
          </Button>
          <span aria-live="polite">{props.selection === null ? "0 of 0" : `${props.selection.position} of ${props.selection.total}`}</span>
        </div>
      </nav>
      <nav class="issue-jump-links" aria-label="Jump to">
        <For
          each={[
            "Highlights",
            "Location chain",
            "Timeline",
            "Evidence",
            "Detection details",
          ]}
        >
          {(label) => (
            <Button
              size="xs"
              onClick={() => jumpTo(label.toLowerCase().replaceAll(" ", "-"))}
            >
              {label}
            </Button>
          )}
        </For>
      </nav>
      <FoldSection id="highlights" title="Highlights">
        <dl class="issue-detail-grid">
          <dt>Rule class</dt>
          <dd>{props.detail.issue.rule_class}</dd>
          <dt>Rule id</dt>
          <dd>{props.selectedEvent?.rule_id ?? "Unavailable"}</dd>
          <dt>Detector</dt>
          <dd>{props.selectedEvent?.detector ?? "Unavailable"}</dd>
          <dt>Confidence</dt>
          <dd>{props.selectedEvent?.confidence ?? "Unknown"}</dd>
          <dt>Severity</dt>
          <dd>
            {props.detail.issue.severity}
            {props.detail.issue.base_severity && props.detail.issue.base_severity !== props.detail.issue.severity
              ? ` (base ${props.detail.issue.base_severity})`
              : ""}
          </dd>
          <dt>Location</dt>
          <dd>
            <Redacted
              text={props.detail.issue.display_locator}
              ruleClass={props.detail.issue.rule_class}
            />
          </dd>
          <dt>First seen</dt>
          <dd>
            <RelativeTime iso={props.detail.issue.first_seen} />
          </dd>
          <dt>Last seen</dt>
          <dd>
            <RelativeTime iso={props.detail.issue.last_seen} />
          </dd>
        </dl>
      </FoldSection>
      <FoldSection id="location-chain" title="Location chain">
        <ol class="location-chain">
          <For each={frames()}>
            {(frame, index) => (
              <li classList={{ matched: index() === frames().length - 1 }}>
                {frame}
              </li>
            )}
          </For>
        </ol>
        <Show when={props.selectedEvent}>
          {(event) => (
            <p class="matched-snippet">
              <Redacted
                text={event().masked_snippet}
                ruleClass={event().rule_class}
              />
            </p>
          )}
        </Show>
        <For each={props.detail.events}>
          {(event) => (
            <button type="button" onClick={() => props.onNavigate(event.id)}>
              {event.rule_id} · <RelativeTime iso={event.observed_at} />
            </button>
          )}
        </For>
      </FoldSection>
      <Show when={credential()}>
        <FoldSection
          id="linked-occurrences"
          title={`Linked occurrences (${props.detail.occurrences.items.length})`}
        >
          <p>Links are limited to fingerprints of one key version. A key rotation starts new groups.</p>
          <Show when={props.detail.occurrences.key_versions.length > 1}>
            <p>Occurrences span key versions {props.detail.occurrences.key_versions.join(", ")}.</p>
          </Show>
          <ul>
            <For each={props.detail.occurrences.items}>
              {(occurrence) => (
                <li>
                  <A href={`/issues/${encodeURIComponent(occurrence.issue_id)}`}>
                    {occurrence.short_id} · {occurrence.source} · {occurrence.display_locator}
                  </A>
                  <StatusBadge status={occurrence.status} />
                  <ExposureBadge visibility={occurrence.exposure_visibility} compact />
                </li>
              )}
            </For>
          </ul>
        </FoldSection>
      </Show>
      <Show when={props.detail.mergedInto}>
        {(primary) => <div class="merged-issue-banner">Merged into <A href={`/issues/${encodeURIComponent(primary().id)}`}>{primary().short_id}</A><Button size="xs" onClick={() => props.onUnmergeChild(primary().id, props.detail.issue.id)}>Unmerge</Button></div>}
      </Show>
      <Show when={props.detail.mergedChildren.length > 0}>
        <FoldSection id="merged-issues" title={`Merged issues (${props.detail.mergedChildren.length})`}>
          <ul>{props.detail.mergedChildren.map((child) => <li><A href={`/issues/${encodeURIComponent(child.id)}`}>{child.short_id} · {child.source} · {child.display_locator}</A><span>{child.status}</span><Button size="xs" onClick={() => props.onUnmerge([child.id])}>Unmerge</Button></li>)}</ul>
          <Button size="sm" onClick={() => props.onUnmerge()}>Unmerge all</Button>
        </FoldSection>
      </Show>
      <FoldSection id="external-issues" title={`External issues (${props.detail.externalIssues.length})`}>
        <ul>{props.detail.externalIssues.map((link) => <li><a href={link.url} target="_blank" rel="noreferrer">{link.title ?? link.url}</a><Button size="xs" onClick={() => props.onDeleteExternal(link.id)}>Remove link</Button></li>)}</ul>
        <Show when={props.detail.externalIssues.length === 0}><p>No external issues linked.</p></Show>
        <p>Use More to link an issue or create a GitHub issue.</p>
      </FoldSection>
      <FoldSection id="timeline" title="Timeline">
        <Timeline>
          <For each={props.detail.history}>
            {(item) => (
              <li>
                <span class={`timeline-dot timeline-${item.kind}`}>
                  {historyIcon(item.kind)}
                </span>
                <div>
                  <strong>{historyLabel(item.kind)}</strong>
                  <p>
                    {[item.from_value, item.to_value]
                      .filter((value) => value !== null)
                      .join(" → ")}
                    {item.note === null ? "" : ` · ${item.note}`}
                  </p>
                </div>
                <RelativeTime iso={item.at} />
              </li>
            )}
          </For>
        </Timeline>
        <Show when={props.detail.history.length === 0}>
          <p>No activity has been recorded.</p>
        </Show>
      </FoldSection>
      <FoldSection id="evidence" title="Evidence">
        <For each={props.detail.evidence}>
          {(item) => (
            <article class="evidence-row">
              <strong>{item.display_filename}</strong>
              <span>
                {item.verdict} · {item.size_bytes} bytes
              </span>
              <Show when={item.verdict === "rejected"}>
                <span>{item.rejection_rule_classes.join(", ")}</span>
              </Show>
            </article>
          )}
        </For>
        <Show when={props.detail.evidence.length === 0}>
          <p>No evidence uploaded.</p>
        </Show>
        <label>
          Upload evidence
          <input
            aria-label="Upload evidence"
            type="file"
            onChange={props.onUpload}
          />
        </label>
      </FoldSection>
      <FoldSection id="detection-details" title="Detection details">
        <dl class="issue-detail-grid">
          <dt>Detector</dt>
          <dd>{props.selectedEvent?.detector ?? "Unavailable"}</dd>
          <dt>Rule id</dt>
          <dd>{props.selectedEvent?.rule_id ?? "Unavailable"}</dd>
          <dt>Rule class</dt>
          <dd>
            {props.selectedEvent?.rule_class ?? props.detail.issue.rule_class}
          </dd>
          <dt>Confidence</dt>
          <dd>{props.selectedEvent?.confidence ?? "Unknown"}</dd>
          <dt>Fingerprint</dt>
          <dd>
            {props.selectedEvent?.fingerprint.slice(0, 8) ?? "Unavailable"}
          </dd>
        </dl>
      </FoldSection>
    </div>
  );
}
