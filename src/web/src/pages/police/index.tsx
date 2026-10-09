import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, policeApi } from "../../api/client";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { StatusBadge } from "../../components/StatusBadge";

export default function PolicePage() {
  const [workers, { refetch }] = createResource(async () => (await policeApi.workers()).items);
  const [error, setError] = createSignal<ApiError | null>(null);

  async function disable(id: string): Promise<void> {
    setError(null);
    try {
      await policeApi.disable(id);
      await refetch();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed."));
    }
  }

  return (
    <section>
      <h1>Police workers</h1>
      <ErrorBanner error={error()} />
      <Show when={!workers.loading} fallback={<p>Loading workers…</p>}>
        <table>
          <thead><tr><th>Name</th><th>Mode</th><th>Labels</th><th>Task types</th><th>Status</th><th>Heartbeat</th><th>Actions</th></tr></thead>
          <tbody>
            <For each={workers()} fallback={<tr><td colspan="7">No Police workers registered.</td></tr>}>
              {(worker) => <tr>
                <td>{worker.name} <small>{worker.version}</small></td>
                <td>{worker.mode}</td>
                <td>{worker.labels.join(", ") || "—"}</td>
                <td>{worker.task_types.join(", ") || "—"}</td>
                <td><StatusBadge status={worker.status} /></td>
                <td>{worker.last_heartbeat_at === null ? "Never" : <RelativeTime iso={worker.last_heartbeat_at} />}</td>
                <td><button class="button-secondary" disabled={worker.status === "disabled"} onClick={() => void disable(worker.id)}>Disable</button></td>
              </tr>}
            </For>
          </tbody>
        </table>
      </Show>
    </section>
  );
}
