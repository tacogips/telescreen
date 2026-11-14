import { useNavigate } from "@solidjs/router";
import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { useAuth } from "../../auth/useAuth";
import { ApiError, dataApi } from "../../api/client";
import type { DataAssetKind, DataFieldSlot, JsonValue } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Panel, Placeholder } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";
import { MetadataForm } from "./MetadataForm";

const kinds: readonly DataAssetKind[] = ["file", "table", "list", "channel", "object", "folder", "other"];
const slotsFrom = (fields: Awaited<ReturnType<typeof dataApi.fields>>["items"]): DataFieldSlot[] => fields.filter((field) => !field.archived).map((field) => ({ key: field.key, label: field.label, field_type: field.field_type, required: field.required, role: field.role, options: field.options, value_json: null }));

/** Registers a manually tracked data asset and its metadata. */
export default function DataRegister(): JSX.Element {
  const auth = useAuth();
  const navigate = useNavigate();
  const [fields] = createResource(async (): Promise<DataFieldSlot[]> => slotsFrom((await dataApi.fields()).items));
  const [name, setName] = createSignal("");
  const [source, setSource] = createSignal("");
  const [locator, setLocator] = createSignal("");
  const [kind, setKind] = createSignal<DataAssetKind>("other");
  const [itemCount, setItemCount] = createSignal("");
  const [error, setError] = createSignal<ApiError | null>(null);
  const register = async (values: readonly Readonly<{ key: string; value_json: string | null }>[]): Promise<void> => {
    setError(null);
    try {
      const typedValues: Record<string, JsonValue> = Object.fromEntries(values.flatMap((entry) => {
        if (entry.value_json === null) return [];
        try { return [[entry.key, JSON.parse(entry.value_json) as JsonValue]]; } catch { return []; }
      }));
      const asset = await dataApi.register({ name: name(), source: source(), locator: locator(), kind: kind(), ...(itemCount() === "" ? {} : { item_count: Number(itemCount()) }), values: typedValues });
      await navigate(`/data/${encodeURIComponent(asset.id)}`);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) setError(new ApiError(409, failure.code, "Already registered"));
      else setError(failure instanceof ApiError ? failure : new ApiError(0, "request_failed", "Registration could not be completed."));
    }
  };
  return <section class="page-content">
    <TopBar.Slot name="title">Register data</TopBar.Slot>
    <Show when={auth.can("data.write")} fallback={<p role="alert">You do not have permission to register data.</p>}>
      <Show when={!fields.loading} fallback={<Placeholder label="Loading field schema…" />}>
        <Show when={error()} keyed>{(failure) => <ErrorBanner error={failure} />}</Show>
        <Panel title="Data asset">
          <div class="page-drawer-content">
            <label class="field">Name<input aria-label="Name" required value={name()} onInput={(event) => setName(event.currentTarget.value)} /></label>
            <label class="field">Source<input aria-label="Source" required value={source()} onInput={(event) => setSource(event.currentTarget.value)} /></label>
            <label class="field">Locator<input aria-label="Locator" required value={locator()} onInput={(event) => setLocator(event.currentTarget.value)} /></label>
            <label class="field">Kind<select aria-label="Kind" value={kind()} onChange={(event) => setKind(event.currentTarget.value as DataAssetKind)}><For each={kinds}>{(value) => <option value={value}>{value}</option>}</For></select></label>
            <label class="field">Item count<input aria-label="Item count" type="number" min="0" value={itemCount()} onInput={(event) => setItemCount(event.currentTarget.value)} /></label>
          </div>
          <MetadataForm fields={fields() ?? []} onSave={register} submitLabel="Register data" disabled={!name() || !source() || !locator()} />
        </Panel>
      </Show>
    </Show>
  </section>;
}
