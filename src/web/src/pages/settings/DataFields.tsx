import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { ApiError, dataApi } from "../../api/client";
import { useAuth } from "../../auth/useAuth";
import type { DataField, DataFieldBody, DataFieldRole, DataFieldType } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Drawer, EmptyState, Table, showToast } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";

const fieldTypes: readonly DataFieldType[] = ["text", "long_text", "integer", "decimal", "boolean", "date", "single_select", "multi_select", "reference", "url"];
const roles: readonly DataFieldRole[] = ["purpose", "owner", "deletion_due"];
const fieldUseMessage = "This field has values and cannot be changed or deleted.";
function safeError(value: unknown): ApiError { return value instanceof ApiError ? value : new ApiError(0, "request_failed", "The field schema could not be updated."); }
function optionsText(field: DataField | null): string { return field?.options.map((option) => `${option.value}|${option.label}`).join("\n") ?? ""; }
function parseOptions(value: string): readonly Readonly<{ value: string; label: string }>[] { return value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => { const [optionValue, ...label] = line.split("|"); return { value: (optionValue ?? "").trim(), label: label.join("|").trim() }; }); }

/** Admin editor for the ordered metadata field schema. */
export default function DataFields(): JSX.Element {
  const auth = useAuth();
  const [fields, { refetch }] = createResource(() => auth.can("data_fields.admin"), async (allowed) => allowed ? (await dataApi.fields()).items : []);
  const [editing, setEditing] = createSignal<DataField | null>(null);
  const [creating, setCreating] = createSignal(false);
  const [error, setError] = createSignal<ApiError | null>(null);
  const [conflict, setConflict] = createSignal("");
  const [key, setKey] = createSignal("");
  const [label, setLabel] = createSignal("");
  const [description, setDescription] = createSignal("");
  const [fieldType, setFieldType] = createSignal<DataFieldType>("text");
  const [options, setOptions] = createSignal("");
  const [required, setRequired] = createSignal(false);
  const [role, setRole] = createSignal<DataFieldRole | "">("");
  const begin = (field: DataField | null): void => {
    setEditing(field); setCreating(field === null); setKey(field?.key ?? ""); setLabel(field?.label ?? "");
    setDescription(field?.description ?? ""); setFieldType(field?.field_type ?? "text"); setOptions(optionsText(field));
    setRequired(field?.required ?? false); setRole(field?.role ?? ""); setError(null); setConflict("");
  };
  const save = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault(); setError(null); setConflict("");
    const selectedRole = role();
    const body: DataFieldBody = { key: key(), label: label(), description: description(), field_type: fieldType(), options: fieldType() === "single_select" || fieldType() === "multi_select" ? parseOptions(options()) : [], required: required(), role: selectedRole === "" ? null : selectedRole };
    try {
      if (creating()) await dataApi.createField(body);
      else if (editing() !== null) await dataApi.updateField(editing()!.id, { label: body.label, description: body.description, field_type: body.field_type, options: body.options, required: body.required, role: body.role });
      setEditing(null); setCreating(false); await refetch(); showToast("Field saved", "success");
    } catch (failure) {
      const apiError = safeError(failure);
      if (apiError.status === 409 && apiError.code === "field_in_use") setConflict(fieldUseMessage);
      else if (apiError.status === 409) setConflict("A field with this key or role already exists.");
      else setError(apiError);
    }
  };
  const reorder = async (field: DataField, delta: -1 | 1): Promise<void> => {
    const ordered = [...(fields() ?? []).filter((entry) => !entry.archived)];
    const index = ordered.findIndex((entry) => entry.id === field.id);
    const next = index + delta;
    if (next < 0 || next >= ordered.length) return;
    [ordered[index], ordered[next]] = [ordered[next]!, ordered[index]!];
    try { await dataApi.reorderFields(ordered.map((entry) => entry.id)); await refetch(); }
    catch (failure) { setError(safeError(failure)); }
  };
  const fieldAction = async (field: DataField, action: "archive" | "unarchive" | "delete"): Promise<void> => {
    setError(null); setConflict("");
    try {
      if (action === "archive") await dataApi.archiveField(field.id);
      else if (action === "unarchive") await dataApi.unarchiveField(field.id);
      else await dataApi.deleteField(field.id);
      await refetch();
    } catch (failure) {
      const apiError = safeError(failure);
      if (apiError.status === 409 && apiError.code === "field_in_use") setConflict(fieldUseMessage);
      else if (apiError.status === 409) setConflict("This field conflicts with another field.");
      else setError(apiError);
    }
  };
  return <Show when={auth.can("data_fields.admin")} fallback={<p role="alert">You do not have permission to manage the field schema.</p>}><section class="page-content">
    <TopBar.Slot name="title">Field schema</TopBar.Slot>
    <TopBar.Slot name="actions"><button class="button-primary" aria-label="New field" onClick={() => begin(null)}>New field</button></TopBar.Slot>
    <Show when={error()} keyed>{(failure) => <ErrorBanner error={failure} />}</Show><Show when={conflict()}><p role="alert">{conflict()}</p></Show>
    <Show when={(fields()?.length ?? 0) > 0} fallback={<EmptyState title="No fields configured"><p>Add fields to describe data assets.</p></EmptyState>}>
      <Table><thead><tr><th>Label</th><th>Key</th><th>Type</th><th>Required</th><th>Role</th><th>Values</th><th>Archived</th><th>Order</th><th>Actions</th></tr></thead><tbody><For each={[...(fields() ?? [])].sort((a, b) => Number(a.archived) - Number(b.archived) || a.position - b.position)}>{(field, index) => <tr>
        <td><button class="text-button" onClick={() => begin(field)}>{field.label}</button></td><td><code>{field.key}</code></td><td>{field.field_type}</td><td>{field.required ? "Yes" : "No"}</td><td>{field.role ?? "—"}</td><td>{field.value_count}</td><td>{field.archived ? "Yes" : "No"}</td>
        <td><button class="button-secondary" aria-label={`Move ${field.label} up`} disabled={field.archived || index() === 0} onClick={() => void reorder(field, -1)}>↑</button><button class="button-secondary" aria-label={`Move ${field.label} down`} disabled={field.archived || index() >= (fields()?.filter((entry) => !entry.archived).length ?? 0) - 1} onClick={() => void reorder(field, 1)}>↓</button></td>
        <td><Show when={field.archived} fallback={<button class="button-secondary" onClick={() => void fieldAction(field, "archive")}>Archive</button>}><button class="button-secondary" onClick={() => void fieldAction(field, "unarchive")}>Unarchive</button></Show><button class="button-secondary" disabled={field.value_count > 0} onClick={() => void fieldAction(field, "delete")}>Delete</button></td>
      </tr>}</For></tbody></Table>
    </Show>
    <Drawer open={creating() || editing() !== null} title={creating() ? "New field" : "Edit field"} onClose={() => { setCreating(false); setEditing(null); }}>
      <form class="page-drawer-content" onSubmit={(event) => void save(event)}>
        <label class="field">Key<input aria-label="Field key" required pattern="^[a-z][a-z0-9_]{0,47}$" title="Use lowercase letters, numbers, and underscores." value={key()} disabled={!creating()} onInput={(event) => setKey(event.currentTarget.value)} /></label>
        <label class="field">Label<input aria-label="Field label" required maxLength={80} value={label()} onInput={(event) => setLabel(event.currentTarget.value)} /></label>
        <label class="field">Description<textarea aria-label="Field description" maxLength={500} value={description()} onInput={(event) => setDescription(event.currentTarget.value)} /></label>
        <label class="field">Type<select aria-label="Field type" value={fieldType()} disabled={!creating() && (editing()?.value_count ?? 0) > 0} onChange={(event) => setFieldType(event.currentTarget.value as DataFieldType)}><For each={fieldTypes}>{(type) => <option value={type}>{type}</option>}</For></select></label>
        <Show when={fieldType() === "single_select" || fieldType() === "multi_select"}><label class="field">Options (one value|label per line)<textarea aria-label="Options" value={options()} onInput={(event) => setOptions(event.currentTarget.value)} /></label></Show>
        <label class="field"><input type="checkbox" aria-label="Required" checked={required()} onChange={(event) => setRequired(event.currentTarget.checked)} /> Required</label>
        <label class="field">Role<select aria-label="Field role" value={role()} onChange={(event) => setRole(event.currentTarget.value as DataFieldRole | "")}><option value="">No role</option><For each={roles.filter((candidate) => candidate === "purpose" ? ["text", "long_text", "single_select", "multi_select"].includes(fieldType()) : candidate === "owner" ? fieldType() === "reference" : fieldType() === "date")}>{(value) => <option value={value}>{value}</option>}</For></select></label>
        <button class="button-primary" type="submit">Save field</button>
      </form>
    </Drawer>
  </section></Show>;
}
