import { A, useParams } from "@solidjs/router";
import { createResource, createSignal, For, Show, type JSX } from "solid-js";
import { runsApi, workflowsApi } from "../../api/client";
import type { RunStatus } from "../../api/types";
import { EmptyState, StatusIndicator, Table } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";
import { RelativeTime } from "../../components/RelativeTime";
import { RunDetail } from "./RunDetail";

const runStatuses: readonly RunStatus[] = ["running", "waiting", "succeeded", "failed", "cancelled"];

export default function RunsPage(): JSX.Element {
  const params = useParams<{ id?: string }>();
  const [status, setStatus] = createSignal("");
  const [workflow, setWorkflow] = createSignal("");
  const [runs, { refetch }] = createResource(() => [status(), workflow()] as const, async ([selectedStatus, selectedWorkflow]) => {
    const filters = new URLSearchParams();
    if (selectedStatus) filters.set("status", selectedStatus);
    if (selectedWorkflow.trim()) filters.set("workflow", selectedWorkflow.trim());
    const [runPage, definitions] = await Promise.all([runsApi.list(filters), workflowsApi.list()]);
    const names = new Map(definitions.items.map((definition) => [definition.id, definition.name]));
    return runPage.items.map((run) => ({ run, workflowName: names.get(run.definition_id) ?? run.definition_id }));
  });

  return (
    <section>
      <Show when={params.id} fallback={<>
        <TopBar.Slot name="title">Runs</TopBar.Slot>
        <p class="muted">Track workflow execution, human tasks, and map progress.</p>
        <div class="panel filters"><label class="field">Status<select aria-label="Status" value={status()} onChange={(event) => setStatus(event.currentTarget.value)}><option value="">All statuses</option><For each={runStatuses}>{(value) => <option value={value}>{value.replaceAll("_", " ")}</option>}</For></select></label><label class="field">Workflow<input aria-label="Workflow" value={workflow()} onInput={(event) => setWorkflow(event.currentTarget.value)} placeholder="Workflow name" /></label></div>
        <Show when={!runs.loading} fallback={<p>Loading runs...</p>}>
          <Show when={!runs.error} fallback={<p role="alert">Runs could not be loaded.</p>}>
          <Show when={(runs()?.length ?? 0) > 0} fallback={<EmptyState title="No runs match these filters"><p>Try another workflow or status.</p></EmptyState>}>
            <Table><thead><tr><th>Started</th><th>Workflow</th><th>Issue</th><th>Trigger</th><th>Status</th></tr></thead><tbody><For each={runs()}>{(item) => <tr><td><RelativeTime iso={item.run.started_at} /></td><td><A href={`/runs/${encodeURIComponent(item.run.id)}`}>{item.workflowName}</A><div class="muted">{item.run.id}</div></td><td>{item.run.issue_id ? <A href={`/issues/${encodeURIComponent(item.run.issue_id)}`}>{item.run.issue_id}</A> : "—"}</td><td>{item.run.trigger}</td><td><StatusIndicator status={item.run.status} /></td></tr>}</For></tbody></Table>
          </Show>
          </Show>
        </Show>
      </>}>
        <RunDetail runId={params.id ?? ""} onChanged={() => refetch()} />
      </Show>
    </section>
  );
}
