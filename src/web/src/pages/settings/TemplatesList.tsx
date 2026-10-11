import { A, useNavigate } from "@solidjs/router";
import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { ApiError, templatesApi } from "../../api/client";
import { useAuth } from "../../auth/useAuth";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { TopBar } from "../../components/shell/TopBar";
import { Button, EmptyState, Panel, Placeholder, Table, Tag } from "../../components/ui";
import { TemplateEditor } from "./TemplateEditor";

/** Settings > Notification templates: built-in and custom templates with their versions. */
export function TemplatesList(): JSX.Element {
  const auth = useAuth();
  const navigate = useNavigate();
  const [templates] = createResource(async () => (await templatesApi.list()).items);
  const [creating, setCreating] = createSignal(false);
  const loadError = (): ApiError | undefined => templates.error === undefined ? undefined : templates.error instanceof ApiError ? templates.error : new ApiError(0, "request_failed", "Templates could not be loaded.");
  return <section class="page-content settings-page">
    <TopBar.Slot name="title">Notification templates</TopBar.Slot>
    <TopBar.Slot name="actions"><Show when={auth.can("templates.write") && !creating()}><Button variant="primary" onClick={() => setCreating(true)}>New template</Button></Show></TopBar.Slot>
    <Show when={loadError()} keyed>{(failure) => <ErrorBanner error={failure} />}</Show>
    <Show when={creating() && auth.can("templates.write")}>
      <Panel title="New template"><TemplateEditor mode="create" variables={[]} canWrite onSaved={(saved) => navigate(`/settings/templates/${encodeURIComponent(saved.name)}`)} onCancel={() => setCreating(false)} /></Panel>
    </Show>
    <Show when={!templates.loading} fallback={<Placeholder label="Loading templates…" />}>
      <Show when={(templates()?.length ?? 0) > 0} fallback={<Show when={!templates.error}><EmptyState title="No templates"><p>Built-in templates appear once the Bureau is reachable.</p></EmptyState></Show>}>
        <Table><thead><tr><th>Name</th><th>Event kind</th><th>Type</th><th>Active version</th><th>Latest version</th><th>Updated</th></tr></thead><tbody>
          <For each={templates()}>{(item) => <tr>
            <td><A href={`/settings/templates/${encodeURIComponent(item.name)}`}>{item.name}</A></td>
            <td><code>{item.event_kind}</code></td>
            <td><Tag variant={item.builtin ? "muted" : "info"}>{item.builtin ? "Built-in" : "Custom"}</Tag></td>
            <td>{item.active_version ?? "—"}</td>
            <td>{item.latest_version}</td>
            <td><Show when={item.updated_at} fallback="—">{(at) => <RelativeTime iso={at()} />}</Show></td>
          </tr>}</For>
        </tbody></Table>
      </Show>
    </Show>
  </section>;
}
