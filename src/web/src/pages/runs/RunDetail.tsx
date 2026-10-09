import { A } from "@solidjs/router";
import { createResource, createSignal, For, onCleanup, onMount, Show, type JSX } from "solid-js";
import { ApiError, humanTasksApi, mapJobsApi, runsApi } from "../../api/client";
import type { HumanTask, JsonValue, MapJob, WorkflowRunDetail } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { StatusBadge } from "../../components/StatusBadge";

function parseJson(source: string | null | undefined): JsonValue | null {
  if (source == null) return null;
  try {
    return JSON.parse(source) as JsonValue;
  } catch {
    return null;
  }
}

function objectValue(value: JsonValue | null): Readonly<Record<string, JsonValue>> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : null;
}

function mapJobId(stepInput: string | null, stepOutput: string | null): string | null {
  const output = objectValue(parseJson(stepOutput));
  const input = objectValue(parseJson(stepInput));
  const outputId = output?.map_job_id;
  const inputId = input?.map_job_id;
  return typeof outputId === "string" ? outputId : typeof inputId === "string" ? inputId : null;
}

function terminal(status: WorkflowRunDetail["status"]): boolean {
  return ["succeeded", "failed", "cancelled", "timed_out"].includes(status);
}

function MapProgress(props: Readonly<{ jobId: string; onChanged: () => void }>): JSX.Element {
  const [job, { refetch }] = createResource(() => props.jobId, (id) => mapJobsApi.get(id));
  const [error, setError] = createSignal<ApiError | null>(null);
  const [busy, setBusy] = createSignal(false);
  const refresh = async (): Promise<void> => { await refetch(); props.onChanged(); };
  const action = async (kind: "cancel" | "retry"): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      if (kind === "cancel") await mapJobsApi.cancel(props.jobId);
      else await mapJobsApi.retryFailed(props.jobId);
      await refresh();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The map job could not be updated."));
    } finally { setBusy(false); }
  };
  const counts = (): MapJob["counts"] => job()?.counts ?? { ready: 0, dispatched: 0, succeeded: 0, failed: 0, cancelled: 0 };
  const failedShards = (): MapJob["failed_shards"] => job()?.failed_shards ?? [];
  const done = (): number => counts().succeeded + counts().failed + counts().cancelled;
  const total = (): number => done() + counts().ready + counts().dispatched;
  const percent = (): number => total() === 0 ? 0 : Math.min(100, Math.round((done() / total()) * 100));
  return <section class="map-progress"><Show when={error()}>{(value) => <div class="error-banner" role="alert"><strong>{value().code}</strong><span>{value().detail}</span></div>}</Show><Show when={!job.error} fallback={<p>Map progress unavailable.</p>}><Show when={job.latest !== undefined || Boolean(job.error)} fallback={<p>Loading map progress...</p>}>
    <div class="map-heading"><strong>Map progress</strong><StatusBadge status={job()?.status ?? "unknown"} /><span>{done()} / {total()}</span></div>
    <progress max="100" value={percent()} aria-label="Map progress" />
    <p class="muted">{counts().succeeded} succeeded · {counts().failed} failed · {counts().ready} ready · {counts().dispatched} dispatched · {counts().cancelled} cancelled; partitions {job()?.partitions_done ?? 0} / {job()?.partitions_total ?? 0}</p>
    <Show when={job()?.paused_until}><p>Dispatch paused until <RelativeTime iso={job()?.paused_until ?? ""} /></p></Show>
    <Show when={failedShards().length > 0}><h4>Failed shard keys</h4><ul><For each={failedShards()}>{(shard) => <li><code>{shard.shard_key}</code>: {shard.error_code}</li>}</For></ul></Show>
    <div class="button-row"><button class="button-secondary" disabled={busy() || job()?.status === "succeeded" || job()?.status === "cancelled"} onClick={() => void action("cancel")}>Cancel map job</button><button class="button-secondary" disabled={busy() || failedShards().length === 0} onClick={() => void action("retry")}>Retry failed shards</button></div>
  </Show></Show></section>;
}

type HumanTaskDraft = Readonly<{ outcome: string; comment: string }>;

function HumanTaskForm(props: Readonly<{ task: HumanTask; draft: HumanTaskDraft; onOutcomeChange: (value: string) => void; onCommentChange: (value: string) => void; onChanged: () => void }>): JSX.Element {
  const [error, setError] = createSignal<ApiError | null>(null);
  const [busy, setBusy] = createSignal(false);
  const submit = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await humanTasksApi.complete(props.task.id, props.draft.outcome, props.draft.comment.trim() || undefined);
      props.onChanged();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409 && cause.code === "evidence_required") {
        setError(new ApiError(409, cause.code, "Upload evidence first."));
      } else setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The task could not be completed."));
    } finally { setBusy(false); }
  };
  return <form class="human-task-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}><Show when={error()}>{(value) => <div class="error-banner" role="alert"><strong>{value().code}</strong><span>{value().detail}</span></div>}</Show>
    <label class="field">Outcome<select value={props.draft.outcome} onChange={(event) => props.onOutcomeChange(event.currentTarget.value)}><For each={props.task.outcomes}>{(value) => <option value={value}>{value}</option>}</For></select></label>
    <label class="field">Comment<textarea value={props.draft.comment} onInput={(event) => props.onCommentChange(event.currentTarget.value)} rows="3" /></label>
    <button class="button-primary" type="submit" disabled={busy() || props.draft.outcome === ""}>Complete task</button>
  </form>;
}

export function RunDetail(props: Readonly<{ runId: string; onChanged?: () => void }>): JSX.Element {
  const [run, { refetch }] = createResource(() => props.runId, (id) => runsApi.get(id));
  const [taskDrafts, setTaskDrafts] = createSignal<ReadonlyMap<string, HumanTaskDraft>>(new Map());
  const [error, setError] = createSignal<ApiError | null>(null);
  const [busy, setBusy] = createSignal(false);
  const refresh = async (): Promise<void> => { await refetch(); props.onChanged?.(); };
  onMount(() => {
    const timer = globalThis.setInterval(() => {
      if (!terminal(run()?.status ?? "queued")) void refetch();
    }, 5000);
    onCleanup(() => globalThis.clearInterval(timer));
  });
  const cancel = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try { await runsApi.cancel(props.runId); await refresh(); }
    catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The run could not be cancelled.")); }
    finally { setBusy(false); }
  };
  const current = (stepId: string): boolean => run()?.current_step === stepId;
  const taskDraft = (task: HumanTask): HumanTaskDraft => taskDrafts().get(task.id) ?? { outcome: task.outcomes[0] ?? "", comment: "" };
  const updateTaskDraft = (task: HumanTask, outcome: string, comment: string): void => {
    setTaskDrafts((previous) => new Map(previous).set(task.id, { outcome, comment }));
  };
  const mapId = (stepRunId: string): string | null => {
    const step = run()?.steps.find((candidate) => candidate.id === stepRunId);
    return step === undefined ? null : mapJobId(step.input_json, step.output_json);
  };

  return <div class="run-detail"><div class="page-heading"><div><A href="/runs">Runs</A><h1>{run()?.workflow ?? "Workflow run"}</h1><p>{props.runId}</p></div><div class="button-row"><Show when={run()}>{(detail) => <StatusBadge status={detail().status} />}</Show><button class="button-secondary" type="button" disabled={busy() || run()?.status === undefined || terminal(run()!.status)} onClick={() => void cancel()}>Cancel run</button></div></div>
    <Show when={error()}>{(value) => <div class="error-banner" role="alert"><strong>{value().code}</strong><span>{value().detail}</span></div>}</Show><Show when={!run.error} fallback={<ErrorBanner error={run.error instanceof ApiError ? run.error : new ApiError(0, "request_failed", "Run details could not be loaded.")} />}><Show when={run.latest !== undefined || Boolean(run.error)} fallback={<p>Loading run...</p>}>
      <Show when={run()}>{(detail) => <>
        <section class="panel"><div class="detail-grid"><div><span class="muted">Version</span><strong>{detail().workflow_version}</strong></div><div><span class="muted">Issue</span><strong>{detail().issue_id ?? "—"}</strong></div><div><span class="muted">Started</span><strong>{detail().started_at ? <RelativeTime iso={detail().started_at ?? ""} /> : "Not started"}</strong></div><div><span class="muted">Finished</span><strong>{detail().finished_at ? <RelativeTime iso={detail().finished_at ?? ""} /> : "In progress"}</strong></div></div></section>
        <section class="panel"><h2>Steps</h2><ol class="step-list"><For each={detail().steps}>{(step) => <li classList={{ "current-step": current(step.step_id) }}><div class="step-heading"><div><strong>{step.step_id}</strong><span class="muted">{step.kind} · visit {step.visit}</span></div><StatusBadge status={step.status} /></div><div class="detail-grid"><div><span class="muted">Started</span><strong>{step.started_at ? <RelativeTime iso={step.started_at} /> : "—"}</strong></div><div><span class="muted">Finished</span><strong>{step.finished_at ? <RelativeTime iso={step.finished_at} /> : "—"}</strong></div><div><span class="muted">Outcome</span><strong>{objectValue(parseJson(step.output_json))?.outcome?.toString() ?? (step.output_json === null ? "—" : "unavailable")}</strong></div></div><Show when={step.output_json !== null}><details><summary>Step output</summary><pre>{JSON.stringify(parseJson(step.output_json) ?? "unavailable", null, 2)}</pre></details></Show><Show when={step.kind === "map" ? mapId(step.id) : null}>{(id) => <MapProgress jobId={id()} onChanged={() => void refresh()} />}</Show></li>}</For></ol></section>
        <section class="panel"><h2>Transitions</h2><ol class="transition-list"><For each={detail().transitions}>{(transition) => <li><RelativeTime iso={transition.at} /><span><strong>{transition.kind === "send_back" ? "Send back" : transition.kind.replaceAll("_", " ")}</strong>: {transition.from_step ?? "Start"} → {transition.to_step ?? "End"}</span><Show when={transition.kind === "send_back" && transition.reason}><p>Reason: {transition.reason}</p></Show></li>}</For></ol></section>
        <section class="panel"><h2>Human tasks</h2><Show when={detail().human_tasks.length > 0} fallback={<p>No human tasks for this run.</p>}><For each={detail().human_tasks}>{(task) => <article class="task-card"><div class="step-heading"><div><strong>{task.title}</strong><span class="muted">{task.assignee_ref} · tier {task.tier_index + 1}</span></div><StatusBadge status={task.status} /></div><p>SLA due: {task.due_at ? <RelativeTime iso={task.due_at} /> : "No SLA"}</p><Show when={task.status === "pending"}><HumanTaskForm task={task} draft={taskDraft(task)} onOutcomeChange={(outcome) => { const draft = taskDraft(task); updateTaskDraft(task, outcome, draft.comment); }} onCommentChange={(comment) => { const draft = taskDraft(task); updateTaskDraft(task, draft.outcome, comment); }} onChanged={() => void refresh()} /></Show></article>}</For></Show></section>
        <section class="panel"><h2>Run output</h2><pre>{JSON.stringify(parseJson(detail().context_json) ?? "unavailable", null, 2)}</pre></section>
      </>}</Show>
    </Show></Show>
  </div>;
}
