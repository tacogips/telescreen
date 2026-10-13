import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { dataApi, directoryApi } from "../../api/client";
import type { DataFieldSlot, JsonValue, Member, Team } from "../../api/types";
import { ApiError } from "../../api/client";

const errorText: Readonly<Record<string, string>> = {
  required: "This field is required.", invalid_type: "Enter a value with the correct type.",
  invalid_option: "Choose a listed option.", too_long: "This value is too long.",
  unknown_field: "This field is no longer available.", archived_field: "This field is archived.",
  unknown_reference: "Choose an active member or team.", raw_value_detected: "This value contains sensitive data and cannot be saved.",
};
type Props = Readonly<{ fields: readonly DataFieldSlot[]; onSave(values: readonly Readonly<{ key: string; value_json: string | null }>[]): Promise<void>; disabled?: boolean; submitLabel?: string }>;

function initialValue(slot: DataFieldSlot): string {
  if (slot.value_json === null) return "";
  try {
    const value: JsonValue = JSON.parse(slot.value_json) as JsonValue;
    return typeof value === "string" ? value : typeof value === "boolean" ? String(value) : Array.isArray(value) ? value.join(",") : String(value);
  } catch { return ""; }
}
function typedValue(slot: DataFieldSlot, raw: string): JsonValue | null {
  if (raw === "") return null;
  if (slot.field_type === "boolean") return raw === "true";
  if (slot.field_type === "integer") return Number(raw);
  if (slot.field_type === "multi_select") return raw.split(",").map((item) => item.trim()).filter(Boolean);
  return raw;
}

/** Edits field values only after server-side validation returns valid. */
export function MetadataForm(props: Props): JSX.Element {
  const [values, setValues] = createSignal<Record<string, string>>(Object.fromEntries(props.fields.map((slot) => [slot.key, initialValue(slot)])));
  const [errors, setErrors] = createSignal<readonly Readonly<{ field: string; code: string }>[]>([]);
  const [saveError, setSaveError] = createSignal(false);
  const [saving, setSaving] = createSignal(false);
  const [directory] = createResource(async (): Promise<Readonly<{ members: Member[]; teams: Team[] }>> => {
    const [members, teams] = await Promise.all([directoryApi.members(), directoryApi.teams()]);
    return { members: members.items.filter((member) => member.active), teams: teams.items };
  });
  const update = (key: string, value: string): void => { setValues((previous) => ({ ...previous, [key]: value })); };
  const submit = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault(); setErrors([]); setSaveError(false); setSaving(true);
    try {
      const typed = Object.fromEntries(props.fields.map((slot) => [slot.key, typedValue(slot, values()[slot.key] ?? "")]).filter((entry): entry is [string, JsonValue] => entry[1] !== null));
      const validation = await dataApi.validateValues({ values: typed });
      if (!validation.valid) { setErrors(validation.errors); return; }
      await props.onSave(props.fields.map((slot) => ({ key: slot.key, value_json: typedValue(slot, values()[slot.key] ?? "") === null ? null : JSON.stringify(typedValue(slot, values()[slot.key] ?? "")) })));
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 422) setErrors([{ field: "", code: failure.code === "raw_value_detected" ? "raw_value_detected" : failure.code === "required_field_missing" ? "required" : "invalid_type" }]);
      else setSaveError(true);
    } finally { setSaving(false); }
  };
  const fieldError = (key: string): string | undefined => errorText[errors().find((error) => error.field === key || error.field.endsWith(`/${key}`))?.code ?? ""];
  return <form class="data-metadata-form" onSubmit={(event) => void submit(event)}>
    <For each={props.fields}>{(slot) => <label class="field">{slot.label}{slot.required ? " *" : ""}
      <Show when={slot.field_type === "long_text"} fallback={<Show when={slot.field_type === "boolean"} fallback={<Show when={slot.field_type === "multi_select"} fallback={<Show when={slot.field_type === "single_select"} fallback={<Show when={slot.field_type === "reference"} fallback={<input type={slot.field_type === "date" ? "date" : slot.field_type === "url" ? "url" : slot.field_type === "integer" ? "number" : "text"} step={slot.field_type === "decimal" ? "any" : undefined} aria-label={slot.label} value={values()[slot.key] ?? ""} disabled={props.disabled} onInput={(event) => update(slot.key, event.currentTarget.value)} />}> <select aria-label={slot.label} value={values()[slot.key] ?? ""} disabled={props.disabled} onChange={(event) => update(slot.key, event.currentTarget.value)}><option value="">Choose…</option><Show when={directory()}>{(entries) => <><For each={entries().members}>{(member) => <option value={`member:${member.handle}`}>{member.display_name}</option>}</For><For each={entries().teams}>{(team) => <option value={`team:${team.slug}`}>{team.name}</option>}</For></>}</Show></select></Show>}> <select aria-label={slot.label} value={values()[slot.key] ?? ""} disabled={props.disabled} onChange={(event) => update(slot.key, event.currentTarget.value)}><option value="">Choose…</option><For each={slot.options}>{(option) => <option value={option.value}>{option.label}</option>}</For></select></Show>}> <select aria-label={slot.label} multiple disabled={props.disabled} onChange={(event) => update(slot.key, [...event.currentTarget.selectedOptions].map((option) => option.value).join(","))}><For each={slot.options}>{(option) => <option value={option.value}>{option.label}</option>}</For></select></Show>}> <input type="checkbox" aria-label={slot.label} checked={values()[slot.key] === "true"} disabled={props.disabled} onChange={(event) => update(slot.key, String(event.currentTarget.checked))} /></Show>}> <textarea aria-label={slot.label} value={values()[slot.key] ?? ""} disabled={props.disabled} onInput={(event) => update(slot.key, event.currentTarget.value)} /></Show>
      <Show when={fieldError(slot.key)}>{(message) => <small class="field-error">{message()}</small>}</Show>
      <Show when={(values()[slot.key] ?? "") !== ""}><button type="button" class="text-button" disabled={props.disabled} onClick={() => update(slot.key, "")}>Clear {slot.label}</button></Show>
    </label>}</For>
    <Show when={errors().some((error) => error.field === "")}><p role="alert">{errorText[errors().find((error) => error.field === "")?.code ?? ""] ?? "Some fields need attention."}</p></Show>
    <Show when={saveError()}><p role="alert">Field values could not be saved.</p></Show>
    <button class="button-primary" type="submit" disabled={props.disabled || saving()}>{saving() ? "Saving…" : props.submitLabel ?? "Save values"}</button>
  </form>;
}
