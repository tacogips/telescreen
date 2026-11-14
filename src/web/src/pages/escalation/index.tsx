import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, escalationApi } from "../../api/client";
import type { EscalationPolicy, EscalationTier } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { JsonTextArea } from "../../components/JsonTextArea";

export default function EscalationPage() {
  const [policies, { refetch }] = createResource(async () => (await escalationApi.list()).items);
  const [error, setError] = createSignal<ApiError | null>(null);
  const [name, setName] = createSignal("");
  const [selected, setSelected] = createSignal<string | null>(null);
  const [tiersText, setTiersText] = createSignal("[{\"after_seconds\":3600,\"recipients\":[]}]");
  const [errors, setErrors] = createSignal<Readonly<{ path: string; code: string }[]>>([]);

  function edit(policy: EscalationPolicy): void {
    setSelected(policy.name); setName(policy.name); setTiersText(JSON.stringify(policy.tiers, null, 2)); setErrors([]);
  }

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault(); setError(null); setErrors([]);
    let parsed: unknown;
    try { parsed = JSON.parse(tiersText()); } catch { setErrors([{ path: "$", code: "invalid_json" }]); return; }
    if (!Array.isArray(parsed) || !parsed.every(isTier)) { setErrors([{ path: "$", code: "expected_escalation_tiers" }]); return; }
    const tiers: EscalationTier[] = parsed;
    try {
      if (selected() === null) await escalationApi.create({ name: name(), tiers });
      else await escalationApi.update(selected()!, { tiers });
      setSelected(null); setName(""); await refetch();
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "validation_error") setErrors([{ path: "$", code: "validation_error" }]);
      else setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed."));
    }
  }

  return <section>
    <h1>Escalation policies</h1><ErrorBanner error={error()} />
    <Show when={!policies.loading} fallback={<p>Loading policies…</p>}>
      <table><thead><tr><th>Name</th><th>Tiers</th><th>Updated</th><th /></tr></thead><tbody>
        <For each={policies()} fallback={<tr><td colspan="4">No escalation policies configured.</td></tr>}>
          {(policy) => <tr><td>{policy.name}</td><td>{policy.tiers.map((tier) => `${tier.after_seconds}s: ${tier.recipients.join(", ")}`).join(" → ")}</td><td>{policy.updated_at}</td><td><button class="button-secondary" onClick={() => edit(policy)}>Edit</button></td></tr>}
        </For>
      </tbody></table>
    </Show>
    <h2>{selected() === null ? "Add policy" : `Edit ${selected()}`}</h2>
    <form onSubmit={(event) => void save(event)}>
      <label class="field">Policy name<input value={name()} onInput={(event) => setName(event.currentTarget.value)} required disabled={selected() !== null} /></label>
      <JsonTextArea label="Escalation tiers JSON" value={tiersText()} onInput={setTiersText} errors={errors()} />
      <button class="button-secondary" type="submit">Save policy</button> {selected() !== null && <button class="button-secondary" type="button" onClick={() => { setSelected(null); setName(""); }}>Cancel</button>}
    </form>
  </section>;
}

function isTier(value: unknown): value is EscalationTier {
  if (typeof value !== "object" || value === null || !("after_seconds" in value) || !("recipients" in value)) return false;
  return typeof value.after_seconds === "number" && Array.isArray(value.recipients) && value.recipients.every((recipient: unknown) => typeof recipient === "string");
}
