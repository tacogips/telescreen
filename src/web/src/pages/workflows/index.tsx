import { A, useParams } from "@solidjs/router";
import { createResource, For, Show, type JSX } from "solid-js";
import { workflowsApi } from "../../api/client";
import type { Workflow } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { StatusBadge } from "../../components/StatusBadge";
import { WorkflowEditor } from "./WorkflowEditor";

type WorkflowSummary = Workflow & Readonly<{ latest_version?: number | null; trigger_summary?: string }>;

export default function WorkflowsPage(): JSX.Element {
  const params = useParams<{ id?: string }>();
  const [workflows, { refetch }] = createResource(() => workflowsApi.list());

  return (
    <section class="workflow-page">
      <Show when={params.id} fallback={<>
        <div class="page-heading"><div><h1>Workflows</h1><p>Manage validated workflow definitions and active versions.</p></div><A class="button-secondary" href="/workflows/new">New workflow</A></div>
        <Show when={!workflows.error} fallback={<ErrorBanner error={workflows.error ?? null} />}>
          <Show when={!workflows.loading} fallback={<p>Loading workflows...</p>}>
            <Show when={(workflows()?.items.length ?? 0) > 0} fallback={<div class="empty-state">No workflows yet. Create a workflow to start a response process.</div>}>
              <div class="table-scroll"><table><thead><tr><th>Name</th><th>Latest version</th><th>Active version</th><th>Updated</th></tr></thead><tbody>
                <For each={workflows()?.items ?? []}>{(workflow) => <tr>
                  <td><A href={`/workflows/${encodeURIComponent(workflow.name)}`}>{workflow.title || workflow.name}</A><div class="muted">{workflow.name}</div></td>
                  <td>{(workflow as WorkflowSummary).latest_version ?? workflow.active_version ?? "—"}</td>
                  <td>{workflow.active_version ?? "Not active"}</td>
                  <td>{(workflow as WorkflowSummary).trigger_summary ?? workflow.description ?? "Manual"}<div class="muted"><RelativeTime iso={workflow.updated_at} /></div></td>
                </tr>}</For>
              </tbody></table></div>
            </Show>
          </Show>
        </Show>
      </>}>
        <WorkflowEditor name={params.id === "new" ? undefined : params.id} onChanged={() => refetch()} />
      </Show>
    </section>
  );
}
