import { A } from "@solidjs/router";
import { For, Show, createEffect, createResource, createSignal, type JSX } from "solid-js";
import { dataApi } from "../../api/client";
import type { DataAssetDetail, DataEvent, DataItem, DataItemKind } from "../../api/types";
import { Avatar } from "../../components/Avatar";
import { RelativeTime } from "../../components/RelativeTime";
import { ItemCountChart } from "../../components/ItemCountChart";
import { EmptyState, Table, Timeline } from "../../components/ui";

function eventLabel(event: DataEvent): string {
  const values = [event.kind.replaceAll("_", " ")];
  if (event.items_added !== null) values.push(`${event.items_added} added`);
  if (event.items_removed !== null) values.push(`${event.items_removed} removed`);
  if (event.item_count_after !== null) values.push(`${event.item_count_after} items`);
  if (event.field_key !== null) values.push(event.field_key);
  return values.join(" · ");
}

/** Displays the asset history and item/value inventory. */
export function DataDetailSections(props: Readonly<{ asset: DataAssetDetail; refreshToken: number }>): JSX.Element {
  const [events] = createResource(() => [props.asset.id, props.refreshToken] as const, async ([id]) => dataApi.events(id));
  const [series] = createResource(() => props.asset.id, async (id) => dataApi.itemSeries(id, 90));
  const [kind, setKind] = createSignal<DataItemKind>("item");
  const [itemState, setItemState] = createSignal<"present" | "removed">("present");
  const [items] = createResource(() => [props.asset.id, kind(), itemState(), props.refreshToken] as const, async ([id, itemKind, state]) => dataApi.items(id, itemKind, state));
  const [itemRows, setItemRows] = createSignal<DataItem[]>([]);
  const [itemCursor, setItemCursor] = createSignal<string | null>(null);
  const [eventRows, setEventRows] = createSignal<DataEvent[]>([]);
  const [eventCursor, setEventCursor] = createSignal<string | null>(null);
  const [tab, setTab] = createSignal<DataItemKind>("item");
  createEffect(() => {
    const page = events();
    if (page !== undefined) { setEventRows(page.items); setEventCursor(page.next_cursor); }
  });
  createEffect(() => {
    const page = items();
    if (page !== undefined) { setItemRows(page.items); setItemCursor(page.next_cursor); }
  });
  const loadMoreEvents = async (): Promise<void> => {
    const cursor = eventCursor();
    if (cursor === null) return;
    const page = await dataApi.events(props.asset.id, cursor);
    setEventRows((previous) => [...previous, ...page.items]);
    setEventCursor(page.next_cursor);
  };
  const loadMoreItems = async (): Promise<void> => {
    const cursor = itemCursor();
    if (cursor === null) return;
    const page = await dataApi.items(props.asset.id, kind(), itemState(), cursor);
    setItemRows((previous) => [...previous, ...page.items]);
    setItemCursor(page.next_cursor);
  };
  return <>
    <section class="data-detail-section"><h2>Timeline</h2><Timeline><For each={eventRows()}>{(event) => <li>{Avatar({ name: event.actor.display_name, ref: event.actor.ref, size: 24 })}<strong>{event.actor.display_name}</strong><span>{eventLabel(event)}</span><RelativeTime iso={event.occurred_at} /><Show when={event.note}><p>{event.note}</p></Show></li>}</For></Timeline><Show when={eventCursor() !== null}><button class="button-secondary" onClick={() => void loadMoreEvents()}>Load more</button></Show></section>
    <section class="data-detail-section"><h2>Item count</h2><ItemCountChart points={series()?.points ?? []} label="Item count over time" /></section>
    <section class="data-detail-section"><h2>Items and values</h2>
      <div role="tablist" aria-label="Asset contents"><button type="button" role="tab" aria-selected={tab() === "item"} onClick={() => { setTab("item"); setKind("item"); setItemCursor(null); }}>Items</button><button type="button" role="tab" aria-label="Values" aria-selected={tab() === "value"} onClick={() => { setTab("value"); setKind("value"); setItemCursor(null); }}>Values</button></div>
      <label class="field">Item state<select aria-label="Item state" value={itemState()} onChange={(event) => { setItemState(event.currentTarget.value as "present" | "removed"); setItemCursor(null); }}><option value="present">Present</option><option value="removed">Removed</option></select></label>
      <Show when={itemRows().length > 0} fallback={<EmptyState title={`No ${tab() === "item" ? "items" : "values"}`}><p>No entries are available for this asset.</p></EmptyState>}>
        <Table><thead><tr><th>Key prefix</th><th>First seen</th><th>Removed at</th></tr></thead><tbody><For each={itemRows()}>{(item: DataItem) => <tr><td><code>{item.key_prefix}</code></td><td>{new Date(item.first_seen_at).toLocaleString()}</td><td>{item.removed_at === null ? "—" : new Date(item.removed_at).toLocaleString()}</td></tr>}</For></tbody></Table>
      </Show>
      <Show when={itemCursor() !== null}><button class="button-secondary" onClick={() => void loadMoreItems()}>Load more</button></Show>
    </section>
    <section class="data-detail-section"><h2>Related issues</h2><Show when={props.asset.open_issues.length > 0} fallback={<p>No open issues are linked to this asset.</p>}><ul><For each={props.asset.open_issues}>{(issue) => <li><A href={`/issues/${encodeURIComponent(issue.issue_id)}`}>{issue.short_id}</A></li>}</For></ul></Show></section>
  </>;
}
