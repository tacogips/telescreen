import { A, useNavigate, useParams } from "@solidjs/router";
import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { ApiError, alertsApi, apiFetch } from "../../api/client";
import { useAuth } from "../../auth/useAuth";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { TopBar } from "../../components/shell/TopBar";
import { Button, EmptyState, Panel, Placeholder, Table, Tag } from "../../components/ui";
import { AlertEditor } from "./AlertEditor";
import { AlertHistory } from "./AlertHistory";
import type { RichAlertRule } from "./alertTypes";

function asError(value: unknown): ApiError | null {
  return value === undefined ? null : value instanceof ApiError ? value : new ApiError(0, "request_failed", "Alert rules could not be loaded.");
}

/** Alerts list, editor and per-rule history. */
export default function AlertsPage(): JSX.Element {
  const params = useParams<{ id?: string }>();
  const auth = useAuth();
  const navigate = useNavigate();
  const [rules, { refetch }] = createResource(async () => (await alertsApi.list()).items as RichAlertRule[]);
  const [detail] = createResource(() => params.id && params.id !== "new" ? params.id : undefined, async (id) => alertsApi.get(id) as Promise<RichAlertRule>);
  const [historyTab, setHistoryTab] = createSignal(true);
  const [actionError, setActionError] = createSignal<ApiError | null>(null);
  const isNew = (): boolean => params.id === "new";
  const current = (): RichAlertRule | undefined => detail.latest;
  async function mutate(id: string, action: "enable" | "disable" | "unmute" | "mute", hours = 1): Promise<void> {
    setActionError(null);
    try {
      if (action === "enable") await alertsApi.enable(id);
      else if (action === "disable") await alertsApi.disable(id);
      else if (action === "unmute") await alertsApi.unmute(id);
      else await apiFetch(`/api/alert-rules/${encodeURIComponent(id)}/mute`, { method: "POST", json: { minutes: hours * 60 } });
      await refetch();
    } catch (cause) { setActionError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The alert action could not be completed.")); }
  }
  async function removeRule(rule: RichAlertRule): Promise<void> {
    if (!window.confirm(`Delete alert rule “${rule.name}”?`)) return;
    setActionError(null);
    try { await alertsApi.delete(rule.id); await refetch(); }
    catch (cause) { setActionError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The alert rule could not be deleted.")); }
  }
  const ruleStatus = (rule: RichAlertRule): string => rule.status ?? (rule.muted_until && Date.parse(rule.muted_until) > Date.now() ? "muted" : rule.enabled ? "enabled" : "disabled");
  const triggerName = (rule: RichAlertRule): string => typeof rule.trigger === "string" ? rule.trigger : rule.trigger.kind;
  const filtersSummary = (rule: RichAlertRule): string => {
    const filters = rule.filters;
    if (!filters) return rule.query || "Any issue";
    const values = [filters.min_severity, ...filters.priorities, ...filters.sources, ...filters.rule_classes, ...filters.exposures, ...filters.owner_teams, ...filters.detectors].filter((value): value is string => value !== null && value.length > 0);
    return values.length > 0 ? values.join(", ") : "Any issue";
  };
  const actionsSummary = (rule: RichAlertRule): string => {
    const count = rule.actions?.targets.length ?? 0;
    return `${count > 0 ? `${count} target(s)` : "No notification target"}${rule.actions?.start_workflow ? " · workflow" : ""}`;
  };
  return <section class="page-content alerts-page">
    <TopBar.Slot name="title">Alerts</TopBar.Slot>
    <TopBar.Slot name="actions"><Show when={auth.can("alerts.write") && params.id === undefined}><Button variant="primary" onClick={() => navigate("/alerts/new")}>New alert rule</Button></Show></TopBar.Slot>
    <Show when={actionError() ?? asError(rules.error) ?? asError(detail.error)} keyed>{(error) => <ErrorBanner error={error} />}</Show>
    <Show when={params.id === undefined}>
      <Show when={!rules.loading} fallback={<Placeholder label="Loading alert rules…" />}>
        <Show when={(rules()?.length ?? 0) > 0} fallback={<EmptyState title="No alert rules"><p>Create a rule to notify your team when an issue matches.</p></EmptyState>}>
          <Table><thead><tr><th>Name</th><th>Trigger</th><th>Filters</th><th>Actions</th><th>Interval</th><th>Status</th><th>Last fired</th><th>Fires (24h)</th><th>Actions</th></tr></thead><tbody>
            <For each={rules()}>{(item) => { const rule = item as RichAlertRule; const status = ruleStatus(rule); return <tr>
              <td><A href={`/alerts/${encodeURIComponent(rule.id)}`}>{rule.name}</A></td><td>{triggerName(rule)}</td><td>{filtersSummary(rule)}</td><td>{actionsSummary(rule)}</td><td>{rule.action_interval_minutes} min</td>
              <td><Tag variant={status === "enabled" ? "success" : status === "muted" ? "warning" : "muted"}>{status === "muted" && rule.muted_until ? `Muted until ${new Date(rule.muted_until).toLocaleString()}` : status}</Tag></td>
              <td><Show when={rule.last_fired_at} fallback="—">{(at) => <RelativeTime iso={at()} />}</Show></td><td>{rule.fires_24h ?? "—"}</td>
              <td><Show when={auth.can("alerts.write")}><A href={`/alerts/${encodeURIComponent(rule.id)}?edit=1`}>Edit</A><Button size="sm" onClick={() => void mutate(rule.id, rule.enabled ? "disable" : "enable")}>{rule.enabled ? "Disable" : "Enable"}</Button><Button size="sm" onClick={() => void removeRule(rule)}>Delete</Button></Show>
                <Show when={auth.can("alerts.mute")}><Show when={status === "muted"} fallback={<div class="inline-actions"><Button size="sm" onClick={() => void mutate(rule.id, "mute", 1)}>Mute 1h</Button><Button size="sm" onClick={() => void mutate(rule.id, "mute", 24)}>Mute 24h</Button><Button size="sm" onClick={() => void mutate(rule.id, "mute", 168)}>Mute 7d</Button></div>}><Button size="sm" onClick={() => void mutate(rule.id, "unmute")}>Unmute</Button></Show></Show>
              </td>
            </tr>; }}</For>
          </tbody></Table>
        </Show>
      </Show>
    </Show>
    <Show when={isNew()}><Panel title="New alert rule"><AlertEditor canWrite={auth.can("alerts.write")} onSaved={() => { void refetch(); navigate("/alerts"); }} onCancel={() => navigate("/alerts")} /></Panel></Show>
    <Show when={params.id !== undefined && !isNew()}>
      <Show when={current()} fallback={<Show when={!detail.loading}><Placeholder label="Loading alert rule…" /></Show>}>
        {(rule) => <><p><A href="/alerts">Alerts</A> / {rule().name}</p>
          <Show when={auth.can("alerts.write")}><Panel title="Edit alert rule"><AlertEditor initial={rule()} canWrite onSaved={() => void refetch()} onCancel={() => navigate(`/alerts/${encodeURIComponent(rule().id)}`)} /></Panel></Show>
          <Panel title={rule().name}><p>{filtersSummary(rule())}</p><div role="tablist" aria-label="Alert details"><button role="tab" aria-selected={!historyTab()} onClick={() => setHistoryTab(false)}>Overview</button><button role="tab" aria-selected={historyTab()} onClick={() => setHistoryTab(true)}>History</button></div>
            <Show when={historyTab()}><AlertHistory ruleId={rule().id} /></Show><Show when={!historyTab()}><p>{triggerName(rule())} · {rule().action_interval_minutes} minute action interval</p></Show>
          </Panel>
        </>}
      </Show>
    </Show>
    <Show when={!auth.can("alerts.read")}><Panel title="Permission required"><p>You do not have permission to view alert rules.</p></Panel></Show>
  </section>;
}
