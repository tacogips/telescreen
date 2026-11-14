import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import { ApiError, TemplateValidationError, alertsApi, templatesApi, workflowsApi } from "../../api/client";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Button, Panel, Select, Tag } from "../../components/ui";
import type { AlertValidation, RichAlertRule, RuleForm } from "./alertTypes";
import { emptyRuleForm, formFromRule, splitValues } from "./alertTypes";

type Props = Readonly<{ initial?: RichAlertRule; canWrite: boolean; onSaved(): void; onCancel(): void }>;
const fieldMessages: Readonly<Record<string, string>> = {
  required: "This field is required.", invalid: "Choose a valid value.", invalid_value: "Choose a valid value.",
  invalid_length: "Enter a value with an allowed length.", raw_value_detected: "Remove sensitive values from this field.",
  threshold_required: "Enter a threshold.", window_required: "Enter a time window.", invalid_interval: "Enter a valid action interval.",
  empty_actions: "Add a notification target or start a workflow.", too_many_targets: "Remove targets so there are no more than 10.",
  invalid_recipient: "Enter a valid recipient reference.", undeliverable_combination: "Choose a recipient and channel combination that can deliver.",
  unknown_template: "Choose an available alert template.", template_event_mismatch: "Choose a template for alert fired events.",
  unknown_workflow: "Choose an active workflow.", duplicate_trigger: "Choose a workflow with a different trigger.",
};

/** Builds and validates an alert rule without placing user input in error messages. */
export function AlertEditor(props: Props): JSX.Element {
  const [form, setForm] = createSignal<RuleForm>(props.initial ? formFromRule(props.initial) : emptyRuleForm());
  const [errors, setErrors] = createSignal<readonly Readonly<{ field: string; code: string }>[]>([]);
  const [failure, setFailure] = createSignal<ApiError | null>(null);
  const [busy, setBusy] = createSignal(false);
  const [templates] = createResource(async () => (await templatesApi.list().catch(() => ({ items: [] }))).items.filter((item) => item.event_kind === "alert_fired"));
  const [workflows] = createResource(async () => (await workflowsApi.list().catch(() => ({ items: [], next_cursor: null }))).items.filter((item) => item.active));
  const needsWindow = (): boolean => form().trigger === "frequency" || form().trigger === "values";
  const update = (patch: Partial<RuleForm>): void => { setForm({ ...form(), ...patch }); };
  const updateTarget = (index: number, patch: Partial<RuleForm["actions"]["targets"][number]>): void => {
    const targets = form().actions.targets.map((target, targetIndex) => targetIndex === index ? { ...target, ...patch } : target);
    update({ actions: { ...form().actions, targets } });
  };
  const message = (code: string): string => fieldMessages[code] ?? "Check this field.";
  const errorsFor = (field: string): readonly string[] => errors().filter((entry) => entry.field === field).map((entry) => message(entry.code));
  const errorsUnder = (prefix: string): readonly string[] => errors().filter((entry) => entry.field === prefix || entry.field.startsWith(`${prefix}/`)).map((entry) => message(entry.code));
  const isRenderedError = (field: string): boolean => {
    if (["/name", "/action_interval_minutes", "/actions", "/actions/targets", "/actions/start_workflow"].includes(field)) return true;
    if (needsWindow() && ["/trigger/threshold", "/trigger/window_minutes"].includes(field)) return true;
    const targetError = /^\/actions\/targets\/(\d+)\/(recipients(?:\/\d+)?|channels\/\d+|template)$/.exec(field);
    return targetError !== null && Number(targetError[1]) < form().actions.targets.length;
  };
  const hasUnrenderedErrors = (): boolean => errors().some((entry) => !isRenderedError(entry.field));
  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault(); setFailure(null); setErrors([]); setBusy(true);
    try {
      const value = form();
      const validationInput = {
        name: value.name,
        trigger: { kind: value.trigger, threshold: value.threshold, window_minutes: value.window_minutes },
        filters: { ...value.filters, min_severity: value.filters.min_severity || null },
        actions: { targets: value.actions.targets.map((target) => ({ recipients: splitValues(target.recipient), channels: target.channels, template: target.template })), start_workflow: value.actions.start_workflow },
        action_interval_minutes: value.action_interval_minutes,
      };
      const validation = await alertsApi.validate(validationInput as unknown as Parameters<typeof alertsApi.validate>[0]) as unknown as AlertValidation;
      if (!validation.valid) { setErrors(validation.errors); return; }
      const payload = { ...validationInput, enabled: value.enabled };
      let created: Awaited<ReturnType<typeof alertsApi.create>> | undefined;
      if (props.initial) await alertsApi.update(props.initial.id, payload as unknown as Parameters<typeof alertsApi.update>[1]);
      else created = await alertsApi.create(payload as unknown as Parameters<typeof alertsApi.create>[0]);
      const savedId = props.initial?.id ?? created?.id;
      const savedEnabled = props.initial ? props.initial.enabled : (created?.enabled ?? true);
      if (savedId !== undefined && value.enabled !== savedEnabled) {
        if (value.enabled) await alertsApi.enable(savedId);
        else await alertsApi.disable(savedId);
      }
      props.onSaved();
    } catch (cause) {
      if (cause instanceof TemplateValidationError) setErrors(cause.errors);
      else setFailure(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The alert rule could not be saved."));
    } finally { setBusy(false); }
  }
  const triggerOptions = [["issue_created", "New issue"], ["issue_regressed", "Regression"], ["issue_escalating", "Escalating"], ["frequency", "Finding frequency"], ["values", "Distinct values"]] as const;
  return <form class="alert-editor" onSubmit={(event) => void save(event)}>
    <Show when={failure()} keyed>{(error) => <ErrorBanner error={error} />}</Show>
    <label class="field">Name<input aria-label="Rule name" value={form().name} onInput={(event) => update({ name: event.currentTarget.value })} required /></label>
    <For each={errorsFor("/name")}>{(text) => <small class="field-error">{text}</small>}</For>
    <label class="field">Trigger<Select aria-label="Trigger" value={form().trigger} onChange={(event) => update({ trigger: event.currentTarget.value })}><For each={triggerOptions}>{([value, label]) => <option value={value}>{label}</option>}</For></Select></label>
    <Show when={needsWindow()}><div class="form-grid">
      <label class="field">Threshold<input aria-label="Threshold" type="number" min="1" max="100000" value={form().threshold ?? ""} onInput={(event) => update({ threshold: event.currentTarget.value === "" ? null : Number(event.currentTarget.value) })} /></label>
      <label class="field">Window (minutes)<input aria-label="Window (minutes)" type="number" min="5" max="10080" value={form().window_minutes ?? ""} onInput={(event) => update({ window_minutes: event.currentTarget.value === "" ? null : Number(event.currentTarget.value) })} /></label>
    </div><For each={errorsFor("/trigger/threshold")}>{(text) => <small class="field-error">{text}</small>}</For><For each={errorsFor("/trigger/window_minutes")}>{(text) => <small class="field-error">{text}</small>}</For></Show>
    <Panel title="Filter chips"><div class="form-grid">
      <label class="field">Minimum severity<Select aria-label="Minimum severity" value={form().filters.min_severity} onChange={(event) => update({ filters: { ...form().filters, min_severity: event.currentTarget.value } })}><option value="">Any</option><option>critical</option><option>high</option><option>medium</option></Select></label>
      <label class="field">Priorities<input aria-label="Priorities" value={form().filters.priorities.join(", ")} onInput={(event) => update({ filters: { ...form().filters, priorities: splitValues(event.currentTarget.value) } })} placeholder="high, medium" /></label>
      <label class="field">Sources<input aria-label="Sources" value={form().filters.sources.join(", ")} onInput={(event) => update({ filters: { ...form().filters, sources: splitValues(event.currentTarget.value) } })} /></label>
      <label class="field">Rule classes<input aria-label="Rule classes" value={form().filters.rule_classes.join(", ")} onInput={(event) => update({ filters: { ...form().filters, rule_classes: splitValues(event.currentTarget.value) } })} /></label>
      <label class="field">Exposures<input aria-label="Exposures" value={form().filters.exposures.join(", ")} onInput={(event) => update({ filters: { ...form().filters, exposures: splitValues(event.currentTarget.value) } })} /></label>
      <label class="field">Owner teams<input aria-label="Owner teams" value={form().filters.owner_teams.join(", ")} onInput={(event) => update({ filters: { ...form().filters, owner_teams: splitValues(event.currentTarget.value) } })} /></label>
      <label class="field">Detectors<input aria-label="Detectors" value={form().filters.detectors.join(", ")} onInput={(event) => update({ filters: { ...form().filters, detectors: splitValues(event.currentTarget.value) } })} /></label>
    </div></Panel>
    <div class="filter-chips" aria-label="Selected alert filters"><For each={[["priority", form().filters.priorities], ["source", form().filters.sources], ["rule class", form().filters.rule_classes], ["exposure", form().filters.exposures], ["owner team", form().filters.owner_teams], ["detector", form().filters.detectors]] as const}>{([kind, values]) => <For each={values}>{(value) => <Tag variant="info">{kind}: {value}</Tag>}</For>}</For></div>
    <Panel title="Actions"><For each={form().actions.targets}>{(target, index) => <fieldset class="alert-target"><legend>Target {index() + 1}</legend>
      <label class="field">Recipients<input aria-label={`Recipient ${index() + 1}`} value={target.recipient} onInput={(event) => updateTarget(index(), { recipient: event.currentTarget.value })} placeholder="member:alex, team:security" /></label>
      <For each={errorsUnder(`/actions/targets/${index()}/recipients`)}>{(text) => <small class="field-error">{text}</small>}</For>
      <div class="recipient-chips" aria-label={`Selected recipients for target ${index() + 1}`}><For each={splitValues(target.recipient)}>{(recipient) => <Tag variant="info">{recipient}</Tag>}</For></div>
      <fieldset><legend>Channels</legend><For each={["email", "slack"]}>{(channel) => <label><input type="checkbox" checked={target.channels.includes(channel)} onChange={(event) => updateTarget(index(), { channels: event.currentTarget.checked ? [...target.channels, channel] : target.channels.filter((item) => item !== channel) })} />{channel}</label>}</For></fieldset>
      <For each={errorsUnder(`/actions/targets/${index()}/channels`)}>{(text) => <small class="field-error">{text}</small>}</For>
      <label class="field">Template (optional)<Select aria-label={`Template ${index() + 1}`} value={target.template ?? ""} onChange={(event) => updateTarget(index(), { template: event.currentTarget.value || null })}><option value="">Default template</option><For each={templates()}>{(template) => <option value={template.name}>{template.name}</option>}</For></Select></label>
      <For each={errorsUnder(`/actions/targets/${index()}/template`)}>{(text) => <small class="field-error">{text}</small>}</For>
      <Show when={form().actions.targets.length > 1}><Button type="button" size="sm" onClick={() => update({ actions: { ...form().actions, targets: form().actions.targets.filter((_, targetIndex) => targetIndex !== index()) } })}>Remove target {index() + 1}</Button></Show>
    </fieldset>}</For>
      <For each={errorsFor("/actions")}>{(text) => <small class="field-error">{text}</small>}</For>
      <For each={errorsFor("/actions/targets")}>{(text) => <small class="field-error">{text}</small>}</For>
      <Button type="button" size="sm" disabled={form().actions.targets.length >= 10} onClick={() => update({ actions: { ...form().actions, targets: [...form().actions.targets, { recipient: "", channels: ["email"], template: null }] } })}>Add target</Button>
      <label class="field">Start workflow<Select aria-label="Start workflow" value={form().actions.start_workflow ?? ""} onChange={(event) => update({ actions: { ...form().actions, start_workflow: event.currentTarget.value || null } })}><option value="">No workflow</option><For each={workflows()}>{(workflow) => <option value={workflow.name}>{workflow.name}</option>}</For></Select></label>
      <For each={errorsFor("/actions/start_workflow")}>{(text) => <small class="field-error">{text}</small>}</For>
    </Panel>
    <label class="field">Action interval (minutes)<input aria-label="Action interval" type="number" min="5" max="43200" value={form().action_interval_minutes} onInput={(event) => update({ action_interval_minutes: Number(event.currentTarget.value) })} /></label>
    <For each={errorsFor("/action_interval_minutes")}>{(text) => <small class="field-error">{text}</small>}</For>
    <Show when={hasUnrenderedErrors()}><small class="field-error" role="alert">Some fields need attention.</small></Show>
    <label><input type="checkbox" checked={form().enabled} onChange={(event) => update({ enabled: event.currentTarget.checked })} />Enabled</label>
    <div class="form-actions"><Button type="submit" variant="primary" busy={busy()} disabled={!props.canWrite}>{props.initial ? "Save changes" : "Create alert rule"}</Button><Button type="button" onClick={props.onCancel}>Cancel</Button><A href="/alerts">Back to alerts</A></div>
  </form>;
}
