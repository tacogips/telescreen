import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, WorkflowValidationError, escalationApi } from "../../api/client";
import type { EscalationPolicy } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { JsonTextArea } from "../../components/JsonTextArea";
import { Drawer, EmptyState, Panel, PanelItem, Placeholder, Table, Tag, showToast } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";

function resourceError(failure: unknown): ApiError | null {
  if (failure === undefined || failure === null) return null;
  return failure instanceof ApiError ? failure : new ApiError(0, "request_failed", "The request could not be completed.");
}

type Tier = Readonly<{ after: string; assign_to: string; notify: string[]; channels: string[] }>;
type EscalationDocument = Readonly<{ name: string; tiers: Tier[] }>;
type UnknownRecord = Readonly<Record<string, unknown>>;
type ValidationIssue = Readonly<{ path: string; code: string }>;

const DURATION_PATTERN = /^P(?:([0-9]+)W|([0-9]+)D)?(?:T(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+(?:\.[0-9]+)?)S)?)?$/;
const RECIPIENT_PATTERN = /^(member|team|email|slack):.+$/;
const CHANNELS = new Set<string>(["slack", "email"]);

const DEFAULT_SOURCE = JSON.stringify({
  name: "",
  tiers: [{ after: "PT1H", assign_to: "team:triage", notify: [], channels: ["email"] }],
}, null, 2);

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item: unknown) => typeof item === "string");
}

function validateEscalationDocument(value: unknown): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!isRecord(value)) return [{ path: "", code: "invalid_document" }];
  if (!Array.isArray(value.tiers)) return [{ path: "/tiers", code: "invalid_type" }];
  for (const [index, tier] of value.tiers.entries()) {
    const path: string = `/tiers/${index}`;
    if (!isRecord(tier)) {
      issues.push({ path, code: "invalid_type" });
      continue;
    }
    if (typeof tier.after !== "string" || !isValidDuration(tier.after)) {
      issues.push({ path: `${path}/after`, code: "invalid_duration" });
    }
    if (typeof tier.assign_to !== "string") {
      issues.push({ path: `${path}/assign_to`, code: "missing_field" });
    }
    const assignTo: string = typeof tier.assign_to === "string" ? tier.assign_to : "";
    if (!RECIPIENT_PATTERN.test(assignTo)) {
      issues.push({ path: `${path}/assign_to`, code: "invalid_value" });
    }
    validateStringArray(tier.notify, `${path}/notify`, issues);
    if (Array.isArray(tier.notify)) {
      for (const [notifyIndex, recipient] of tier.notify.entries()) {
        if (typeof recipient === "string" && !RECIPIENT_PATTERN.test(recipient)) {
          issues.push({ path: `${path}/notify/${notifyIndex}`, code: "invalid_value" });
        }
      }
    }
    validateStringArray(tier.channels, `${path}/channels`, issues);
    if (Array.isArray(tier.channels)) {
      for (const [channelIndex, channel] of tier.channels.entries()) {
        if (typeof channel === "string" && !CHANNELS.has(channel)) {
          issues.push({ path: `${path}/channels/${channelIndex}`, code: "invalid_value" });
        }
      }
    }
  }
  return issues;
}

function isValidDuration(value: string): boolean {
  return DURATION_PATTERN.test(value) && value !== "P" && value !== "PT" && !value.endsWith("T");
}

function validateStringArray(value: unknown, path: string, issues: ValidationIssue[]): void {
  if (!Array.isArray(value)) {
    issues.push({ path, code: "invalid_type" });
    return;
  }
  for (const [index, item] of value.entries()) {
    if (typeof item !== "string") issues.push({ path: `${path}/${index}`, code: "invalid_type" });
  }
}

function parseEscalationDocument(source: string): EscalationDocument | null {
  try {
    const value: unknown = JSON.parse(source);
    if (!isRecord(value) || typeof value.name !== "string" || !Array.isArray(value.tiers)) return null;
    const tiers: Tier[] = [];
    for (const item of value.tiers) {
      if (!isRecord(item) || typeof item.after !== "string" || typeof item.assign_to !== "string" ||
        !isStringArray(item.notify) || !isStringArray(item.channels)) return null;
      tiers.push({ after: item.after, assign_to: item.assign_to, notify: item.notify, channels: item.channels });
    }
    return { name: value.name, tiers };
  } catch { return null; }
}

function sourceWithName(source: string, name: string): string | null {
  try {
    const value: unknown = JSON.parse(source);
    return isRecord(value) ? JSON.stringify({ ...value, name }) : source;
  } catch { return null; }
}

export default function EscalationPage() {
  const [policies, { refetch }] = createResource(async () => (await escalationApi.list()).items);
  const [error, setError] = createSignal<ApiError | null>(null);
  const [selected, setSelected] = createSignal<EscalationPolicy | null>(null);
  const [formOpen, setFormOpen] = createSignal(false);
  const [name, setName] = createSignal("");
  const [source, setSource] = createSignal(DEFAULT_SOURCE);
  const [errors, setErrors] = createSignal<Readonly<{ path: string; code: string }[]>>([]);

  function edit(policy: EscalationPolicy | null): void {
    setFormOpen(true);
    setSelected(policy); setName(policy?.name ?? ""); setSource(policy?.tiers_json ?? DEFAULT_SOURCE); setErrors([]); setError(null);
  }

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault(); setError(null); setErrors([]);
    let parsedSource: unknown;
    try {
      parsedSource = JSON.parse(source());
    } catch {
      setErrors([{ path: "$", code: "invalid_json" }]);
      return;
    }
    const localErrors: ValidationIssue[] = validateEscalationDocument(parsedSource);
    if (localErrors.length > 0) {
      setErrors(localErrors);
      return;
    }
    const policyName: string = selected()?.name ?? name();
    const tiersJson: string | null = sourceWithName(source(), policyName);
    if (tiersJson === null) { setErrors([{ path: "$", code: "invalid_json" }]); return; }
    try {
      if (selected() === null) await escalationApi.create(policyName, tiersJson, "json");
      else await escalationApi.update(policyName, tiersJson, "json");
      setFormOpen(false); setSelected(null); await refetch(); showToast("Escalation policy saved", "success");
    } catch (caught) {
      if (caught instanceof WorkflowValidationError) setErrors(caught.errors);
      else setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed."));
    }
  }

  return <section class="page-content">
    <TopBar.Slot name="title">Escalation policies</TopBar.Slot>
    <TopBar.Slot name="actions"><button class="button-primary" onClick={() => edit(null)}>New policy</button></TopBar.Slot>
    <Show when={error() ?? resourceError(policies.error)}>{(failure) => <ErrorBanner error={failure()} />}</Show>
    <Show when={!policies.loading} fallback={<Placeholder label="Loading escalation policies…" />}>
      <Show when={(policies()?.length ?? 0) > 0} fallback={<EmptyState title="No escalation policies"><p>Define response tiers to route incidents when a task waits too long.</p></EmptyState>}>
        <Table><thead><tr><th>Policy</th><th>Tiers</th><th>Recipients</th><th>Source</th><th /></tr></thead><tbody>
          <For each={policies()}>{(policy) => {
            const document = parseEscalationDocument(policy.tiers_json);
            return <tr><td><strong>{policy.name}</strong></td>
              <td>{document === null ? <Tag variant="warning">Invalid policy</Tag> : <For each={document.tiers}>{(tier, index) => <Tag>Tier {index() + 1} · {tier.after} · {tier.assign_to}</Tag>}</For>}</td>
              <td>{document === null ? "—" : document.tiers.flatMap((tier) => [tier.assign_to, ...tier.notify]).join(", ") || "—"}</td>
              <td><code>{policy.tiers_json}</code></td><td><button class="button-secondary" onClick={() => edit(policy)}>Edit</button></td>
            </tr>;
          }}</For>
        </tbody></Table>
      </Show>
    </Show>
    <Drawer open={formOpen()} title={selected() === null ? "New escalation policy" : `Edit ${selected()!.name}`} onClose={() => setFormOpen(false)}>
      <form class="page-drawer-content" onSubmit={(event) => void save(event)}>
        <label class="field">Policy name<input value={name()} onInput={(event) => setName(event.currentTarget.value)} required disabled={selected() !== null} /></label>
        <Panel title="Escalation tiers"><For each={parseEscalationDocument(source())?.tiers ?? []}>{(tier, index) => <PanelItem><strong>Tier {index() + 1}</strong><span>After {tier.after}</span><span>Assign to {tier.assign_to}</span><span>Notify {tier.notify.join(", ") || "—"}</span><span>Channels {tier.channels.join(", ") || "—"}</span></PanelItem>}</For></Panel>
        <JsonTextArea label="JSON source" value={source()} onInput={setSource} errors={errors()} />
        <div class="row-actions"><button class="button-primary" type="submit">Save policy</button><button class="button-secondary" type="button" onClick={() => setFormOpen(false)}>Cancel</button></div>
      </form>
    </Drawer>
  </section>;
}
