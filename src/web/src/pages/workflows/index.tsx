import { A, useParams } from "@solidjs/router";
import { createResource, For, Show, type JSX } from "solid-js";
import { workflowsApi } from "../../api/client";
import type { WorkflowVersionRef } from "../../api/types";
import { RelativeTime } from "../../components/RelativeTime";
import { EmptyState, Table, Tag } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";
import { WorkflowEditor } from "./WorkflowEditor";

type WorkflowRunCounts = Readonly<{
  running: number;
  waiting: number;
  succeeded: number;
  failed: number;
  cancelled: number;
}>;

type WorkflowListItem = WorkflowVersionRef & Readonly<{
  trigger_events?: readonly string[];
  last_run_at?: string | null;
  runs_30d?: WorkflowRunCounts;
}>;

const zeroRunCounts: WorkflowRunCounts = {
  running: 0,
  waiting: 0,
  succeeded: 0,
  failed: 0,
  cancelled: 0,
};

const runCountStatuses: readonly (keyof WorkflowRunCounts)[] = [
  "running",
  "waiting",
  "succeeded",
  "failed",
  "cancelled",
];

export default function WorkflowsPage(): JSX.Element {
  const params = useParams<{ id?: string }>();
  const [workflows, { refetch }] = createResource(async () => {
    const items = (await workflowsApi.list()).items as readonly WorkflowListItem[];
    const grouped = new Map<string, typeof items>();
    for (const item of items) grouped.set(item.name, [...(grouped.get(item.name) ?? []), item]);
    return [...grouped.entries()].map(([name, versions]) => {
      const ordered = [...versions].sort((left, right) => right.version - left.version);
      const current = ordered.find((item) => item.active) ?? ordered[0];
      return { name, latest: ordered[0]?.version ?? 0, active: ordered.find((item) => item.active)?.version ?? null,
        triggers: current?.trigger_events ?? [], lastRun: current?.last_run_at ?? null,
        runCounts: current?.runs_30d ?? zeroRunCounts };
    });
  });
  return <section class="workflow-page">
    <Show when={params.id} fallback={<>
      <TopBar.Slot name="title">Workflows</TopBar.Slot>
      <TopBar.Slot name="actions"><A href="/workflows/new">New workflow</A></TopBar.Slot>
      <Show when={!workflows.loading} fallback={<p>Loading workflows...</p>}>
        <Show when={!workflows.error} fallback={<p role="alert">Workflows could not be loaded.</p>}>
        <Show when={(workflows()?.length ?? 0) > 0} fallback={<EmptyState title="No workflows yet"><p>Create a workflow to start an incident response process.</p></EmptyState>}>
          <Table><thead><tr><th>Name</th><th>Trigger events</th><th>Active</th><th>Last run</th><th>Runs (30 days)</th></tr></thead><tbody>
            <For each={workflows()}>{(item) => {
              const total = Object.values(item.runCounts).reduce((sum, count) => sum + count, 0);
              return <tr><td><A href={`/workflows/${encodeURIComponent(item.name)}`}>{item.name}</A><small>Latest v{item.latest}</small></td><td><div class="workflow-trigger-tags"><For each={item.triggers}>{(trigger) => <Tag>{trigger}</Tag>}</For><Show when={item.triggers.length === 0}>-</Show></div></td><td>{item.active === null ? "Inactive" : `v${item.active}`}</td><td><Show when={item.lastRun} fallback="Never">{(at) => <RelativeTime iso={at()} />}</Show></td><td><div class="workflow-run-counts"><Tag>{total}</Tag><For each={runCountStatuses.filter((status) => item.runCounts[status] > 0)}>{(status) => <Tag>{status} {item.runCounts[status]}</Tag>}</For></div></td></tr>;
            }}</For>
          </tbody></Table>
        </Show>
        </Show>
      </Show>
    </>}>
      <WorkflowEditor name={params.id === "new" ? undefined : params.id} onChanged={() => refetch()} />
    </Show>
  </section>;
}
