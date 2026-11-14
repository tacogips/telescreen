import { For, Show, type JSX } from "solid-js";
import type { SavedView } from "../../api/types";

export function StreamViews(props: Readonly<{
  views: readonly SavedView[];
  activeViewId: string | null;
  onApply(view: SavedView): void;
  onRemove(view: SavedView): void;
  onStar(view: SavedView): void;
  onDefault(view: SavedView): void;
}>): JSX.Element {
  const section = (label: string, visibility: "personal" | "shared"): JSX.Element => <section class="saved-view-section" aria-label={label}>
    <strong>{label}</strong>
    <For each={props.views.filter((view) => view.visibility === visibility)}>{(view) => <span class="issue-view-item">
      <button type="button" onClick={() => props.onApply(view)} aria-current={props.activeViewId === view.id ? "page" : undefined}>{view.is_default ? "Default · " : ""}{view.name}</button>
      <button type="button" aria-label={`${view.starred ? "Unstar" : "Star"} view ${view.name}`} onClick={() => props.onStar(view)}>{view.starred ? "★" : "☆"}</button>
      <button type="button" aria-label={`Set ${view.name} as default`} onClick={() => props.onDefault(view)}>Set default</button>
      <Show when={view.visibility === "personal"}><button type="button" aria-label={`Delete view ${view.name}`} onClick={() => props.onRemove(view)}>Delete</button></Show>
    </span>}</For>
  </section>;
  return <nav class="issue-views" aria-label="Saved issue views">{section("Personal views", "personal")}{section("Shared views", "shared")}</nav>;
}
