import { A } from "@solidjs/router";
import { Show, createResource, createSignal, type JSX } from "solid-js";
import { useAuth } from "../../auth/useAuth";
import { ApiError, dataApi } from "../../api/client";
import type { DataAssetDetail, DataAssetKind } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Drawer, Panel, Tag, showToast } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";
import { MetadataForm } from "./MetadataForm";
import { DataDetailSections } from "./DataDetailSections";

const kinds: readonly DataAssetKind[] = ["file", "table", "list", "channel", "object", "folder", "other"];
function safeError(error: unknown): ApiError { return error instanceof ApiError ? error : new ApiError(0, "request_failed", "The request could not be completed."); }
function localInputValue(date: Date): string { return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
function lastSeenText(iso: string | null): string { return iso === null ? "—" : new Date(iso).toLocaleString(); }

/** Shows asset metadata, lifecycle actions, timeline, item history, and related issues. */
export default function DataDetail(props: Readonly<{ id: string }>): JSX.Element {
  const auth = useAuth();
  const [asset, { refetch }] = createResource(() => props.id, (id): Promise<DataAssetDetail> => dataApi.get(id));
  const [error, setError] = createSignal<ApiError | null>(null);
  const [refreshToken, setRefreshToken] = createSignal(0);
  const [editorOpen, setEditorOpen] = createSignal(false);
  const [eventOpen, setEventOpen] = createSignal(false);
  const [eventAt, setEventAt] = createSignal(localInputValue(new Date()));
  const [note, setNote] = createSignal("");
  const [itemsAdded, setItemsAdded] = createSignal("");
  const [itemsRemoved, setItemsRemoved] = createSignal("");
  const [countAfter, setCountAfter] = createSignal("");
  const [editName, setEditName] = createSignal("");
  const [editKind, setEditKind] = createSignal<DataAssetKind>("other");
  const action = async (run: () => Promise<unknown>, success: string): Promise<void> => {
    setError(null);
    try { await run(); await refetch(); setRefreshToken((value) => value + 1); showToast(success, "success"); }
    catch (failure) { setError(safeError(failure)); }
  };
  const saveFields = async (values: readonly Readonly<{ key: string; value_json: string | null }>[]): Promise<void> => {
    const current = asset();
    if (current === undefined) return;
    await action(() => dataApi.putFields(current.id, { values }), "Metadata saved");
  };
  const saveEvent = async (submitEvent: SubmitEvent): Promise<void> => {
    submitEvent.preventDefault();
    const current = asset();
    if (current === undefined) return;
    const occurredAt = new Date(eventAt()).toISOString();
    await action(() => dataApi.recordEvent(current.id, {
      occurred_at: occurredAt,
      note: note(),
      ...(itemsAdded() === "" ? {} : { items_added: Number(itemsAdded()) }),
      ...(itemsRemoved() === "" ? {} : { items_removed: Number(itemsRemoved()) }),
      ...(countAfter() === "" ? {} : { item_count_after: Number(countAfter()) }),
    }), "Event recorded");
    setEventOpen(false);
  };
  const saveEdit = async (submitEvent: SubmitEvent): Promise<void> => {
    submitEvent.preventDefault();
    const current = asset();
    if (current === undefined) return;
    await action(() => dataApi.patch(current.id, { name: current.origin === "registered" ? editName() : undefined, kind: editKind() }), "Asset updated");
    setEditorOpen(false);
  };
  return <section class="page-content">
    <TopBar.Slot name="title">{asset()?.name ?? "Data asset"}</TopBar.Slot>
    <Show when={error() ?? (asset.error === undefined ? null : safeError(asset.error))} keyed>{(failure) => <ErrorBanner error={failure} />}</Show>
    <Show when={asset.loading} fallback={<Show when={asset()}>{(current) => <>
      <nav class="data-breadcrumb" aria-label="Breadcrumb"><A href="/data">Data</A><span aria-hidden="true"> / </span><span aria-current="page">{current().short_id}</span></nav>
      <Panel title={current().name}>
        <div class="data-asset-heading"><code>{current().short_id}</code><Tag variant={current().state === "present" ? "success" : current().state === "deleted" ? "muted" : "warning"}>{current().state}</Tag><Tag>{current().origin}</Tag><Tag>{current().kind}</Tag></div>
        <dl><dt>Source</dt><dd>{current().source}</dd><dt>Locator</dt><dd>{current().display_locator}</dd><dt>First seen</dt><dd>{new Date(current().first_seen_at).toLocaleString()}</dd><dt>Last seen</dt><dd>{lastSeenText(current().last_seen_at)}</dd></dl>
        <Show when={current().incomplete}><div class="notice-warning" role="status">Required information is missing: {current().missing_required.join(", ")}</div></Show>
        <Show when={auth.can("data.write")}><div class="row-actions"><button class="button-secondary" onClick={() => { setEditName(current().name); setEditKind(current().kind); setEditorOpen(true); }}>Edit</button><button class="button-secondary" aria-label="Record event" onClick={() => { setEventAt(localInputValue(new Date())); setEventOpen(true); }}>Record event</button><Show when={current().state === "deleted"} fallback={<button class="button-secondary" onClick={() => void action(() => dataApi.markDeleted(current().id), "Asset marked deleted")}>Mark deleted</button>}><button class="button-secondary" onClick={() => void action(() => dataApi.restore(current().id), "Asset restored")}>Restore</button></Show></div></Show>
      </Panel>
      <Panel title="Metadata"><MetadataForm fields={current().fields} disabled={!auth.can("data.write")} onSave={saveFields} /></Panel>
      <DataDetailSections asset={current()} refreshToken={refreshToken()} />
      <Drawer open={editorOpen()} title="Edit data asset" onClose={() => setEditorOpen(false)}><form class="page-drawer-content" onSubmit={(event) => void saveEdit(event)}>
        <Show when={current().origin === "registered"}><label class="field">Name<input aria-label="Asset name" required value={editName()} onInput={(event) => setEditName(event.currentTarget.value)} /></label></Show>
        <label class="field">Kind<select aria-label="Asset kind" value={editKind()} onChange={(event) => setEditKind(event.currentTarget.value as DataAssetKind)}>{kinds.map((kind) => <option value={kind}>{kind}</option>)}</select></label><button class="button-primary" type="submit">Save asset</button>
      </form></Drawer>
      <Drawer open={eventOpen()} title="Record event" onClose={() => setEventOpen(false)}><form class="page-drawer-content" onSubmit={(event) => void saveEvent(event)}>
        <label class="field">Occurred at<input aria-label="Occurred at" type="datetime-local" required value={eventAt()} onInput={(event) => setEventAt(event.currentTarget.value)} /></label>
        <label class="field">Items added<input aria-label="Items added" type="number" min="0" value={itemsAdded()} onInput={(event) => setItemsAdded(event.currentTarget.value)} /></label>
        <label class="field">Items removed<input aria-label="Items removed" type="number" min="0" value={itemsRemoved()} onInput={(event) => setItemsRemoved(event.currentTarget.value)} /></label>
        <label class="field">Item count after<input aria-label="Item count after" type="number" min="0" value={countAfter()} onInput={(event) => setCountAfter(event.currentTarget.value)} /></label>
        <label class="field">Note<textarea aria-label="Event note" required minLength={1} maxLength={1000} value={note()} onInput={(event) => setNote(event.currentTarget.value)} /></label>
        <button class="button-primary" type="submit">Record event</button>
      </form></Drawer>
    </>}</Show>}>
      <p>Loading data asset…</p>
    </Show>
  </section>;
}
