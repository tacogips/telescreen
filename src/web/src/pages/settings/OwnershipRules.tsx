import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { ApiError, apiFetch, directoryApi, ownershipApi } from "../../api/client";
import { TopBar } from "../../components/shell/TopBar";
import { Panel, Table } from "../../components/ui";

type OwnershipRuleView = Readonly<{ id: string; position: number; source: string | null; pattern: string; team: string | null; enabled: boolean }>;
type RuleForm = Readonly<{ id: string | null; source: string; pattern: string; team: string; enabled: boolean }>;
type OwnershipTestResult = Readonly<{ rule_id: string | null; team: string | null }>;
const SOURCES = ["slack", "gdrive", "gdocs", "gsheets", "github", "custom", "police"] as const;

/** Ordered ownership routing rules and a transient masked-locator test. */
export function OwnershipRules(): JSX.Element {
  const [rules, { mutate, refetch }] = createResource(async () => (await apiFetch<Readonly<{ items: OwnershipRuleView[] }>>("/api/ownership-rules")).items);
  const [teams] = createResource(async () => (await directoryApi.teams()).items);
  const [form, setForm] = createSignal<RuleForm | null>(null);
  const [locator, setLocator] = createSignal("");
  const [testSource, setTestSource] = createSignal("slack");
  const [testResult, setTestResult] = createSignal<string | null>(null);
  const [error, setError] = createSignal<string | null>(null);
  const save = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault(); const value = form(); if (value === null) return;
    setError(null);
    try {
      if (value.id === null) await apiFetch<OwnershipRuleView>("/api/ownership-rules", { method: "POST", json: { source: value.source || null, pattern: value.pattern, team: value.team, enabled: value.enabled } });
      else await apiFetch<OwnershipRuleView>(`/api/ownership-rules/${encodeURIComponent(value.id)}`, { method: "PUT", json: { source: value.source || null, pattern: value.pattern, team: value.team, enabled: value.enabled } });
      setForm(null); await refetch();
    } catch (cause) { setError(cause instanceof ApiError && cause.status === 422 ? "The value looks like a secret and was not saved" : "The ownership rule could not be saved."); }
  };
  const reorder = async (rule: OwnershipRuleView, offset: -1 | 1): Promise<void> => {
    const current = [...(rules() ?? [])].sort((left, right) => left.position - right.position);
    const index = current.findIndex((item) => item.id === rule.id); const target = index + offset;
    if (target < 0 || target >= current.length) return;
    [current[index], current[target]] = [current[target]!, current[index]!];
    try { await ownershipApi.reorder(current.map((item) => item.id)); mutate(current.map((item, position) => ({ ...item, position }))); }
    catch { setError("The ownership rules could not be reordered."); }
  };
  const testLocator = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault(); const value = locator(); setLocator(""); setTestResult(null); setError(null);
    try { const result = await apiFetch<OwnershipTestResult>("/api/ownership-rules/test", { method: "POST", json: { source: testSource(), locator: value } }); setTestResult(result.team ?? "No match"); }
    catch (cause) { setError(cause instanceof ApiError && cause.status === 422 ? "The value looks like a secret and was not saved" : "The locator could not be tested."); }
  };
  const beginEdit = (rule: OwnershipRuleView): void => { setForm({ id: rule.id, source: rule.source ?? "", pattern: rule.pattern, team: rule.team ?? "", enabled: rule.enabled }); };
  return <section class="page-content settings-page"><TopBar.Slot name="title">Ownership rules</TopBar.Slot>
    <Panel title="Rules"><button class="button-primary" onClick={() => setForm({ id: null, source: "", pattern: "", team: teams()?.[0]?.slug ?? "", enabled: true })}>Add rule</button>
      <Table><thead><tr><th>Order</th><th>Source</th><th>Pattern</th><th>Team</th><th>Actions</th></tr></thead><tbody><For each={[...(rules() ?? [])].sort((left, right) => left.position - right.position)}>{(rule, index) => <tr><td>{index() + 1}<button aria-label={`Move ${rule.pattern} up`} disabled={index() === 0} onClick={() => void reorder(rule, -1)}>↑</button><button aria-label={`Move ${rule.pattern} down`} disabled={index() === (rules()?.length ?? 0) - 1} onClick={() => void reorder(rule, 1)}>↓</button></td><td>{rule.source ?? "Any"}</td><td>{rule.pattern}</td><td>{teams()?.find((team) => team.slug === rule.team)?.name ?? rule.team ?? "—"}</td><td><button class="button-secondary" onClick={() => beginEdit(rule)}>Edit</button><button class="button-secondary" onClick={async () => { try { await ownershipApi.delete(rule.id); await refetch(); } catch { setError("The ownership rule could not be deleted."); } }}>Delete</button></td></tr>}</For></tbody></Table>
    </Panel>
    <Show when={form()}>{(value) => <div role="dialog" aria-modal="true" aria-label={value().id === null ? "Create ownership rule" : "Edit ownership rule"}><form class="settings-form" onSubmit={(event) => void save(event)}>
      <label class="field">Source<select aria-label="Source" value={value().source} onChange={(event) => setForm({ ...value(), source: event.currentTarget.value })}><option value="">Any source</option><For each={SOURCES}>{(source) => <option value={source}>{source}</option>}</For></select></label>
      <label class="field">Pattern<input aria-label="Pattern" value={value().pattern} onInput={(event) => setForm({ ...value(), pattern: event.currentTarget.value })} required /></label>
      <label class="field">Team<select aria-label="Team" value={value().team} onChange={(event) => setForm({ ...value(), team: event.currentTarget.value })}><For each={teams()}>{(team) => <option value={team.slug}>{team.name}</option>}</For></select></label>
      <label><input type="checkbox" checked={value().enabled} onChange={(event) => setForm({ ...value(), enabled: event.currentTarget.checked })} />Enabled</label>
      <button class="button-primary" type="submit">Save rule</button><button class="button-secondary" type="button" onClick={() => setForm(null)}>Cancel</button>
    </form></div>}</Show>
    <Panel title="Test locator"><form class="inline-form" onSubmit={(event) => void testLocator(event)}><label class="field">Source<select aria-label="Test source" value={testSource()} onChange={(event) => setTestSource(event.currentTarget.value)}><For each={SOURCES}>{(source) => <option value={source}>{source}</option>}</For></select></label><label class="field">Display locator<input aria-label="Display locator" value={locator()} onInput={(event) => setLocator(event.currentTarget.value)} /></label><button class="button-secondary" type="submit">Test locator</button></form><Show when={testResult()}>{(result) => <p role="status">{result() === "No match" ? result() : `Matched team: ${result()}`}</p>}</Show></Panel>
    <Show when={error()}>{(message) => <p class="field-error" role="alert">{message()}</p>}</Show>
  </section>;
}
