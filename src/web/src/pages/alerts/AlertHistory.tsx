import { For, Show, createMemo, createResource, createSignal, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import { apiFetch } from "../../api/client";
import { RelativeTime } from "../../components/RelativeTime";
import { Button, Table, Tag } from "../../components/ui";
import type { RichAlertFire } from "./alertTypes";

/** Displays rule fires in pages and links to their issue and workflow run. */
export function AlertHistory(props: Readonly<{ ruleId: string }>): JSX.Element {
  const [page, setPage] = createSignal(0);
  const [cursors, setCursors] = createSignal<readonly (string | null)[]>([null]);
  const [history, { refetch }] = createResource(() => [props.ruleId, cursors().at(-1)] as const, async ([id, cursor]) => {
    const endpoint = `/api/alert-rules/${encodeURIComponent(id)}/history${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`;
    return await apiFetch<Readonly<{ items: RichAlertFire[]; next_cursor: string | null }>>(endpoint);
  });
  const items = createMemo(() => history()?.items ?? []);
  return <section aria-label="Alert history">
    <Table><thead><tr><th>Time</th><th>Issue</th><th>Trigger</th><th>Outcome</th><th>Notifications</th><th>Run</th></tr></thead><tbody>
      <For each={items()}>{(fire) => <tr><td><RelativeTime iso={fire.fired_at ?? fire.at ?? ""} /></td><td><A href={`/issues/${encodeURIComponent(fire.issue_id)}`}>{fire.issue_id}</A></td><td>{fire.trigger_kind ?? fire.trigger ?? "Alert rule"}</td><td><Tag variant={fire.outcome === "delivered" ? "success" : fire.outcome === "failed" || fire.outcome === "blocked" ? "warning" : "muted"}>{fire.outcome}</Tag></td><td>{fire.notification_count ?? "—"}</td><td><Show when={fire.run_id} fallback="—">{(runId) => <A href={`/runs/${encodeURIComponent(runId())}`}>View run</A>}</Show></td></tr>}</For>
    </tbody></Table>
    <Show when={(history()?.items.length ?? 0) > 0} fallback={<p>No alert fires yet.</p>}>
      <nav aria-label="History pages" class="pagination"><Button size="sm" disabled={page() === 0} onClick={() => { setCursors(cursors().slice(0, -1)); setPage(Math.max(0, page() - 1)); void refetch(); }}>Previous</Button><span>Page {page() + 1}</span><Button size="sm" disabled={!history()?.next_cursor} onClick={() => { const cursor = history()?.next_cursor; if (cursor) { setCursors([...cursors(), cursor]); setPage(page() + 1); } }}>Next</Button></nav>
    </Show>
  </section>;
}
