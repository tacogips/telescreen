import { A, useParams, useSearchParams } from "@solidjs/router";
import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, collectorsApi, mapJobsApi } from "../../api/client";
import type { CollectorConfig, CoverageResource, CoverageSummary, MapJob, MapJobSummary } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { JsonTextArea } from "../../components/JsonTextArea";
import { Drawer, EmptyState, ExposureBadge, Pagination, Panel, PanelItem, Placeholder, StatusIndicator, Table, Tag, showToast } from "../../components/ui";
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
  const route = useParams<{ id?: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [coveragePage, setCoveragePage] = createSignal(1);
  const [coverageCursors, setCoverageCursors] = createSignal<(string | undefined)[]>([undefined]);
  const [coverageState, setCoverageState] = createSignal<string | undefined>();
  const [coverage] = createResource(
      () => route.id !== undefined && searchParams.tab === "coverage" ? [route.id, coverageCursors()[coveragePage() - 1], coverageState()] as const : undefined,
    async ([id, cursor, state]): Promise<Readonly<{ summary: CoverageSummary; rows: CoverageResource[]; nextCursor: string | null }>> => {
      const filters: { cursor?: string; state?: string; limit: number } = { limit: 10 };
      if (cursor !== undefined) filters.cursor = cursor;
      if (state !== undefined && state !== "") filters.state = state;
      const [summary, resources] = await Promise.all([collectorsApi.coverage(id), collectorsApi.coverageResources(id, filters)]);
      return { summary, rows: resources.items, nextCursor: resources.next_cursor };
    },
  );
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
    <TopBar.Slot name="title">{route.id === undefined ? "Collectors" : entries()?.find((entry) => entry.config.id === route.id)?.config.name ?? "Collector"}</TopBar.Slot>
    <TopBar.Slot name="actions"><Show when={route.id === undefined}><button class="button-primary" onClick={() => startEdit(null)}>Connect source</button></Show></TopBar.Slot>
    <Show when={error() ?? resourceError(entries.error)}>{(failure) => <ErrorBanner error={failure()} />}</Show>
    <Show when={sweepConflict()}><p class="notice-warning" role="alert">A sweep is already running</p></Show>
    <Show when={route.id !== undefined}>
      <nav aria-label="Collector detail tabs" class="row-actions"><button class="button-secondary" aria-current={searchParams.tab !== "coverage" ? "page" : undefined} onClick={() => { setCoveragePage(1); setCoverageCursors([undefined]); setSearchParams({ tab: undefined }); }}>Overview</button><button class="button-secondary" aria-current={searchParams.tab === "coverage" ? "page" : undefined} onClick={() => { setCoveragePage(1); setCoverageCursors([undefined]); setSearchParams({ tab: "coverage" }); }}>Coverage</button><A href="/collectors">All collectors</A></nav>
      <Show when={searchParams.tab === "coverage"} fallback={
        <Show when={entries()?.find((entry) => entry.config.id === route.id)}>
          {(entry) => <Panel title="Overview">
            <PanelItem><span>Source</span><strong>{entry().config.kind}</strong></PanelItem>
            <PanelItem><span>Trigger</span><strong>{entry().config.trigger_mode}</strong></PanelItem>
            <PanelItem><span>Health</span><span title={(entry().config.health?.conditions ?? []).join(", ") || "No active health conditions"}><StatusIndicator status={healthStyle(entry().config.health?.status)} label={entry().config.health?.status ?? "unknown"} /></span></PanelItem>
            <PanelItem><span>Last success</span><span>{entry().config.health?.last_success_at ?? "Never"}</span></PanelItem>
          </Panel>}
        </Show>
      }>
        <Show when={!coverage.loading} fallback={<Placeholder label="Loading coverage…" />}>
          <Show when={coverage.latest}>
            {(result) => <>
              <Panel title="Coverage">
                <p>Since {result().summary.since} · {result().summary.unscannable_total} unscannable resources</p>
                <For each={result().summary.counts}>{(count) => <PanelItem><span>{count.state}{count.reason === null ? "" : " · " + count.reason}</span><strong>{count.count}</strong></PanelItem>}</For>
              </Panel>
              <Panel title="Unscannable resources">
                <label class="field">State<select aria-label="Coverage state" value={coverageState() ?? ""} onChange={(event) => { setCoverageState(event.currentTarget.value || undefined); setCoveragePage(1); setCoverageCursors([undefined]); }}>
                  <option value="">All states</option><option value="partial">Partial</option><option value="unsupported">Unsupported</option><option value="fetch_failed">Fetch failed</option>
                </select></label>
                <Show when={result().rows.length > 0} fallback={<p>No unscannable resources.</p>}>
                  <Table><thead><tr><th>Resource</th><th>State</th><th>Reason</th><th>Exposure</th><th>Last attempt</th><th>Linked issues</th></tr></thead>
                    <tbody><For each={[...result().rows].sort((left, right) => Number(right.exposure_visibility === "public") - Number(left.exposure_visibility === "public"))}>{(row) =>
                      <tr><td><code>{row.resource_id.slice(0, 8)}</code></td><td>{row.state}</td><td>{row.reason ?? "—"}</td><td><ExposureBadge visibility={row.exposure_visibility} /></td><td>{row.last_attempt_at}</td>
                        <td><For each={row.issues}>{(issue) => <A href={"/issues/" + encodeURIComponent(issue.issue_id)}>{issue.short_id}</A>}</For></td></tr>
                    }</For></tbody>
                  </Table>
                </Show>
                <Pagination page={coveragePage()} pages={coveragePage() + (result().nextCursor === null ? 0 : 1)} onChange={(page) => {
                  if (page < coveragePage()) setCoveragePage(page);
                  else if (result().nextCursor !== null) {
                    setCoverageCursors((previous) => [...previous.slice(0, page - 1), result().nextCursor ?? undefined]);
                    setCoveragePage(page);
                  }
                }} />
              </Panel>
            </>}
          </Show>
        </Show>
      </Show>
    </Show>
    <Show when={route.id === undefined}>
    <Show when={!entries.loading} fallback={<Placeholder label="Loading collectors…" />}>
      <Show when={(entries()?.length ?? 0) > 0} fallback={<EmptyState title="No collectors configured"><p>Connect a source to begin monitoring.</p><button class="button-primary" onClick={() => startEdit(null)}>Connect a source</button></EmptyState>}>
        <Table><thead><tr><th>Collector</th><th>Trigger</th><th>Schedule</th><th>Status</th><th>Latest sweep</th><th>Actions</th></tr></thead><tbody>
          <For each={entries()}>{(entry) => <tr>
            <td><span class="collector-kind-icon" aria-hidden="true">{entry.config.kind.slice(0, 1).toUpperCase()}</span> <A href={"/collectors/" + encodeURIComponent(entry.config.id)}><strong>{entry.config.name}</strong></A><small>{entry.config.kind}</small></td>
            <td>{entry.config.trigger_mode}</td><td>{entry.config.cron ?? "—"}<Show when={entry.config.sweep_cron}><small>Sweep {entry.config.sweep_cron}</small></Show></td>
            <td><span title={(entry.config.health?.conditions ?? []).join(", ") + (entry.config.health?.last_success_at ? " · Last success " + entry.config.health.last_success_at : "")}><StatusIndicator status={healthStyle(entry.config.health?.status)} label={entry.config.health?.status ?? (entry.config.enabled ? "unknown" : "disabled")} /></span><Show when={entry.config.health?.event_only}><Tag variant="warning">Event only</Tag></Show><Tag variant={entry.config.enabled ? "success" : "muted"}>{entry.config.enabled ? "Enabled" : "Disabled"}</Tag></td>
            <td><Show when={entry.job} fallback={<span>—</span>}>{(job) => <button class="text-button" onClick={() => setSelectedJob(job())}><StatusIndicator status={job().status} label={job().status} /> <progress max="100" value={progress(job())} /> {job().partitions_done ?? 0}/{job().partitions_total ?? 0}</button>}</Show></td>
            <td class="row-actions"><button class="button-secondary" onClick={() => void run(entry.config, false)}>Run now</button><button class="button-secondary" onClick={() => void run(entry.config, true)}>Sweep now</button><button class="button-secondary" onClick={() => startEdit(entry.config)}>Edit</button><button class="button-secondary" onClick={() => void toggle(entry.config)}>{entry.config.enabled ? "Disable" : "Enable"}</button></td>
          </tr>}</For>
        </tbody></Table>
      </Show>
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

function healthStyle(status: string | undefined): string {
  if (status === "ok") return "success";
  if (status === "warning") return "warning";
  if (status === "failing") return "danger";
  return "muted";
}
