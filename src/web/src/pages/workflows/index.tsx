import { A, useParams } from "@solidjs/router";
import { createResource, For, Show, type JSX } from "solid-js";
import { workflowsApi } from "../../api/client";
import { EmptyState, Table } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";
import { WorkflowEditor } from "./WorkflowEditor";

export default function WorkflowsPage(): JSX.Element {
  const params = useParams<{ id?: string }>();
  const [workflows, { refetch }] = createResource(async () => {
    const items = (await workflowsApi.list()).items;
    const grouped = new Map<string, typeof items>();
    for (const item of items) grouped.set(item.name, [...(grouped.get(item.name) ?? []), item]);
    return [...grouped.entries()].map(([name, versions]) => {
      const ordered = [...versions].sort((left, right) => right.version - left.version);
      return { name, latest: ordered[0]?.version ?? 0, active: ordered.find((item) => item.active)?.version ?? null };
    });
  });
  return <section class="workflow-page">
    <Show when={params.id} fallback={<>
      <TopBar.Slot name="title">Workflows</TopBar.Slot>
      <TopBar.Slot name="actions"><A href="/workflows/new">New workflow</A></TopBar.Slot>
      <Show when={!workflows.loading} fallback={<p>Loading workflows...</p>}>
        <Show when={!workflows.error} fallback={<p role="alert">Workflows could not be loaded.</p>}>
        <Show when={(workflows()?.length ?? 0) > 0} fallback={<EmptyState title="No workflows yet"><p>Create a workflow to start an incident response process.</p></EmptyState>}>
          <Table><thead><tr><th>Name</th><th>Latest version</th><th>Active version</th></tr></thead><tbody>
            <For each={workflows()}>{(item) => <tr><td><A href={`/workflows/${encodeURIComponent(item.name)}`}>{item.name}</A></td><td>{item.latest}</td><td>{item.active ?? "—"}</td></tr>}</For>
          </tbody></Table>
        </Show>
        </Show>
      </Show>
    </>}>
      <WorkflowEditor name={params.id === "new" ? undefined : params.id} onChanged={() => refetch()} />
    </Show>
  </section>;
}
