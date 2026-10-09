import { A, useParams } from "@solidjs/router";
import { createResource, createSignal, For, Show, type JSX } from "solid-js";
import { runsApi } from "../../api/client";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { StatusBadge } from "../../components/StatusBadge";
import { RunDetail } from "./RunDetail";

export default function RunsPage(): JSX.Element {
  const params = useParams<{ id?: string }>();
  const [status, setStatus] = createSignal("");
  const [workflow, setWorkflow] = createSignal("");
  const [runs, { refetch }] = createResource(() => {
    const filters = new URLSearchParams();
    if (status()) filters.set("status", status());
    if (workflow().trim()) filters.set("workflow", workflow().trim());
    return runsApi.list(filters);
  });

  return (
    <section>
      <Show when={params.id} fallback={<>
        <div class="page-heading"><div><h1>Runs</h1><p>Track workflow execution, human tasks, and map progress.</p></div></div>
        <div class="panel filters"><label class="field">Status<select value={status()} onChange={(event) => setStatus(event.currentTarget.value)}><option value="">All statuses</option><For each={["queued", "running", "waiting", "succeeded", "failed", "cancelled", "timed_out"]}>{(value) => <option value={value}>{value.replaceAll("_", " ")}</option>}</For></select></label><label class="field">Workflow<input value={workflow()} onInput={(event) => setWorkflow(event.currentTarget.value)} placeholder="Workflow name" /></label></div>
        <Show when={!runs.error} fallback={<ErrorBanner error={runs.error ?? null} />}><Show when={!runs.loading} fallback={<p>Loading runs...</p>}>
          <Show when={(runs()?.items.length ?? 0) > 0} fallback={<div class="empty-state">No runs match these filters.</div>}>
            <div class="table-scroll"><table><thead><tr><th>Workflow</th><th>Status</th><th>Current step</th><th>Started</th></tr></thead><tbody><For each={runs()?.items ?? []}>{(run) => <tr><td><A href={`/runs/${encodeURIComponent(run.id)}`}>{run.workflow}</A><div class="muted">{run.id}</div></td><td><StatusBadge status={run.status} /></td><td>{run.current_step ?? "—"}</td><td>{run.started_at ? <RelativeTime iso={run.started_at} /> : "Not started"}</td></tr>}</For></tbody></table></div>
          </Show>
        </Show></Show>
      </>}>
        <RunDetail runId={params.id ?? ""} onChanged={() => refetch()} />
      </Show>
    </section>
  );
}
