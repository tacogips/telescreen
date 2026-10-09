import { A } from "@solidjs/router";
import { createResource, createSignal, For, onCleanup, onMount, Show, type JSX } from "solid-js";
import { Check, Clock3, LoaderCircle, Slash, X } from "lucide-solid";
import { ApiError, humanTasksApi, mapJobsApi, runsApi, workflowsApi } from "../../api/client";
import type { JsonValue, MapJob, RunDetail as RunDetailData, RunHumanTask, RunStatus } from "../../api/types";
import { Button, FoldSection, Panel, StatusIndicator } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { StatusBadge } from "../../components/StatusBadge";
import { parseWorkflowGraph } from "../workflows/graph";

function parseJson(source: string | null): JsonValue | null {
  if (source === null) return null;
  try { return JSON.parse(source) as JsonValue; } catch { return null; }
}
function objectValue(value: JsonValue | null): Readonly<Record<string, JsonValue>> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : null;
}
function mapJobId(input: string | null, output: string | null): string | null {
  const outputId = objectValue(parseJson(output))?.map_job_id;
  const inputId = objectValue(parseJson(input))?.map_job_id;
  return typeof outputId === "string" ? outputId : typeof inputId === "string" ? inputId : null;
}
function terminal(status: RunStatus | undefined): boolean {
  return status !== undefined && status !== "running" && status !== "waiting";
}

function MapProgress(props: Readonly<{ jobId: string; onChanged: () => void }>): JSX.Element {
  const [job, { refetch }] = createResource(() => props.jobId, (id) => mapJobsApi.get(id));
  const [error, setError] = createSignal<ApiError | null>(null);
  const [busy, setBusy] = createSignal(false);
  const refresh = async (): Promise<void> => { await refetch(); props.onChanged(); };
  const action = async (kind: "cancel" | "retry"): Promise<void> => {
    setBusy(true); setError(null);
    try { if (kind === "cancel") await mapJobsApi.cancel(props.jobId); else await mapJobsApi.retryFailed(props.jobId); await refresh(); }
    catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The map job could not be updated.")); }
    finally { setBusy(false); }
  };
  const counts = (): MapJob["counts"] => job()?.counts ?? {};
  const count = (key: "ready" | "dispatched" | "succeeded" | "failed" | "cancelled"): number => counts()[key] ?? 0;
  const failedShards = (): MapJob["failed_shards"] => job()?.failed_shards ?? [];
  const done = (): number => count("succeeded") + count("failed") + count("cancelled");
  const total = (): number => done() + count("ready") + count("dispatched");
  const percent = (): number => total() === 0 ? 0 : Math.min(100, Math.round((done() / total()) * 100));
  return <section class="map-progress"><Show when={error()}>{(value) => <div class="error-banner" role="alert"><strong>{value().code}</strong><span>{value().detail}</span></div>}</Show><Show when={!job.error} fallback={<p>Map progress unavailable.</p>}><Show when={job.latest !== undefined || Boolean(job.error)} fallback={<p>Loading map progress...</p>}>
    <div class="map-heading"><strong>Map progress</strong><StatusBadge status={job()?.status ?? "unknown"} /><span>{done()} / {total()}</span></div>
    <progress max="100" value={percent()} aria-label="Map progress" />
    <p class="muted">{count("succeeded")} succeeded · {count("failed")} failed · {count("ready")} ready · {count("dispatched")} dispatched · {count("cancelled")} cancelled; partitions {job()?.partitions_done ?? 0} / {job()?.partitions_total ?? 0}</p>
    <Show when={job()?.paused_until}><p>Dispatch paused until <RelativeTime iso={job()?.paused_until ?? ""} /></p></Show>
    <Show when={failedShards().length > 0}><h4>Failed shard keys</h4><ul><For each={failedShards()}>{(shard) => <li><code>{shard.shard_key}</code>: {shard.error_code}</li>}</For></ul></Show>
    <div class="button-row"><button class="button-secondary" disabled={busy() || job()?.status === "succeeded" || job()?.status === "cancelled"} onClick={() => void action("cancel")}>Cancel map job</button><button class="button-secondary" disabled={busy() || failedShards().length === 0} onClick={() => void action("retry")}>Retry failed shards</button></div>
  </Show></Show></section>;
}

type HumanTaskDraft = Readonly<{ outcome: string; comment: string }>;
function HumanTaskForm(props: Readonly<{ task: RunHumanTask; draft: HumanTaskDraft; onOutcomeChange: (value: string) => void; onCommentChange: (value: string) => void; onChanged: () => void }>): JSX.Element {
  const [error, setError] = createSignal<ApiError | null>(null);
  const [busy, setBusy] = createSignal(false);
  const submit = async (): Promise<void> => {
    setBusy(true); setError(null);
    try { await humanTasksApi.complete(props.task.id, props.draft.outcome, props.draft.comment.trim() || undefined); props.onChanged(); }
    catch (cause) {
      if (cause instanceof ApiError && cause.status === 409 && cause.code === "evidence_required") setError(new ApiError(409, cause.code, "Upload evidence first."));
      else setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The task could not be completed."));
    } finally { setBusy(false); }
  };
  return <form class="human-task-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}><Show when={error()}>{(value) => <div class="error-banner" role="alert"><strong>{value().code}</strong><span>{value().detail}</span></div>}</Show>
    <label class="field">Outcome<select value={props.draft.outcome} onChange={(event) => props.onOutcomeChange(event.currentTarget.value)}><For each={props.task.outcomes}>{(value) => <option value={value}>{value}</option>}</For></select></label>
    <label class="field">Comment<textarea value={props.draft.comment} onInput={(event) => props.onCommentChange(event.currentTarget.value)} rows="3" /></label>
    <button class="button-primary" type="submit" disabled={busy() || props.draft.outcome === ""}>Complete task</button>
  </form>;
}

export function RunDetail(props: Readonly<{ runId: string; onChanged?: () => void }>): JSX.Element {
  const [detail, { refetch }] = createResource(() => props.runId, (id) => runsApi.get(id));
  const runData = (): RunDetailData | undefined => detail.error ? undefined : detail.latest;
  const detailError = (): ApiError | undefined => detail.error === undefined ? undefined : detail.error instanceof ApiError ? detail.error : new ApiError(0, "request_failed", "Run details could not be loaded.");
  const [definitions] = createResource(() => workflowsApi.list());
  const definitionItems = () => definitions.error ? undefined : definitions.latest?.items;
  const definition = () => definitionItems()?.find((item) => item.id === runData()?.run.definition_id);
  const [workflowVersion] = createResource(() => {
    const item = definition();
    return item === undefined ? undefined : [item.name, item.version] as const;
  }, ([name, version]) => workflowsApi.version(name, version));
  const graph = () => {
    const compiled = workflowVersion.error ? undefined : workflowVersion.latest?.compiled_json;
    return compiled === undefined ? null : parseWorkflowGraph(compiled, "json");
  };
  const stepTypes = (): ReadonlyMap<string, string> => new Map((graph()?.nodes ?? []).map((node) => [node.id, node.type]));
  const [taskDrafts, setTaskDrafts] = createSignal<ReadonlyMap<string, HumanTaskDraft>>(new Map());
  const [error, setError] = createSignal<ApiError | null>(null);
  const [busy, setBusy] = createSignal(false);
  const refresh = async (): Promise<void> => { await refetch(); props.onChanged?.(); };
  onMount(() => {
    const timer = globalThis.setInterval(() => {
      const status = runData()?.run.status;
      if (status === "running" || status === "waiting") void refetch();
    }, 5000);
    onCleanup(() => globalThis.clearInterval(timer));
  });
  const cancel = async (): Promise<void> => {
    setBusy(true); setError(null);
    try { await runsApi.cancel(props.runId); await refresh(); }
    catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The run could not be cancelled.")); }
    finally { setBusy(false); }
  };
  const taskDraft = (task: RunHumanTask): HumanTaskDraft => taskDrafts().get(task.id) ?? { outcome: task.outcomes[0] ?? "", comment: "" };
  const updateTaskDraft = (task: RunHumanTask, outcome: string, comment: string): void => { setTaskDrafts((previous) => new Map(previous).set(task.id, { outcome, comment })); };
  const mapId = (stepRunId: string): string | null => {
    const step = runData()?.steps.find((candidate) => candidate.id === stepRunId);
    return step === undefined ? null : mapJobId(step.input_json, step.output_json);
  };
  const currentStatus = (): RunStatus | undefined => runData()?.run.status;
  const currentStepId = (): string | null => {
    const steps = runData()?.steps ?? [];
    return [...steps].reverse().find((step) => step.status === "running" || step.status === "waiting" || step.status === "ready")?.id ?? null;
  };
  const stepIcon = (status: string): JSX.Element => {
    if (status === "running") return <LoaderCircle size={16} class="run-step-spinner" aria-label="Running" />;
    if (status === "succeeded") return <Check size={16} aria-label="Succeeded" />;
    if (status === "failed") return <X size={16} aria-label="Failed" />;
    if (status === "cancelled") return <Slash size={16} aria-label="Cancelled" />;
    return <Clock3 size={16} aria-label={status === "waiting" ? "Waiting" : "Ready"} />;
  };

  return <div class="run-detail"><TopBar.Slot name="title">{(definition()?.name ?? "Workflow run") + " · " + props.runId}</TopBar.Slot><TopBar.Slot name="actions"><Button variant="danger" disabled={busy() || terminal(currentStatus()) || currentStatus() === undefined} onClick={() => void cancel()}>Cancel run</Button></TopBar.Slot>
    <Show when={error()}>{(value) => <div class="error-banner" role="alert"><strong>{value().code}</strong><span>{value().detail}</span></div>}</Show><Show when={detailError()} keyed>{(value) => <ErrorBanner error={value} />}</Show><Show when={!detail.error}><Show when={detail.latest !== undefined || Boolean(detail.error)} fallback={<p>Loading run...</p>}>
      <Show when={runData()}>{(value) => <>
        <Panel><div class="detail-grid"><div><span class="muted">Workflow</span><strong>{definition()?.name ?? value().run.definition_id}</strong></div><div><span class="muted">Status</span><StatusIndicator status={value().run.status} /></div><div><span class="muted">Issue</span><strong><Show when={value().run.issue_id} fallback="—">{(id) => <A href={"/issues/" + encodeURIComponent(id())}>{id()}</A>}</Show></strong></div><div><span class="muted">Started</span><strong><RelativeTime iso={value().run.started_at} /></strong></div><div><span class="muted">Finished</span><strong><Show when={value().run.finished_at}>{(at) => <RelativeTime iso={at()} />}</Show><Show when={!value().run.finished_at}>In progress</Show></strong></div></div></Panel>
        <Panel><h2>Steps</h2><ol class="step-list"><For each={value().steps}>{(step) => <li class={currentStepId() === step.id ? "current-step" : ""}><div class="step-heading"><div class="step-title">{stepIcon(step.status)}<strong>{step.step_id}</strong><span class="muted">{stepTypes().get(step.step_id) ?? "Step"} · visit {step.visit} · attempt {step.attempt}</span></div><StatusIndicator status={step.status} /></div><Show when={step.error_code}><p role="alert">{step.error_code}</p></Show><FoldSection id={"step-input-" + step.id} title="Input"><pre>{JSON.stringify(parseJson(step.input_json) ?? "unavailable", null, 2)}</pre></FoldSection><Show when={step.output_json !== null}><FoldSection id={"step-output-" + step.id} title="Output"><pre>{JSON.stringify(parseJson(step.output_json) ?? "unavailable", null, 2)}</pre></FoldSection></Show><Show when={mapId(step.id)}>{(id) => <MapProgress jobId={id()} onChanged={() => void refresh()} />}</Show></li>}</For></ol></Panel>
        <Panel><h2>Transitions</h2><ol class="transition-list"><For each={value().transitions}>{(transition) => <li><RelativeTime iso={transition.at} /><span><strong>{transition.kind === "send_back" ? "↶ Send back" : transition.kind.replaceAll("_", " ")}</strong>: {transition.from_step_run_id ?? "Start"} → {transition.to_step_id}</span><Show when={transition.kind === "send_back" && transition.reason}><p>Reason: {transition.reason}</p></Show></li>}</For></ol></Panel>
        <Panel><h2>Human tasks</h2><Show when={value().human_tasks.length > 0} fallback={<p>No human tasks for this run.</p>}><For each={value().human_tasks}>{(task) => <article class="task-card"><div class="step-heading"><div><strong>{task.title}</strong><span class="muted">{task.assignee_ref}</span></div><StatusIndicator status={task.status} /></div><Show when={task.status === "open"}><HumanTaskForm task={task} draft={taskDraft(task)} onOutcomeChange={(outcome) => { const draft = taskDraft(task); updateTaskDraft(task, outcome, draft.comment); }} onCommentChange={(comment) => { const draft = taskDraft(task); updateTaskDraft(task, draft.outcome, comment); }} onChanged={() => void refresh()} /></Show></article>}</For></Show></Panel>
      </>}</Show>
    </Show></Show>
  </div>;
}
