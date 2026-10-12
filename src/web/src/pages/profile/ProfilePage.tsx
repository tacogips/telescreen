import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { ApiError, meApi, viewsApi } from "../../api/client";
import type { NotificationSetting, SavedView, SelfToken } from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { ErrorBanner } from "../../components/ErrorBanner";
import { getTheme, setTheme, type ThemeChoice } from "../../components/shell/theme";
import { Button, Panel, Placeholder, Select, Table, Tag } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";

type Channel = "email" | "slack" | "off";
type ProfileSetting = Readonly<{ category: string; stored: Channel | null; preference: Channel; effective?: Channel; available?: Readonly<{ email?: boolean; slack?: boolean }> }>;
type SettingResponse = Readonly<{ category: string; stored?: Channel | null; preference?: Channel; effective?: Channel; available?: Readonly<{ email?: boolean; slack?: boolean }> }>;
type CreatedSecret = Readonly<{ id: string; token: string }>;
const categories: readonly Readonly<{ id: string; name: string; configured: boolean }>[] = [
  { id: "assigned", name: "Assigned to me", configured: false }, { id: "mentions", name: "Mentions", configured: false },
  { id: "subscribed", name: "Subscribed issues", configured: false }, { id: "alerts", name: "Alert rules", configured: true },
  { id: "workflow_tasks", name: "Workflow tasks", configured: true }, { id: "weekly_report", name: "Weekly report", configured: false },
];
const channelChoices: readonly Channel[] = ["email", "slack", "off"];

function safeError(value: unknown, fallback: string): ApiError | null {
  return value === undefined ? null : value instanceof ApiError ? value : new ApiError(0, "request_failed", fallback);
}

function mapSettings(items: readonly (NotificationSetting | ProfileSetting)[]): ProfileSetting[] {
  return categories.map((category) => {
    const found = items.find((item) => item.category === category.id) as SettingResponse | undefined;
    if (found === undefined) return { category: category.id, stored: null, preference: "email" };
    const stored = "stored" in found && found.stored !== undefined
      ? found.stored
      : category.configured ? null : found.preference ?? null;
    return { category: category.id, stored, preference: found.effective ?? found.preference ?? "email", effective: found.effective, available: found.available };
  });
}

/** Profile notification preferences, personal tokens, default view, and theme. */
export default function ProfilePage(): JSX.Element {
  const auth = useAuth();
  const [settings, setSettings] = createSignal<ProfileSetting[]>([]);
  const [settingsResource] = createResource(async () => { const result = await meApi.notificationSettings() as unknown as Readonly<{ items: ProfileSetting[] }>; const mapped = mapSettings(result.items); setSettings(mapped); return mapped; });
  const [tokens, { refetch: refetchTokens }] = createResource(async () => (await meApi.tokens()).items);
  const [views, { refetch: refetchViews }] = createResource(async () => {
    const items = (await viewsApi.list()).items;
    setSelectedView(items.find((view) => view.is_default)?.id ?? "");
    return items;
  });
  const [theme, setThemeChoice] = createSignal<ThemeChoice>(getTheme());
  const [selectedView, setSelectedView] = createSignal("");
  const [label, setLabel] = createSignal("");
  const [scopes, setScopes] = createSignal<string[]>(["read"]);
  const [secret, setSecret] = createSignal<CreatedSecret | null>(null);
  const [busy, setBusy] = createSignal(false);
  const [notice, setNotice] = createSignal("");
  const [failure, setFailure] = createSignal<ApiError | null>(null);
  const role = (): string => auth.user()?.role ?? "viewer";
  const offeredScopes = (): readonly string[] => role() === "admin" ? ["read", "write", "admin"] : role() === "responder" ? ["read", "write"] : ["read"];
  const defaultId = (): string => views()?.find((view) => view.is_default)?.id ?? "";

  async function savePreference(category: string, selection: string): Promise<void> {
    const configured = selection === "as_configured";
    const effective = settings().find((item) => item.category === category)?.effective ?? "email";
    const chosen = configured ? effective : selection as Channel;
    const updated = settings().map((item) => item.category === category ? { ...item, stored: configured ? null : chosen, preference: chosen } : item);
    setSettings(updated); setFailure(null);
    const payload = updated.map((item) => ({ category: item.category, stored: item.stored, preference: item.preference }));
    try { await meApi.updateNotificationSettings(payload); }
    catch (cause) { setFailure(safeError(cause, "Notification settings could not be saved.")); }
  }

  async function createToken(event: SubmitEvent): Promise<void> {
    event.preventDefault(); setFailure(null); setBusy(true);
    try {
      const result = await meApi.createToken({ label: label(), scopes: scopes() });
      setSecret(result); setLabel(""); await refetchTokens();
    } catch (cause) { setFailure(safeError(cause, "The token could not be created.")); }
    finally { setBusy(false); }
  }

  async function revoke(token: SelfToken): Promise<void> {
    if (!window.confirm(`Revoke token “${token.label}”?`)) return;
    setFailure(null);
    try { await meApi.revokeToken(token.id); await refetchTokens(); }
    catch (cause) { setFailure(safeError(cause, "The token could not be revoked.")); }
  }

  async function setDefault(id: string): Promise<void> {
    setSelectedView(id); setFailure(null);
    try { if (id) await viewsApi.setDefault(id); await refetchViews(); }
    catch (cause) { setFailure(safeError(cause, "The default view could not be saved.")); }
  }

  return <section class="page-content profile-page">
    <TopBar.Slot name="title">Profile</TopBar.Slot>
    <Show when={failure()} keyed>{(error) => <ErrorBanner error={error} />}</Show>
    <Show when={notice()}><p role="status" class="notice">{notice()}</p></Show>
    <Show when={!settingsResource.loading && !tokens.loading && !views.loading} fallback={<Placeholder label="Loading profile…" />}>
      <Panel title="Notification settings"><p>Choose where each notification category is sent. “As configured” follows the organization setting.</p>
        <Table><thead><tr><th>Category</th><th>Channel</th></tr></thead><tbody>
          <For each={categories}>{(category) => { const setting = (): ProfileSetting | undefined => settings().find((item) => item.category === category.id); const selected = (): string => category.configured && setting()?.stored === null ? "as_configured" : setting()?.preference ?? "email"; return <tr>
            <th scope="row">{category.name}</th><td><Select aria-label={`${category.name} channel`} value={selected()} onChange={(event) => void savePreference(category.id, event.currentTarget.value)}>
              <Show when={category.configured}><option value="as_configured">As configured</option></Show>
              <For each={channelChoices}>{(channel) => <option value={channel} disabled={channel === "slack" && setting()?.available?.slack === false}>{channel === "off" ? "Off" : channel}</option>}</For>
            </Select></td>
          </tr>; }}</For>
        </tbody></Table>
      </Panel>
      <Panel title="My tokens"><Show when={(tokens()?.length ?? 0) > 0} fallback={<p>No personal tokens yet.</p>}><Table><thead><tr><th>Label</th><th>Scopes</th><th>Preview</th><th>Created</th><th></th></tr></thead><tbody><For each={tokens()}>{(token) => <tr><td>{token.label}</td><td>{token.scopes.join(", ")}</td><td><code>{token.preview}</code></td><td>{new Date(token.created_at).toLocaleDateString()}</td><td><Button size="sm" onClick={() => void revoke(token)}>Revoke</Button></td></tr>}</For></tbody></Table></Show>
        <form class="inline-form" onSubmit={(event) => void createToken(event)}><label class="field">Token label<input aria-label="Token label" value={label()} onInput={(event) => setLabel(event.currentTarget.value)} required maxLength={64} /></label><fieldset><legend>Scopes</legend><For each={offeredScopes()}>{(scope) => <label><input type="checkbox" checked={scopes().includes(scope)} onChange={(event) => setScopes(event.currentTarget.checked ? [...new Set([...scopes(), scope])] : scopes().filter((item) => item !== scope))} />{scope}</label>}</For></fieldset><Button type="submit" variant="primary" busy={busy()}>Create token</Button></form>
      </Panel>
      <Panel title="Default issue view"><label class="field">Default view<Select aria-label="Default view" value={selectedView() || defaultId()} onChange={(event) => void setDefault(event.currentTarget.value)}><option value="">No default view</option><For each={views()}>{(view: SavedView) => <option value={view.id} selected={view.id === (selectedView() || defaultId())}>{view.name} ({view.visibility})</option>}</For></Select></label></Panel>
      <Panel title="Theme"><label class="field">Theme<Select aria-label="Theme" value={theme()} onChange={(event) => { const choice = event.currentTarget.value as ThemeChoice; setThemeChoice(choice); setTheme(choice); }}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></Select></label></Panel>
    </Show>
    <Show when={secret()} keyed>{(created) => <div class="drawer-backdrop"><section class="drawer" role="dialog" aria-modal="true" aria-labelledby="token-secret-title"><h2 id="token-secret-title">Copy your token now</h2><p>This secret is shown once. Close this dialog to remove it from the page.</p><Tag variant="warning">Keep this token private</Tag><p><code aria-label="Token secret">{created.token}</code></p><div class="form-actions"><Button onClick={() => { void navigator.clipboard?.writeText(created.token); setNotice("Token copied."); }}>Copy</Button><Button variant="primary" onClick={() => setSecret(null)}>Close</Button></div></section></div>}</Show>
  </section>;
}
