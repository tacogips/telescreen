import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, apiFetch, collectorsApi, mapJobsApi } from "../../api/client";
import type { CollectorConfig, MapJob } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { JsonTextArea } from "../../components/JsonTextArea";
import { StatusBadge } from "../../components/StatusBadge";

type ConfigSweeps = Readonly<{ config: CollectorConfig; job: MapJob | null }>;

export default function CollectorsPage() {
  const [configs, { refetch }] = createResource(async () => (await collectorsApi.list()).items);
  const [sweeps] = createResource(configs, async (items): Promise<ConfigSweeps[]> => Promise.all(items.map(async (config) => {
    const filters = new URLSearchParams({ collector_config_id: config.id });
    const jobs = (await mapJobsApi.list(filters)).items;
    return { config, job: jobs[0] ?? null };
  })));
  const [error, setError] = createSignal<ApiError | null>(null);
  const [sweepConflict, setSweepConflict] = createSignal(false);
  const [editing, setEditing] = createSignal<CollectorConfig | null>(null);
  const [name, setName] = createSignal("");
  const [kind, setKind] = createSignal("slack");
  const [triggerMode, setTriggerMode] = createSignal("schedule");
  const [cron, setCron] = createSignal("");
  const [sweepCron, setSweepCron] = createSignal("");
  const [credentialRef, setCredentialRef] = createSignal("");
  const [selector, setSelector] = createSignal("{}");
  const [selectorErrors, setSelectorErrors] = createSignal<Readonly<{ path: string; code: string }[]>>([]);

  function startEdit(config: CollectorConfig): void {
    setEditing(config);
    setName(config.name);
    setKind(config.kind);
    setTriggerMode(config.trigger_mode);
    setCron(config.cron ?? "");
    setSweepCron(config.sweep_cron ?? "");
    setCredentialRef(config.credential_ref ?? "");
    setSelector(config.selector_json);
  }

  function clearForm(): void {
    setEditing(null); setName(""); setKind("slack"); setTriggerMode("schedule");
    setCron(""); setSweepCron(""); setCredentialRef(""); setSelector("{}"); setSelectorErrors([]);
  }

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault(); setError(null); setSelectorErrors([]);
    if (credentialRef() !== "" && !/^(env|file):/.test(credentialRef())) {
      setError(new ApiError(0, "invalid_credential_ref", "Use an env: or file: secret reference.")); return;
    }
    try {
      JSON.parse(selector());
    } catch {
      setSelectorErrors([{ path: "$", code: "invalid_json" }]); return;
    }
    const value = { name: name(), kind: kind(), trigger_mode: triggerMode(), selector_json: selector(), cron: cron() || null, sweep_cron: sweepCron() || null, credential_ref: credentialRef() || null };
    try {
      if (editing() === null) {
        await apiFetch<CollectorConfig>("/api/collectors", { method: "POST", json: value });
      } else {
        await apiFetch<CollectorConfig>(`/api/collectors/${encodeURIComponent(editing()!.id)}`, { method: "PATCH", json: { trigger_mode: value.trigger_mode, selector_json: value.selector_json, cron: value.cron, sweep_cron: value.sweep_cron, credential_ref: value.credential_ref } });
      }
      clearForm(); await refetch();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed."));
    }
  }

  async function run(id: string, sweep: boolean): Promise<void> {
    setError(null); setSweepConflict(false);
    try {
      if (sweep) await collectorsApi.sweep(id); else await collectorsApi.run(id);
      await refetch();
    } catch (caught) {
      if (sweep && caught instanceof ApiError && caught.status === 409) {
        setSweepConflict(true);
        setError(null);
      } else {
        setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed."));
      }
    }
  }

  function progress(job: MapJob | null): number {
    return job === null || job.partitions_total === 0 ? 0 : Math.min(100, Math.round(100 * job.partitions_done / job.partitions_total));
  }

  return (
    <section>
      <h1>Collectors</h1>
      <ErrorBanner error={error()} />
      <Show when={sweepConflict()}><p role="alert">A sweep is already running.</p></Show>
      <Show when={!configs.loading} fallback={<p>Loading collectors…</p>}>
        <table><thead><tr><th>Name</th><th>Kind</th><th>Trigger</th><th>Cron</th><th>Sweep cron</th><th>Enabled</th><th>Latest sweep</th><th>Actions</th></tr></thead>
          <tbody><For each={sweeps()} fallback={<tr><td colspan="8">No collectors configured.</td></tr>}>
            {(entry) => <tr>
              <td>{entry.config.name}</td><td>{entry.config.kind}</td><td>{entry.config.trigger_mode}</td><td>{entry.config.cron ?? "—"}</td><td>{entry.config.sweep_cron ?? "—"}</td><td>{entry.config.enabled ? "Yes" : "No"}</td>
              <td><Show when={entry.job} fallback={"—"}><StatusBadge status={entry.job!.status} /> <progress max="100" value={progress(entry.job!)} /> {progress(entry.job!)}%</Show></td>
              <td><button class="button-secondary" onClick={() => void run(entry.config.id, false)}>Run now</button> <button class="button-secondary" onClick={() => void run(entry.config.id, true)}>Sweep now</button> <button class="button-secondary" onClick={() => startEdit(entry.config)}>Edit</button></td>
            </tr>}
          </For></tbody>
        </table>
      </Show>
      <h2>{editing() === null ? "Add collector" : `Edit ${editing()!.name}`}</h2>
      <form onSubmit={(event) => void save(event)}>
        <label class="field">Name<input value={name()} onInput={(event) => setName(event.currentTarget.value)} required /></label>
        <label class="field">Kind<select value={kind()} onChange={(event) => setKind(event.currentTarget.value)}><For each={["slack", "gdrive", "gdocs", "gsheets", "github", "custom", "police"]}>{(option) => <option value={option}>{option}</option>}</For></select></label>
        <label class="field">Trigger mode<select value={triggerMode()} onChange={(event) => setTriggerMode(event.currentTarget.value)}><option value="event">Event</option><option value="schedule">Schedule</option><option value="both">Both</option></select></label>
        <label class="field">Cron<input value={cron()} onInput={(event) => setCron(event.currentTarget.value)} placeholder="0 * * * *" /></label>
        <label class="field">Sweep cron<input value={sweepCron()} onInput={(event) => setSweepCron(event.currentTarget.value)} placeholder="0 2 * * *" /></label>
        <label class="field">Credential reference<input value={credentialRef()} onInput={(event) => setCredentialRef(event.currentTarget.value)} placeholder="env:COLLECTOR_TOKEN" /><small>Use env: or file: references; enter no secret value.</small></label>
        <JsonTextArea label="Selector JSON" value={selector()} onInput={setSelector} errors={selectorErrors()} />
        <button class="button-secondary" type="submit">Save collector</button> {editing() !== null && <button class="button-secondary" type="button" onClick={clearForm}>Cancel edit</button>}
      </form>
    </section>
  );
}
