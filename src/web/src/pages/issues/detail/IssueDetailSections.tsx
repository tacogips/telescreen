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

function eventDate(event: IssueEvent): string {
  return new Date(event.observed_at).toISOString().slice(0, 10);
}

function eventGraph(events: readonly IssueEvent[]): number[] {
  const days: string[] = [];
  const now = new Date();
  for (let offset = 13; offset >= 0; offset -= 1) {
    const day = new Date(
      Date.UTC(
        now.getUTCFullYear(),
        now.getUTCMonth(),
        now.getUTCDate() - offset,
      ),
    );
    days.push(day.toISOString().slice(0, 10));
  }
  return days.map(
    (day) => events.filter((event) => eventDate(event) === day).length,
  );
}

function tagCounts(
  events: readonly IssueEvent[],
  select: (event: IssueEvent) => string,
): readonly Readonly<{ label: string; count: number }>[] {
  const counts = new Map<string, number>();
  for (const event of events) {
    const label = select(event);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1])
    .slice(0, 4)
    .map(([label, count]) => ({ label, count }));
}

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
    selectedEventId: string | null;
    activeTab: string;
    onUpload(event: Event & { currentTarget: HTMLInputElement }): void;
    onSelectEvent(id: string | null): void;
    onActiveTab(tab: string): void;
  }>,
): JSX.Element {
  const recommendedEvent = (): IssueEvent | undefined =>
    [...props.detail.events].sort(
      (left, right) =>
        (right.confidence ?? 0) - (left.confidence ?? 0) ||
        right.observed_at.localeCompare(left.observed_at),
    )[0];
  const moveEvent = (delta: number): void => {
    const events = props.detail.events;
    const index = events.findIndex(
      (event) => event.id === props.selectedEventId,
    );
    const nextIndex = Math.min(events.length - 1, Math.max(0, index + delta));
    props.onSelectEvent(events[nextIndex]?.id ?? null);
  };
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
    <main class="issue-detail-main">
      <section class="finding-chart-block">
        <h3>Loaded findings, last 14 days</h3>
        <MiniBarChart
          values={eventGraph(props.detail.events)}
          label="Loaded findings, last 14 days"
          height={48}
        />
        <div class="finding-tags">
          <For each={tagCounts(props.detail.events, (event) => event.detector)}>
            {(item) => (
              <Tag variant="muted">
                {item.label} · {item.count}
              </Tag>
            )}
          </For>
          <For
            each={tagCounts(
              props.detail.events,
              (event) => event.location.split(/[ >/:]/)[0] ?? "unknown",
            )}
          >
            {(item) => (
              <Tag variant="info">
                {item.label} · {item.count}
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
            onClick={() => props.onSelectEvent(recommendedEvent()?.id ?? null)}
          >
            Recommended
          </Button>
          <Button
            size="xs"
            onClick={() =>
              props.onSelectEvent(props.detail.events[0]?.id ?? null)
            }
          >
            First
          </Button>
          <Button
            size="xs"
            onClick={() =>
              props.onSelectEvent(props.detail.events.at(-1)?.id ?? null)
            }
          >
            Latest
          </Button>
          <Button size="xs" onClick={() => moveEvent(-1)}>
            Previous
          </Button>
          <Button size="xs" onClick={() => moveEvent(1)}>
            Next
          </Button>
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
            <button type="button" onClick={() => props.onSelectEvent(event.id)}>
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
    </main>
  );
}
