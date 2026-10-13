import { A, useSearchParams } from "@solidjs/router";
import { For, Show, createEffect, createResource, createSignal, untrack, type JSX } from "solid-js";
import { useAuth } from "../../auth/useAuth";
import { ApiError, dataApi, directoryApi } from "../../api/client";
import type { DataAsset, DataAssetTab, DataCounts, Member } from "../../api/types";
import { Avatar } from "../../components/Avatar";
import { ErrorBanner } from "../../components/ErrorBanner";
import { CountTabs } from "../../components/CountTabs";
import { Sparkline } from "../../components/Sparkline";
import { EmptyState, Placeholder, Table, Tag } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";

const tabs: readonly Readonly<{ id: DataAssetTab; label: string; count: (counts: DataCounts) => number }>[] = [
  { id: "all", label: "All", count: (counts) => counts.all },
  { id: "present", label: "Present", count: (counts) => counts.present },
  { id: "deleted", label: "Deleted", count: (counts) => counts.deleted },
  { id: "incomplete", label: "Incomplete", count: (counts) => counts.incomplete },
  { id: "overdue", label: "Overdue", count: (counts) => counts.overdue },
];
const filterNames = ["source", "kind", "state", "origin", "owner", "fields"] as const;
type FilterName = typeof filterNames[number];
type ListData = Readonly<{ rows: readonly DataAsset[]; counts: DataCounts; cursor: string | null }>;
const emptyCounts: DataCounts = { all: 0, present: 0, deleted: 0, incomplete: 0, overdue: 0 };

function filtersFrom(search: Readonly<Record<string, string | readonly string[] | undefined>>): URLSearchParams {
  const filters = new URLSearchParams();
  for (const key of ["tab", "q", "source", "kind", "state", "origin", "owner"] as const) {
    const raw = search[key];
    const value = Array.isArray(raw) ? raw[0] : raw;
    if (value !== undefined && value !== "") filters.set(key, value);
  }
  const rawFields = search.fields;
  const fields = Array.isArray(rawFields) ? rawFields[0] ?? "" : rawFields ?? "";
  for (const field of fields.split(",").map((value: string) => value.trim()).filter(Boolean)) filters.append("field", field);
  return filters;
}

function stateVariant(state: DataAsset["state"]): "success" | "muted" | "warning" {
  return state === "present" ? "success" : state === "deleted" ? "muted" : "warning";
}

/** Searchable, filterable data asset register with counts and export. */
export default function DataList(): JSX.Element {
  const auth = useAuth();
  const [params, setParams] = useSearchParams();
  const [pageCursor, setPageCursor] = createSignal<string | null>(null);
  const [loadedRows, setLoadedRows] = createSignal<readonly DataAsset[]>([]);
  const [members] = createResource(async (): Promise<Member[]> => (await directoryApi.members()).items);
  const [data, { refetch }] = createResource(() => ({ tab: params.tab, q: params.q, source: params.source, kind: params.kind, state: params.state, origin: params.origin, owner: params.owner, fields: params.fields }), async (search): Promise<ListData> => {
    const filters = filtersFrom(search);
    if (pageCursor() !== null) filters.set("cursor", pageCursor() ?? "");
    const [page, counts] = await Promise.all([dataApi.list(filters), dataApi.counts(filtersFrom({ ...search, tab: undefined }))]);
    return { rows: page.items, counts, cursor: page.next_cursor };
  });
  createEffect(() => {
    const page = data();
    if (page !== undefined) setLoadedRows((previous) => untrack(pageCursor) === null ? page.rows : [...previous, ...page.rows]);
  });
  const [fieldChips, setFieldChips] = createSignal(params.fields ?? "");
  const [exportError, setExportError] = createSignal<ApiError | null>(null);
  const selectedTab = (): DataAssetTab => tabs.some((tab) => tab.id === params.tab) ? params.tab as DataAssetTab : "all";
  const setFilter = (name: FilterName, value: string): void => {
    setPageCursor(null);
    setLoadedRows([]);
    setParams({ [name]: value || undefined });
  };
  const exportCsv = async (): Promise<void> => {
    setExportError(null);
    try {
      const blob = await dataApi.exportCsv(filtersFrom(params));
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `telescreen-data-${new Date().toISOString().slice(0, 10)}.csv`;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
    } catch (failure) {
      setExportError(failure instanceof ApiError ? failure : new ApiError(0, "request_failed", "CSV export could not be completed."));
    }
  };
  const loadMore = async (): Promise<void> => {
    const cursor = data.latest?.cursor;
    if (cursor === null || cursor === undefined) return;
    setPageCursor(cursor);
    await refetch();
  };
  return <section class="page-content">
    <TopBar.Slot name="title">Data</TopBar.Slot>
    <TopBar.Slot name="actions"><button class="button-secondary" onClick={() => void exportCsv()}>Export CSV</button><Show when={auth.can("data.write")}><A class="button-primary" href="/data/new">Register data</A></Show></TopBar.Slot>
    <Show when={exportError() ?? data.error} keyed>{(error) => <ErrorBanner error={error} />}</Show>
    <CountTabs tabs={tabs.map((tab) => ({ value: tab.id, label: tab.label }))} value={selectedTab()} counts={Object.fromEntries(tabs.map((tab) => [tab.id, tab.count(data.latest?.counts ?? emptyCounts)]))} label="Data assets" onChange={(id) => { setPageCursor(null); setLoadedRows([]); setParams({ tab: id === "all" ? undefined : id }); }} />
    <div class="page-filter-bar" role="search" aria-label="Filter data assets">
        <label class="field">Search<input aria-label="Search data" value={params.q ?? ""} onInput={(event) => { setPageCursor(null); setLoadedRows([]); setParams({ q: event.currentTarget.value || undefined }); }} /></label>
        <label class="field">Source<select aria-label="Source" value={params.source ?? ""} onChange={(event) => setFilter("source", event.currentTarget.value)}><option value="">All sources</option><For each={[...new Set([...loadedRows().map((asset) => asset.source), params.source ?? ""].filter(Boolean))]}>{(source) => <option value={source}>{source}</option>}</For></select></label>
        <label class="field">Kind<select aria-label="Kind" value={params.kind ?? ""} onChange={(event) => setFilter("kind", event.currentTarget.value)}><option value="">All kinds</option>{["file", "table", "list", "channel", "object", "folder", "other"].map((kind) => <option value={kind}>{kind}</option>)}</select></label>
        <label class="field">State<select aria-label="State" value={params.state ?? ""} onChange={(event) => setFilter("state", event.currentTarget.value)}><option value="">All states</option>{["present", "deleted", "unknown"].map((state) => <option value={state}>{state}</option>)}</select></label>
        <label class="field">Origin<select aria-label="Origin" value={params.origin ?? ""} onChange={(event) => setFilter("origin", event.currentTarget.value)}><option value="">All origins</option><option value="discovered">Discovered</option><option value="registered">Registered</option></select></label>
        <label class="field">Owner<select aria-label="Owner" value={params.owner ?? ""} onChange={(event) => setFilter("owner", event.currentTarget.value)}><option value="">All owners</option><For each={members()}>{(member) => <option value={`member:${member.handle}`}>{member.display_name}</option>}</For></select></label>
        <label class="field">Field chips<input aria-label="Field filters" placeholder="purpose:research" value={fieldChips()} onInput={(event) => setFieldChips(event.currentTarget.value)} onChange={(event) => { setPageCursor(null); setLoadedRows([]); setParams({ fields: event.currentTarget.value || undefined }); }} /></label>
    </div>
    <Show when={!data.loading || loadedRows().length > 0} fallback={<Placeholder label="Loading data assets…" />}>
      <Show when={loadedRows().length > 0} fallback={<EmptyState title="No data assets"><p>Data assets appear here when discovered or registered.</p></EmptyState>}>
        <Table><thead><tr><th>Name</th><th>Source</th><th>State</th><th>Items</th><th>Open issues</th><th>Owner</th><th>Deletion due</th><th>Last seen</th></tr></thead><tbody>
          <For each={loadedRows()}>{(asset) => <tr>
            <td><A href={`/data/${encodeURIComponent(asset.id)}`}><strong>{asset.name}</strong></A><small>{asset.display_locator}</small></td><td>{asset.source}</td><td><Tag variant={stateVariant(asset.state)}>{asset.state}</Tag><Show when={asset.incomplete}><Tag variant="warning">Incomplete</Tag></Show></td>
            <td>{asset.item_count ?? "—"}<Show when={asset.item_trend.length > 1}><Sparkline values={[...asset.item_trend]} /></Show></td><td>{asset.open_issue_count}</td><td>{asset.owner ? <>{Avatar({ name: asset.owner.display_name, ref: asset.owner.ref, size: 22 })} {asset.owner.display_name}</> : "—"}</td><td classList={{ "text-danger": asset.overdue }}>{asset.deletion_due ?? "—"}</td><td>{new Date(asset.last_seen_at).toLocaleString()}</td>
          </tr>}</For>
        </tbody></Table>
      </Show>
      <Show when={data.latest?.cursor !== null && data.latest?.cursor !== undefined}><button class="button-secondary" onClick={() => void loadMore()}>Load more</button></Show>
    </Show>
  </section>;
}
