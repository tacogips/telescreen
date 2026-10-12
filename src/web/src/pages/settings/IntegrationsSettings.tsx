import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { collectorsApi, integrationsApi } from "../../api/client";
import type { CollectorConfig, IntegrationStatus } from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { TopBar } from "../../components/shell/TopBar";
import { Panel, PanelItem, Tag } from "../../components/ui";

type IntegrationView = IntegrationStatus & Readonly<{ last_check_at?: string | null; last_check_ok?: boolean | null; error_code?: string | null; docs_path?: string | null }>;

/** Integration status cards and safe, permission-gated connectivity checks. */
export function IntegrationsSettings(): JSX.Element {
  const auth = useAuth();
  const [data, { refetch }] = createResource(auth.user, async (user) => {
    if (user === undefined || !user.permissions.includes("integrations.read")) return { integrations: [], collectors: [] };
    const integrations = await integrationsApi.list();
    const collectors = user.permissions.includes("collectors.read") ? (await collectorsApi.list()).items : [];
    return { integrations: integrations.items as IntegrationView[], collectors };
  });
  const [results, setResults] = createSignal<Readonly<Record<string, string>>>({});
  const [busy, setBusy] = createSignal<string | null>(null);
  const test = async (kind: string): Promise<void> => {
    setBusy(kind);
    try {
      const result = await integrationsApi.test(kind);
      setResults({ ...results(), [kind]: result.status === "ok" ? "Connection test succeeded." : result.status });
      await refetch();
    } catch (cause) {
      setResults({ ...results(), [kind]: cause instanceof Error && "code" in cause && typeof cause.code === "string" ? cause.code : "The connection test failed." });
    } finally { setBusy(null); }
  };
  const collectorsByKind = (collectors: readonly CollectorConfig[]): ReadonlyMap<string, number> => {
    const grouped = new Map<string, number>();
    for (const collector of collectors) grouped.set(collector.kind, (grouped.get(collector.kind) ?? 0) + 1);
    return grouped;
  };
  return <section class="page-content settings-page">
    <TopBar.Slot name="title">Integrations</TopBar.Slot>
    <Show when={auth.can("integrations.read")} fallback={<p role="alert">You do not have permission to view integrations.</p>}>
    <Show when={!data.loading} fallback={<p>Loading integrations…</p>}>
      <Show when={data.error === undefined} fallback={<p role="alert">Integration status could not be loaded.</p>}>
        <For each={data()?.integrations}>{(integration) => {
          const view = (): IntegrationView => integration;
          return <Panel title={integration.kind}><PanelItem><span>Configured</span><Tag variant={integration.configured ? "success" : "muted"}>{integration.configured ? "Yes" : "No"}</Tag></PanelItem>
            <PanelItem><span>Status</span><span>{integration.status}</span></PanelItem>
            <Show when={view().last_check_at}><PanelItem><span>Last check</span><time>{view().last_check_at}</time></PanelItem></Show>
            <Show when={view().error_code}><PanelItem><span>Last error</span><code>{view().error_code}</code></PanelItem></Show>
            <PanelItem><a href={view().docs_path ?? `https://github.com/tacogips/telescreen#${integration.kind}`}>Documentation</a>
              <Show when={auth.can("integrations.test")}><button class="button-secondary" disabled={busy() === integration.kind} onClick={() => void test(integration.kind)}>Test</button></Show>
            </PanelItem>
            <Show when={results()[integration.kind]}>{(message) => <p role="status">{message()}</p>}</Show>
          </Panel>;
        }}</For>
        <Panel title="Collectors"><Show when={auth.can("collectors.read")} fallback={<p>Collector details are unavailable for this account.</p>}>
          <Show when={(data()?.collectors.length ?? 0) > 0} fallback={<p>No collectors configured.</p>}>
            <For each={[...collectorsByKind(data()?.collectors ?? []).entries()]}>{([kind, count]) => <PanelItem><span>{kind}</span><strong>{count}</strong></PanelItem>}</For>
          </Show>
        </Show></Panel>
      </Show>
    </Show>
    </Show>
  </section>;
}
