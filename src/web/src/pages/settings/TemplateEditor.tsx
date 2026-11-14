import { For, Show, createEffect, createSignal, on, type JSX } from "solid-js";
import { ApiError, TemplateValidationError, templatesApi } from "../../api/client";
import type { TemplatePreview as TemplatePreviewData, TemplateValidationErrorItem, TemplateVersionText } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Button, Select } from "../../components/ui";
import { TemplatePreview } from "./TemplatePreview";
import { EMPTY_DRAFT, EVENT_KINDS, TEMPLATE_FIELDS, asApiError, draftFromVersion, draftPayload, type TemplateDraft, type TemplateField } from "./templateText";

type EditorProps = Readonly<{
  mode: "create" | "version";
  name?: string;
  eventKind?: string;
  initial?: TemplateVersionText;
  variables: readonly string[];
  canWrite: boolean;
  onSaved: (saved: Readonly<{ name: string; version: number }>) => void;
  onCancel?: () => void;
}>;

/** Template text editor. Drafts live only in memory; validate and preview send the current text. */
export function TemplateEditor(props: EditorProps): JSX.Element {
  const [name, setName] = createSignal("");
  const [eventKind, setEventKind] = createSignal("workflow_notify");
  const [draft, setDraft] = createSignal<TemplateDraft>(EMPTY_DRAFT);
  const [fieldErrors, setFieldErrors] = createSignal<readonly TemplateValidationErrorItem[]>([]);
  const [preview, setPreview] = createSignal<TemplatePreviewData | null>(null);
  const [error, setError] = createSignal<ApiError | null>(null);
  const [notice, setNotice] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const [focused, setFocused] = createSignal<TemplateField>("body");
  const areas = new Map<TemplateField, HTMLTextAreaElement>();
  createEffect(on(() => props.initial, (initial) => {
    if (initial === undefined) return;
    setDraft(draftFromVersion(initial)); setFieldErrors([]); setPreview(null); setError(null); setNotice("");
  }));

  const currentName = (): string => props.mode === "create" ? name().trim() : props.name ?? "";
  const currentKind = (): string => props.mode === "create" ? eventKind() : props.eventKind ?? "workflow_notify";
  const errorsFor = (field: string): readonly TemplateValidationErrorItem[] => fieldErrors().filter((item) => item.field === field);
  const otherErrors = (): readonly TemplateValidationErrorItem[] => fieldErrors().filter((item) => !TEMPLATE_FIELDS.some((spec) => spec.field === item.field));
  const update = (field: TemplateField, value: string): void => { setDraft({ ...draft(), [field]: value }); };
  const insertVariable = (path: string): void => {
    const field = focused();
    const text = draft()[field];
    const area = areas.get(field);
    const start = area?.selectionStart ?? text.length;
    const end = area?.selectionEnd ?? text.length;
    const token = `{{ ${path} }}`;
    update(field, text.slice(0, start) + token + text.slice(end));
    if (area !== undefined) { area.focus(); area.setSelectionRange(start + token.length, start + token.length); }
  };
  const run = async (action: () => Promise<void>, detail: string): Promise<void> => {
    setBusy(true); setError(null); setNotice("");
    try { await action(); }
    catch (cause) {
      if (cause instanceof TemplateValidationError) { setFieldErrors(cause.errors); setNotice("Review the validation errors."); }
      else setError(asApiError(cause, detail));
    } finally { setBusy(false); }
  };
  const validate = (): Promise<void> => run(async () => {
    const result = await templatesApi.validate({ event_kind: currentKind(), ...draftPayload(draft()) });
    setFieldErrors(result.errors); setNotice(result.valid ? "Template is valid." : "Review the validation errors.");
  }, "Validation could not be completed.");
  const showPreview = (): Promise<void> => run(async () => {
    setPreview(await templatesApi.preview({ event_kind: currentKind(), ...draftPayload(draft()) }));
  }, "The preview could not be rendered.");
  const save = (): Promise<void> => run(async () => {
    const saved = await templatesApi.create({ name: currentName(), event_kind: currentKind(), ...draftPayload(draft()) });
    setFieldErrors([]); setNotice(`Saved as version ${saved.version} (inactive)`);
    props.onSaved(saved);
  }, "The template could not be saved.");

  return <div class="template-editor">
    <Show when={error()} keyed>{(failure) => <ErrorBanner error={failure} />}</Show>
    <Show when={notice()}>{(message) => <p class="notice" role="status">{message()}</p>}</Show>
    <Show when={otherErrors().length > 0}><ul class="validation-errors" aria-label="Validation errors"><For each={otherErrors()}>{(item) => <li><code>{item.field}</code>: {item.code}</li>}</For></ul></Show>
    <div class="template-editor-columns">
      <div class="template-fields">
        <Show when={props.mode === "create"}>
          <label class="field">Name<input value={name()} placeholder="team.alert" onInput={(event) => setName(event.currentTarget.value)} required /></label>
          <label class="field">Event kind<Select aria-label="Event kind" value={eventKind()} onChange={(event) => setEventKind(event.currentTarget.value)}><For each={EVENT_KINDS}>{(kind) => <option value={kind}>{kind}</option>}</For></Select></label>
        </Show>
        <For each={TEMPLATE_FIELDS}>{(spec) => <div class="template-field">
          <label class="field">{spec.label}<textarea ref={(element) => areas.set(spec.field, element)} class="template-textarea" rows={spec.rows} spellcheck={false} value={draft()[spec.field]} onFocus={() => setFocused(spec.field)} onInput={(event) => update(spec.field, event.currentTarget.value)} /></label>
          <For each={errorsFor(spec.field)}>{(item) => <p class="field-error" role="alert" data-field={spec.field}>{spec.label}: {item.code}</p>}</For>
        </div>}</For>
        <div class="button-row">
          <Button disabled={busy()} onClick={() => void validate()}>Validate</Button>
          <Button disabled={busy()} onClick={() => void showPreview()}>Preview</Button>
          <Show when={props.canWrite}><Button variant="primary" disabled={busy() || currentName().length === 0} onClick={() => void save()}>Save as new version</Button></Show>
          <Show when={props.onCancel}>{(cancel) => <Button variant="transparent" disabled={busy()} onClick={() => cancel()()}>Cancel</Button>}</Show>
        </div>
      </div>
      <Show when={props.variables.length > 0}>
        <aside class="template-variables" aria-label="Allowed variables"><h3>Allowed variables</h3><ul><For each={props.variables}>{(path) => <li><Button size="sm" variant="transparent" onClick={() => insertVariable(path)}><code>{path}</code></Button></li>}</For></ul></aside>
      </Show>
    </div>
    <Show when={preview()}>{(value) => <TemplatePreview preview={value()} />}</Show>
  </div>;
}
