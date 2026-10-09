import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, collectorsApi, mapJobsApi } from "../../api/client";
import type { CollectorConfig, MapJob, MapJobSummary } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { JsonTextArea } from "../../components/JsonTextArea";
import { Drawer, EmptyState, Placeholder, StatusIndicator, Table, Tag, showToast } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";

function resourceError(failure: unknown): ApiError | null {
  if (failure === undefined || failure === null) return null;
  return failure instanceof ApiError ? failure : new ApiError(0, "request_failed", "The request could not be completed.");
}

type CollectorEntry = Readonly<{ config: CollectorConfig; summary: MapJobSummary | null; job: MapJob | null }>;
type CollectorBody = Readonly<{ name: string; kind: string; trigger_mode: string; cron: string | null; sweep_cron: string | null; selector_json: string; credential_ref: string | null; enabled: boolean }>;

function bodyFor(config: CollectorConfig, enabled: boolean): CollectorBody {
  return { name: config.name, kind: config.kind, trigger_mode: config.trigger_mode, cron: config.cron, sweep_cron: config.sweep_cron, selector_json: config.selector_json, credential_ref: config.credential_ref, enabled };
}

function progress(job: MapJob | null): number {
  if (job === null) return 0;
  const done = job.partitions_done ?? 0;
  const total = job.partitions_total ?? 0;
  return total === 0 ? 0 : Math.min(100, Math.round(100 * done / total));
}

export default function CollectorsPage() {
  const [entries, { refetch }] = createResource(async (): Promise<CollectorEntry[]> => {
    const [configPage, jobsPage] = await Promise.all([collectorsApi.list(), mapJobsApi.list()]);
    return Promise.all(configPage.items.map(async (config) => {
      const matches = jobsPage.items.filter((job) => job.collector_config_id === config.id).sort((left, right) => right.created_at.localeCompare(left.created_at));
      const summary = matches[0] ?? null;
      return { config, summary, job: summary === null ? null : await mapJobsApi.get(summary.id) };
    }));
  });
  const [error, setError] = createSignal<ApiError | null>(null);
  const [sweepConflict, setSweepConflict] = createSignal(false);
  const [editing, setEditing] = createSignal<CollectorConfig | null>(null);
  const [formOpen, setFormOpen] = createSignal(false);
  const [selectedJob, setSelectedJob] = createSignal<MapJob | null>(null);
  const [name, setName] = createSignal("");
  const [kind, setKind] = createSignal("slack");
  const [triggerMode, setTriggerMode] = createSignal("schedule");
  const [cron, setCron] = createSignal("");
  const [sweepCron, setSweepCron] = createSignal("");
  const [credentialRef, setCredentialRef] = createSignal("");
  const [selector, setSelector] = createSignal("{}");
  const [selectorErrors, setSelectorErrors] = createSignal<Readonly<{ path: string; code: string }[]>>([]);

  function startEdit(config: CollectorConfig | null): void {
    setFormOpen(true);
    setEditing(config); setName(config?.name ?? ""); setKind(config?.kind ?? "slack");
    setTriggerMode(config?.trigger_mode ?? "schedule"); setCron(config?.cron ?? "");
    setSweepCron(config?.sweep_cron ?? ""); setCredentialRef(config?.credential_ref ?? "");
    setSelector(config?.selector_json ?? "{}"); setSelectorErrors([]); setError(null);
  }

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault(); setError(null); setSelectorErrors([]);
    if (credentialRef() !== "" && !/^(env|file):/.test(credentialRef())) {
      setError(new ApiError(0, "invalid_credential_ref", "Use an env: or file: secret reference.")); return;
    }
    try { JSON.parse(selector()); } catch { setSelectorErrors([{ path: "$", code: "invalid_json" }]); return; }
    const value: CollectorBody = { name: name(), kind: kind(), trigger_mode: triggerMode(), selector_json: selector(), cron: cron() || null, sweep_cron: sweepCron() || null, credential_ref: credentialRef() || null, enabled: editing()?.enabled ?? true };
    try {
      if (editing() === null) await collectorsApi.create(value);
      else await collectorsApi.update(editing()!.id, value);
      setFormOpen(false); setEditing(null); await refetch(); showToast("Collector saved", "success");
    } catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
  }

  async function toggle(config: CollectorConfig): Promise<void> {
    try { await collectorsApi.update(config.id, bodyFor(config, !config.enabled)); await refetch(); showToast(config.enabled ? "Collector disabled" : "Collector enabled", "success"); }
    catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
  }

  async function run(config: CollectorConfig, sweep: boolean): Promise<void> {
    setError(null); setSweepConflict(false);
    try {
      if (sweep) { const started = await collectorsApi.sweep(config.id); setSelectedJob(await mapJobsApi.get(started.map_job_id)); }
      else await collectorsApi.run(config.id);
      await refetch(); showToast(sweep ? "Sweep started" : "Scan started", "success");
    } catch (caught) {
      if (sweep && caught instanceof ApiError && caught.status === 409) setSweepConflict(true);
      else setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed."));
    }
  }

  return <section class="page-content">
    <TopBar.Slot name="title">Collectors</TopBar.Slot>
    <TopBar.Slot name="actions"><button class="button-primary" onClick={() => startEdit(null)}>Connect source</button></TopBar.Slot>
    <Show when={error() ?? resourceError(entries.error)}>{(failure) => <ErrorBanner error={failure()} />}</Show>
    <Show when={sweepConflict()}><p class="notice-warning" role="alert">A sweep is already running</p></Show>
    <Show when={!entries.loading} fallback={<Placeholder label="Loading collectors…" />}>
      <Show when={(entries()?.length ?? 0) > 0} fallback={<EmptyState title="No collectors configured"><p>Connect a source to begin monitoring.</p><button class="button-primary" onClick={() => startEdit(null)}>Connect a source</button></EmptyState>}>
        <Table><thead><tr><th>Collector</th><th>Trigger</th><th>Schedule</th><th>Status</th><th>Latest sweep</th><th>Actions</th></tr></thead><tbody>
          <For each={entries()}>{(entry) => <tr>
            <td><span class="collector-kind-icon" aria-hidden="true">{entry.config.kind.slice(0, 1).toUpperCase()}</span> <strong>{entry.config.name}</strong><small>{entry.config.kind}</small></td>
            <td>{entry.config.trigger_mode}</td><td>{entry.config.cron ?? "—"}<Show when={entry.config.sweep_cron}><small>Sweep {entry.config.sweep_cron}</small></Show></td>
            <td><Tag variant={entry.config.enabled ? "success" : "muted"}>{entry.config.enabled ? "Enabled" : "Disabled"}</Tag></td>
            <td><Show when={entry.job} fallback={<span>—</span>}>{(job) => <button class="text-button" onClick={() => setSelectedJob(job())}><StatusIndicator status={job().status} label={job().status} /> <progress max="100" value={progress(job())} /> {job().partitions_done ?? 0}/{job().partitions_total ?? 0}</button>}</Show></td>
            <td class="row-actions"><button class="button-secondary" onClick={() => void run(entry.config, false)}>Run now</button><button class="button-secondary" onClick={() => void run(entry.config, true)}>Sweep now</button><button class="button-secondary" onClick={() => startEdit(entry.config)}>Edit</button><button class="button-secondary" onClick={() => void toggle(entry.config)}>{entry.config.enabled ? "Disable" : "Enable"}</button></td>
          </tr>}</For>
        </tbody></Table>
      </Show>
    </Show>
    <Drawer open={formOpen()} title={editing() === null ? "Connect a collector" : `Edit ${editing()!.name}`} onClose={() => setFormOpen(false)}>
      <form class="page-drawer-content" onSubmit={(event) => void save(event)}>
        <label class="field">Name<input value={name()} onInput={(event) => setName(event.currentTarget.value)} required /></label>
        <label class="field">Kind<select value={kind()} onChange={(event) => setKind(event.currentTarget.value)}>{["slack", "gdrive", "gdocs", "gsheets", "github", "custom", "police"].map((option) => <option value={option}>{option}</option>)}</select></label>
        <label class="field">Trigger mode<select value={triggerMode()} onChange={(event) => setTriggerMode(event.currentTarget.value)}><option value="event">Event</option><option value="schedule">Schedule</option><option value="both">Both</option></select></label>
        <label class="field">Schedule cron<input value={cron()} onInput={(event) => setCron(event.currentTarget.value)} placeholder="0 * * * *" /></label>
        <label class="field">Sweep cron<input value={sweepCron()} onInput={(event) => setSweepCron(event.currentTarget.value)} placeholder="0 2 * * *" /></label>
        <label class="field">Credential reference<input value={credentialRef()} onInput={(event) => setCredentialRef(event.currentTarget.value)} placeholder="env:COLLECTOR_TOKEN" /><small>Use env: or file: references; never enter a secret value.</small></label>
        <JsonTextArea label="Selector JSON" value={selector()} onInput={setSelector} errors={selectorErrors()} />
        <div class="row-actions"><button class="button-primary" type="submit">Save collector</button><button class="button-secondary" type="button" onClick={() => setFormOpen(false)}>Cancel</button></div>
      </form>
    </Drawer>
    <Drawer open={selectedJob() !== null} title="Sweep progress" onClose={() => setSelectedJob(null)}>
      <Show when={selectedJob()}>{(job) => <div class="page-drawer-content"><h2>{job().origin}</h2><StatusIndicator status={job().status} label={job().status} /><p>{job().partitions_done ?? 0} of {job().partitions_total ?? 0} partitions complete</p><progress max="100" value={progress(job())} /><p>{job().findings_so_far ?? 0} findings reported</p></div>}</Show>
    </Drawer>
  </section>;
}
