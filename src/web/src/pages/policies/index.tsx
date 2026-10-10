import { A, useNavigate, useParams } from "@solidjs/router";
import { createEffect, createResource, createSignal, For, Show } from "solid-js";
import { ApiError, policiesApi } from "../../api/client";
import type { PolicySummary, PolicyVersion } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { TopBar } from "../../components/shell/TopBar";
import { EmptyState, Panel, PanelItem, Placeholder, Table, Tag, showToast } from "../../components/ui";

export default function PoliciesPage() {
  const params = useParams<{ name?: string }>();
  const navigate = useNavigate();
  const routeName = (): string | undefined => params.name === undefined ? undefined : decodeURIComponent(params.name);
  const [policies, { refetch: refetchPolicies }] = createResource(async () => (await policiesApi.list()).items);
  const [detail, { refetch: refetchDetail }] = createResource(() => routeName(), async (name) => name ? policiesApi.get(name) : undefined);
  const [format, setFormat] = createSignal<"yaml" | "json">("yaml");
  const [source, setSource] = createSignal("");
  const [errors, setErrors] = createSignal<Readonly<{ path: string; code: string }[]>>([]);
  const [notice, setNotice] = createSignal("");
  const [error, setError] = createSignal<ApiError | null>(null);
  const [busy, setBusy] = createSignal(false);
  const currentPolicy = (): PolicySummary | undefined => policies()?.find((policy) => policy.name === routeName());
  const versions = (): PolicyVersion[] => detail()?.versions ?? [];
  createEffect(() => {
    const list = detail()?.versions ?? [];
    const initial = list.find((version) => version.active) ?? list.at(-1);
    if (initial !== undefined) { setSource(initial.source_text); setFormat(initial.source_format); }
  });

  async function validate(): Promise<void> {
    setBusy(true); setError(null); setNotice("");
    try { const result = await policiesApi.validate({ source_format: format(), source_text: source() }); setErrors(result.errors); setNotice(result.valid ? "Policy is valid." : "Review the validation errors."); }
    catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "Policy validation failed.")); }
    finally { setBusy(false); }
  }

  async function save(): Promise<void> {
    setBusy(true); setError(null); setNotice("");
    try {
      const saved = await policiesApi.create({ source_format: format(), source_text: source() });
      setSource(saved.source_text); setFormat(saved.source_format); setNotice(`Saved version ${saved.version}.`);
      if (params.name === undefined || params.name === "new") navigate(`/policies/${encodeURIComponent(saved.name)}`);
      await Promise.all([refetchPolicies(), refetchDetail()]);
    } catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The policy could not be saved.")); }
    finally { setBusy(false); }
  }

  async function loadVersion(version: number): Promise<void> {
    const name = routeName();
    if (name === undefined || name === "new") return;
    try { const value = await policiesApi.version(name, version); setSource(value.source_text); setFormat(value.source_format); setNotice(`Loaded version ${version}.`); }
    catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The policy version could not be loaded.")); }
  }

  async function activate(version: number): Promise<void> {
    const name = routeName();
    if (name === undefined || name === "new") return;
    setBusy(true); setError(null);
    try { await policiesApi.activate(name, version); await Promise.all([refetchPolicies(), refetchDetail()]); setNotice(`Version ${version} is active.`); }
    catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The policy version could not be activated.")); }
    finally { setBusy(false); }
  }

  async function disable(): Promise<void> {
    const name = routeName();
    if (name === undefined || name === "new") return;
    setBusy(true); setError(null);
    try { await policiesApi.disable(name); await refetchPolicies(); showToast("Policy disabled", "success"); }
    catch (cause) { setError(cause instanceof ApiError ? cause : new ApiError(0, "request_failed", "The policy could not be disabled.")); }
    finally { setBusy(false); }
  }

  return <section class="page-content">
    <TopBar.Slot name="title">{routeName() && routeName() !== "new" ? routeName() : "Policies"}</TopBar.Slot>
    <TopBar.Slot name="actions"><Show when={routeName() !== undefined && routeName() !== "new"} fallback={<A class="button-primary" href="/policies/new">New policy</A>}><button class="button-secondary" disabled={busy()} onClick={() => void validate()}>Validate</button><button class="button-primary" disabled={busy()} onClick={() => void save()}>Save as new version</button></Show></TopBar.Slot>
    <Show when={error()}>{(value) => <ErrorBanner error={value()} />}</Show><Show when={notice()}>{(value) => <p role="status">{value()}</p>}</Show>
    <Show when={routeName() === undefined}>
      <Show when={!policies.loading} fallback={<Placeholder label="Loading policies…" />}><Show when={(policies()?.length ?? 0) > 0} fallback={<EmptyState title="No policies"><p>Create a placement policy to evaluate exposure risk.</p><A href="/policies/new">Create policy</A></EmptyState>}><Table><thead><tr><th>Name</th><th>Active version</th><th>Effect</th><th>Enabled</th><th>Owner team</th></tr></thead><tbody><For each={policies()}>{(policy) => <tr><td><A href={`/policies/${encodeURIComponent(policy.name)}`}>{policy.name}</A></td><td>{policy.active_version ?? "—"}</td><td>{policy.effect}</td><td>{policy.enabled ? "Yes" : "No"}</td><td>{policy.owner_team ?? "—"}</td></tr>}</For></tbody></Table></Show></Show>
    </Show>
    <Show when={routeName() === "new"}><Panel title="New policy"><label class="field">Format<select value={format()} onChange={(event) => setFormat(event.currentTarget.value as "yaml" | "json")}><option value="yaml">YAML</option><option value="json">JSON</option></select></label><label class="field">Policy source<textarea aria-label="Policy source" rows="18" spellcheck={false} value={source()} onInput={(event) => setSource(event.currentTarget.value)} /></label><ValidationErrors errors={errors()} /><div class="row-actions"><button class="button-secondary" disabled={busy()} onClick={() => void validate()}>Validate</button><button class="button-primary" disabled={busy()} onClick={() => void save()}>Save as new version</button></div></Panel></Show>
    <Show when={routeName() !== undefined && routeName() !== "new"}>
      <Show when={!detail.loading} fallback={<Placeholder label="Loading policy…" />}><Show when={detail.latest} fallback={<EmptyState title="Policy not found"><A href="/policies">Back to policies</A></EmptyState>}>
        <Panel title="Policy editor"><div class="editor-toolbar"><label class="field">Format<select value={format()} onChange={(event) => setFormat(event.currentTarget.value as "yaml" | "json")}><option value="yaml">YAML</option><option value="json">JSON</option></select></label><Show when={currentPolicy()}>{(policy) => <><Tag>{policy().effect}</Tag><Tag>{policy().enabled ? "Enabled" : "Disabled"}</Tag><button class="button-secondary" disabled={busy() || !policy().enabled} onClick={() => void disable()}>Disable</button></>}</Show></div><label class="field">Policy source<textarea aria-label="Policy source" spellcheck={false} rows="18" value={source()} onInput={(event) => setSource(event.currentTarget.value)} /></label><ValidationErrors errors={errors()} /></Panel>
        <Panel title="Versions"><For each={versions()}>{(version) => <PanelItem><span>Version {version.version} {version.active ? <Tag variant="success">Active</Tag> : null}</span><div class="row-actions"><button class="button-secondary" onClick={() => void loadVersion(version.version)}>Open</button><Show when={!version.active}><button class="button-primary" disabled={busy()} onClick={() => void activate(version.version)}>Activate</button></Show></div></PanelItem>}</For></Panel>
      </Show></Show>
    </Show>
  </section>;
}

function ValidationErrors(props: Readonly<{ errors: readonly Readonly<{ path: string; code: string }>[] }>) {
  return <Show when={props.errors.length > 0}><ul aria-label="Validation errors"><For each={props.errors}>{(item) => <li><code>{item.path}</code>: {item.code}</li>}</For></ul></Show>;
}
