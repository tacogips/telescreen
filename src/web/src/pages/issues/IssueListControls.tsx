import { For, Show, type JSX } from "solid-js";
import type { Member, StreamTab, Team } from "../../api/types";
import { PeriodPicker, type Period } from "../../components/PeriodPicker";
import { StreamTabs } from "../../components/StreamTabs";
import { TopBar } from "../../components/shell/TopBar";
import {
  SearchQueryBuilder,
  SortSelect,
  SplitButton,
} from "../../components/ui";
import { ISSUE_QUERY_KEYS } from "./query";
import type { IssueView } from "./savedViews";
import { StreamViews } from "./StreamViews";

const SORTS = [
  { value: "last_seen", label: "Last seen", detail: "Last time the item was found" },
  { value: "first_seen", label: "First seen", detail: "When the issue was first reported" },
  { value: "times_seen", label: "Findings", detail: "Number of findings" },
  { value: "values", label: "Values", detail: "Distinct detected values" },
  { value: "risk", label: "Risk", detail: "Highest risk first" },
  { value: "priority", label: "Priority", detail: "Triage priority" },
  { value: "trending", label: "Trending", detail: "Most findings in the last 24 hours" },
] as const;

export function IssueListControls(props: Readonly<{
  query: string;
  sort: string;
  period: Period;
  start: string;
  end: string;
  trend: "24h" | "14d";
  tab: StreamTab;
  counts: Readonly<Partial<Record<StreamTab, number>>>;
  members: readonly Member[];
  teams: readonly Team[];
  views: readonly IssueView[];
  activeViewId: string | null;
  dirty: boolean;
  onQuery(value: string): void;
  onSort(value: string): void;
  onPeriod(value: Period, start?: string, end?: string): void;
  onTrend(value: "24h" | "14d"): void;
  onTab(value: StreamTab): void;
  onSave(): void;
  onReset(): void;
  onApply(view: IssueView): void;
  onRemove(view: IssueView): void;
  onStar(view: IssueView): void;
  onDefault(view: IssueView): void;
}>): JSX.Element {
  const queryKeys = () => ISSUE_QUERY_KEYS.map((entry) => ({
    key: entry.key,
    description: entry.key,
    ...(entry.values === undefined ? {} : { values: entry.key === "assigned"
      ? [...entry.values, ...props.members.map((member) => `member:${member.handle}`), ...props.teams.map((team) => `#${team.slug}`)]
      : entry.values }),
  }));
  return <>
    <TopBar.Slot name="title">Issues</TopBar.Slot>
    <TopBar.Slot name="actions">
      <Show when={props.dirty}><span class="view-dirty-dot" aria-label="Unsaved view changes" /></Show>
      <SplitButton menuLabel="Save view options" onClick={props.onSave} menu={<div role="none"><button role="menuitem" type="button" onClick={props.onSave}>Save as new view</button><button role="menuitem" type="button" onClick={props.onReset}>Reset</button></div>}>Save view</SplitButton>
    </TopBar.Slot>
    <StreamTabs value={props.tab} counts={props.counts} onChange={props.onTab} />
    <StreamViews views={props.views} activeViewId={props.activeViewId} onApply={props.onApply} onRemove={props.onRemove} onStar={props.onStar} onDefault={props.onDefault} />
    <div class="issue-controls">
      <SearchQueryBuilder value={props.query} onChange={props.onQuery} keys={queryKeys()} placeholder="Search issues…" />
      <PeriodPicker value={props.period} onChange={props.onPeriod} />
      <Show when={props.period === "custom"}><label>Start date<input aria-label="Start date" type="date" value={props.start} onInput={(event) => props.onPeriod("custom", event.currentTarget.value, undefined)} onChange={(event) => props.onPeriod("custom", event.currentTarget.value, undefined)} /></label><label>End date<input aria-label="End date" type="date" value={props.end} onInput={(event) => props.onPeriod("custom", undefined, event.currentTarget.value)} onChange={(event) => props.onPeriod("custom", undefined, event.currentTarget.value)} /></label></Show>
      <SortSelect value={props.sort} options={SORTS} onChange={props.onSort} />
      <label>Trend <select aria-label="Trend window" value={props.trend} onChange={(event) => props.onTrend(event.currentTarget.value as "24h" | "14d")}><option value="24h">24 hours</option><option value="14d">14 days</option></select></label>
      <details class="stream-directory" aria-label="Assignee autocomplete values"><summary>Assignees</summary><For each={props.members}>{(member) => <span>{member.display_name} · member:{member.handle}</span>}</For><For each={props.teams}>{(team) => <span>#{team.slug}</span>}</For></details>
    </div>
  </>;
}
