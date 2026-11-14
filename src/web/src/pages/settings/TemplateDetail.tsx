import { A } from "@solidjs/router";
import { For, Show, createEffect, createMemo, createResource, createSignal, on, type JSX } from "solid-js";
import { ApiError, templatesApi } from "../../api/client";
import type { TemplateDetail as TemplateDetailData } from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { ErrorBanner } from "../../components/ErrorBanner";
import { TopBar } from "../../components/shell/TopBar";
import { Button, Panel, Placeholder, Tag } from "../../components/ui";
import { TemplateEditor } from "./TemplateEditor";
import { asApiError } from "./templateText";

function versionLabel(version: number): string {
  return version === 0 ? "Built-in default" : `Version ${version}`;
}
type TextKey = readonly [name: string, version: number];
function sameKey(left: TextKey | undefined, right: TextKey | undefined): boolean {
  return left === right || (left !== undefined && right !== undefined && left[0] === right[0] && left[1] === right[1]);
}
function defaultVersion(detail: TemplateDetailData): number {
  return detail.active_version ?? Math.max(0, ...detail.versions.map((item) => item.version));
}

/** Settings > Notification templates > one template: versions, editor, validate, preview and activate. */
export function TemplateDetail(props: Readonly<{ name: string }>): JSX.Element {
  const auth = useAuth();
  const [detail, { refetch }] = createResource(() => props.name, (name) => templatesApi.get(name));
  const [opened, setOpened] = createSignal<number | null>(null);
  const [error, setError] = createSignal<ApiError | null>(null);
  const [notice, setNotice] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const loaded = (): TemplateDetailData | undefined => detail.error ? undefined : detail.latest;
  const selectedVersion = (): number | undefined => { const value = loaded(); return value === undefined || value.name !== props.name ? undefined : opened() ?? defaultVersion(value); };
  createEffect(on(() => props.name, () => setOpened(null), { defer: true }));
  // Compared by value: a detail refetch that keeps the same selection must not reload the text and reset the editor.
  const textKey = createMemo((): TextKey | undefined => { const version = selectedVersion(); return version === undefined ? undefined : [props.name, version]; }, undefined, { equals: sameKey });
  const [text] = createResource(textKey, ([name, version]) => templatesApi.version(name, version));
  const loadError = (): ApiError | undefined => detail.error === undefined ? undefined : asApiError(detail.error, "The template could not be loaded.");
  const activate = async (version: number): Promise<void> => {
    setBusy(true); setError(null); setNotice("");
    // Keep the displayed version open so a changed active version does not replace unsaved editor text.
    setOpened(selectedVersion() ?? null);
    try { await templatesApi.activate(props.name, version); setNotice(`${versionLabel(version)} is active.`); await refetch(); }
    catch (cause) { setError(asApiError(cause, "The version could not be activated.")); }
    finally { setBusy(false); }
  };

  return <section class="page-content settings-page template-detail">
    <TopBar.Slot name="title">{props.name}</TopBar.Slot>
    <p><A href="/settings/templates">Notification templates</A></p>
    <Show when={loadError() ?? error()} keyed>{(failure) => <ErrorBanner error={failure} />}</Show>
    <Show when={notice()}>{(message) => <p class="notice" role="status">{message()}</p>}</Show>
    <Show when={loaded()} fallback={<Show when={!detail.error}><Placeholder label="Loading template…" /></Show>}>{(value) => <>
      <Panel><div class="detail-grid"><div><span class="muted">Event kind</span><code>{value().event_kind}</code></div><div><span class="muted">Type</span><Tag variant={value().builtin ? "muted" : "info"}>{value().builtin ? "Built-in" : "Custom"}</Tag></div><div><span class="muted">Active version</span><strong>{value().active_version === null ? "—" : versionLabel(value().active_version ?? 0)}</strong></div></div></Panel>
      <Panel title="Versions"><ul class="version-list"><For each={value().versions}>{(version) => <li>
        <span>{versionLabel(version.version)} <Show when={version.active}><Tag variant="success">Active</Tag></Show><Show when={selectedVersion() === version.version}> <span class="muted">(open)</span></Show></span>
        <span><Button size="sm" disabled={busy()} aria-label={`Open ${versionLabel(version.version)}`} onClick={() => setOpened(version.version)}>Open</Button><Show when={auth.can("templates.write") && !version.active}><Button size="sm" disabled={busy()} aria-label={`Activate ${versionLabel(version.version)}`} onClick={() => void activate(version.version)}>Activate</Button></Show></span>
      </li>}</For></ul></Panel>
      <Panel title="Editor">
        <Show when={!text.error} fallback={<ErrorBanner error={asApiError(text.error, "The version text could not be loaded.")} />}>
          <TemplateEditor mode="version" name={value().name} eventKind={value().event_kind} initial={text.error ? undefined : text.latest} variables={value().variables} canWrite={auth.can("templates.write")} onSaved={() => void refetch()} />
        </Show>
      </Panel>
    </>}</Show>
  </section>;
}
