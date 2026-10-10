import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, policeApi } from "../../api/client";
import type { PoliceWorker } from "../../api/types";
import { RelativeTime } from "../../components/RelativeTime";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Drawer, EmptyState, Placeholder, StatusIndicator, Table, Tag, showToast } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";

function resourceError(failure: unknown): ApiError | null {
  if (failure === undefined || failure === null) return null;
  return failure instanceof ApiError ? failure : new ApiError(0, "request_failed", "The request could not be completed.");
}

function workerHealth(worker: PoliceWorker): Readonly<{ status: string; label: string; conditions: string }> {
  if (worker.status === "disabled") return { status: "muted", label: "Disabled", conditions: "Worker disabled" };
  if (worker.health !== undefined) {
    const status = worker.health.status === "ok" ? "success" : worker.health.status === "warning" ? "warning" : worker.health.status === "failing" ? "danger" : "muted";
    const conditions = worker.health.conditions.length === 0 ? "No active health conditions" : worker.health.conditions.join(", ");
    return { status, label: worker.health.status, conditions };
  }
  const heartbeat = worker.last_heartbeat_at === null ? Number.NaN : Date.parse(worker.last_heartbeat_at);
  const healthy = Number.isFinite(heartbeat) && Date.now() - heartbeat <= 5 * 60 * 1000;
  return healthy ? { status: "success", label: "Healthy", conditions: "No active health conditions" } : { status: "warning", label: "Stale", conditions: "Heartbeat is stale" };
}

export default function PolicePage() {
  const [workers, { refetch }] = createResource(async () => (await policeApi.workers()).items);
  const [selected, setSelected] = createSignal<PoliceWorker | null>(null);
  const [error, setError] = createSignal<ApiError | null>(null);

  async function disable(worker: PoliceWorker): Promise<void> {
    setError(null);
    try {
      await policeApi.disable(worker.id);
      setSelected(null);
      await refetch();
      showToast("Police worker disabled", "success");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed."));
    }
  }

  return (
    <section class="page-content">
      <TopBar.Slot name="title">Police</TopBar.Slot>
      <TopBar.Slot name="actions"><Tag>{workers()?.length ?? 0} workers</Tag></TopBar.Slot>
      <Show when={error() ?? resourceError(workers.error)}>{(failure) => <ErrorBanner error={failure()} />}</Show>
      <Show when={!workers.loading} fallback={<Placeholder label="Loading Police workers…" />}>
        <Show when={(workers()?.length ?? 0) > 0} fallback={<EmptyState title="No Police workers registered"><p>Register a Police worker to verify remediation tasks.</p></EmptyState>}>
          <Table>
            <thead><tr><th>Name</th><th>Mode</th><th>Status</th><th>Last heartbeat</th><th>Task types</th><th>Labels</th><th>Version</th></tr></thead>
            <tbody><For each={workers()}>{(worker) => {
              const health = workerHealth(worker);
              return <tr class="clickable-row" onClick={() => setSelected(worker)}>
                <td><strong>{worker.name}</strong></td><td><Tag>{worker.mode}</Tag></td>
                <td><span title={health.conditions}><StatusIndicator status={health.status} label={health.label} /></span></td>
                <td>{worker.last_heartbeat_at === null ? "Never" : <RelativeTime iso={worker.last_heartbeat_at} />}</td>
                <td>{worker.task_types.join(", ") || "—"}</td><td>{worker.labels.join(", ") || "—"}</td><td>{worker.version}</td>
              </tr>;
            }}</For></tbody>
          </Table>
        </Show>
      </Show>
      <Drawer open={selected() !== null} title="Police worker" onClose={() => setSelected(null)}>
        <Show when={selected()}>{(worker) => <div class="page-drawer-content">
          <h2>{worker().name}</h2><dl>
            <dt>Worker ID</dt><dd>{worker().id}</dd><dt>Mode</dt><dd>{worker().mode}</dd>
            <dt>Status</dt><dd title={workerHealth(worker()).conditions}>{workerHealth(worker()).label}</dd><dt>Health conditions</dt><dd>{workerHealth(worker()).conditions}</dd><dt>Version</dt><dd>{worker().version}</dd>
            <dt>Task types</dt><dd>{worker().task_types.join(", ") || "—"}</dd>
            <dt>Labels</dt><dd>{worker().labels.join(", ") || "—"}</dd><dt>Concurrency</dt><dd>{worker().concurrency}</dd>
            <dt>Last heartbeat</dt><dd>{worker().last_heartbeat_at ?? "Never"}</dd>
          </dl>
          <button class="button-danger" disabled={worker().status === "disabled"} onClick={() => void disable(worker())}>Disable worker</button>
        </div>}</Show>
      </Drawer>
    </section>
  );
}
