import { For, Show, type JSX } from "solid-js";
import { TopBar } from "../../components/shell/TopBar";
import {
  PageFilterBar,
  SearchQueryBuilder,
  SortSelect,
  SplitButton,
} from "../../components/ui";
import { ISSUE_QUERY_KEYS, ISSUE_SOURCES } from "./query";
import type { IssueView } from "./savedViews";

const SORTS = [
  {
    value: "last_seen",
    label: "Last seen",
    detail: "Last time the item was found",
  },
  {
    value: "first_seen",
    label: "First seen",
    detail: "When the issue was first reported",
  },
  { value: "times_seen", label: "Findings", detail: "Number of findings" },
] as const;

function sourceLabel(source: string): string {
  if (source === "gdrive") return "Google Drive";
  if (source === "gdocs") return "Google Docs";
  if (source === "gsheets") return "Google Sheets";
  return source;
}

export function IssueListControls(
  props: Readonly<{
    query: string;
    sort: string;
    source: string;
    views: readonly IssueView[];
    activeViewId: string | null;
    dirty: boolean;
    onQuery(value: string): void;
    onSort(value: string): void;
    onSource(value: string): void;
    onSave(): void;
    onReset(): void;
    onApply(view: IssueView): void;
    onRemove(view: IssueView): void;
  }>,
): JSX.Element {
  return (
    <>
      <TopBar.Slot name="title">Issues</TopBar.Slot>
      <TopBar.Slot name="actions">
        <Show when={props.dirty}>
          <span class="view-dirty-dot" aria-label="Unsaved view changes" />
        </Show>
        <SplitButton
          menuLabel="Save view options"
          onClick={props.onSave}
          menu={
            <div role="none">
              <button role="menuitem" type="button" onClick={props.onSave}>
                Save as new view
              </button>
              <button role="menuitem" type="button" onClick={props.onReset}>
                Reset
              </button>
            </div>
          }
        >
          Save view
        </SplitButton>
      </TopBar.Slot>
      <nav class="issue-views" aria-label="Saved issue views">
        <For each={props.views}>
          {(view) => (
            <span class="issue-view-item">
              <button
                type="button"
                onClick={() => props.onApply(view)}
                aria-current={
                  props.activeViewId === view.id ? "page" : undefined
                }
              >
                {view.starred ? "★ " : ""}
                {view.name}
              </button>
              <button
                type="button"
                aria-label={`Remove view ${view.name}`}
                onClick={() => props.onRemove(view)}
              >
                ×
              </button>
            </span>
          )}
        </For>
      </nav>
      <div class="issue-controls">
        <PageFilterBar>
          <label>
            Source
            <select
              aria-label="Source"
              value={props.source}
              onChange={(event) => props.onSource(event.currentTarget.value)}
            >
              <option value="">All sources</option>
              <For each={ISSUE_SOURCES}>
                {(source) => (
                  <option value={source}>{sourceLabel(source)}</option>
                )}
              </For>
            </select>
          </label>
        </PageFilterBar>
        <SearchQueryBuilder
          value={props.query}
          onChange={props.onQuery}
          keys={ISSUE_QUERY_KEYS}
          placeholder="Search issues…"
        />
        <SortSelect
          value={props.sort}
          options={SORTS}
          onChange={props.onSort}
        />
      </div>
    </>
  );
}
