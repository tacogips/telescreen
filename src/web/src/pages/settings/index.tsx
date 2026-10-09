import { For, Show, createResource, createSignal } from "solid-js";
import { ApiError, collectorsApi, directoryApi, getToken, healthApi, setToken } from "../../api/client";
import type { CollectorConfig, Member, Team } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { getTheme, setTheme, type ThemeChoice } from "../../components/shell/theme";
import { EmptyState, Panel, PanelItem, Placeholder, Select, Tag, showToast } from "../../components/ui";
import { TopBar } from "../../components/shell/TopBar";

function resourceError(failure: unknown): ApiError | null {
  if (failure === undefined || failure === null) return null;
  return failure instanceof ApiError ? failure : new ApiError(0, "request_failed", "The request could not be completed.");
}

type DirectoryData = Readonly<{ members: Member[]; teams: Team[]; collectors: CollectorConfig[]; health: string }>;

export default function SettingsPage() {
  const [data, { refetch }] = createResource(async (): Promise<DirectoryData> => {
    const [members, teams, collectors, health] = await Promise.all([directoryApi.members(), directoryApi.teams(), collectorsApi.list(), healthApi.get()]);
    return { members: members.items, teams: teams.items, collectors: collectors.items, health: health.status };
  });
  const [token, setTokenInput] = createSignal("");
  const [hasToken, setHasToken] = createSignal(getToken() !== null);
  const [theme, setThemeChoice] = createSignal<ThemeChoice>(getTheme());
  const [memberHandle, setMemberHandle] = createSignal("");
  const [memberName, setMemberName] = createSignal("");
  const [memberEmail, setMemberEmail] = createSignal("");
  const [teamSlug, setTeamSlug] = createSignal("");
  const [teamName, setTeamName] = createSignal("");
  const [teamEmail, setTeamEmail] = createSignal("");
  const [teamChannel, setTeamChannel] = createSignal("");
  const [teamHandles, setTeamHandles] = createSignal<Record<string, string>>({});
  const [error, setError] = createSignal<ApiError | null>(null);

  function saveToken(event: SubmitEvent): void { event.preventDefault(); setToken(token()); setTokenInput(""); setHasToken(getToken() !== null); showToast("API token saved", "success"); }
  function clearToken(): void { setToken(null); setTokenInput(""); setHasToken(false); showToast("API token cleared"); }

  async function createMember(event: SubmitEvent): Promise<void> {
    event.preventDefault(); setError(null);
    try { await directoryApi.createMember({ handle: memberHandle(), display_name: memberName(), email: memberEmail() || undefined }); setMemberHandle(""); setMemberName(""); setMemberEmail(""); await refetch(); showToast("Member added", "success"); }
    catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
  }

  async function createTeam(event: SubmitEvent): Promise<void> {
    event.preventDefault(); setError(null);
    try { await directoryApi.createTeam({ slug: teamSlug(), name: teamName(), email: teamEmail() || undefined, slack_channel: teamChannel() || undefined }); setTeamSlug(""); setTeamName(""); setTeamEmail(""); setTeamChannel(""); await refetch(); showToast("Team added", "success"); }
    catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
  }

  async function saveMembers(team: Team): Promise<void> {
    setError(null);
    try { await directoryApi.setTeamMembers(team.slug, (teamHandles()[team.slug] ?? "").split(",").map((handle) => handle.trim()).filter((handle) => handle.length > 0)); showToast("Team members updated", "success"); }
    catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
  }

  return <section class="page-content settings-page">
    <TopBar.Slot name="title">Settings</TopBar.Slot>
    <Show when={error() ?? resourceError(data.error)}>{(failure) => <ErrorBanner error={failure()} />}</Show>
    <Show when={!data.loading} fallback={<Placeholder label="Loading settings…" />}>
      <Panel title="Account"><PanelItem><div><strong>API token</strong><p>{hasToken() ? "Token set" : "No token set"}</p></div><form onSubmit={saveToken} class="inline-form"><label class="field">Bearer token<input type="password" autocomplete="off" value={token()} onInput={(event) => setTokenInput(event.currentTarget.value)} /></label><button class="button-primary" type="submit">Save token</button></form><button class="button-secondary" type="button" onClick={clearToken} disabled={!hasToken()}>Clear token</button></PanelItem>
        <PanelItem><label class="field">Theme<Select aria-label="Theme" value={theme()} onChange={(event) => { const choice = event.currentTarget.value as ThemeChoice; setThemeChoice(choice); setTheme(choice); }}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></Select></label></PanelItem>
      </Panel>
      <Panel title="Members and teams"><div class="settings-columns">
        <div><h3>Members</h3><Show when={(data()?.members.length ?? 0) > 0} fallback={<EmptyState title="No members"><p>Add a member to assign work.</p></EmptyState>}><For each={data()?.members}>{(member) => <PanelItem><strong>{member.display_name}</strong><span>@{member.handle}</span><span>{member.email ?? "—"}</span></PanelItem>}</For></Show>
          <form class="settings-form" onSubmit={(event) => void createMember(event)}><h4>Add member</h4><label class="field">Handle<input value={memberHandle()} onInput={(event) => setMemberHandle(event.currentTarget.value)} required /></label><label class="field">Display name<input value={memberName()} onInput={(event) => setMemberName(event.currentTarget.value)} required /></label><label class="field">Email<input type="email" value={memberEmail()} onInput={(event) => setMemberEmail(event.currentTarget.value)} /></label><button class="button-secondary" type="submit">Add member</button></form>
        </div>
        <div><h3>Teams</h3><Show when={(data()?.teams.length ?? 0) > 0} fallback={<EmptyState title="No teams"><p>Create a team for assignment and escalation.</p></EmptyState>}><For each={data()?.teams}>{(team) => <PanelItem><div><strong>{team.name}</strong><small>{team.slug} · {team.email ?? "—"} · {team.slack_channel ?? "—"}</small></div><form class="inline-form" onSubmit={(event) => { event.preventDefault(); void saveMembers(team); }}><label class="field">Member handles<input value={teamHandles()[team.slug] ?? ""} placeholder="alice, bob" onInput={(event) => setTeamHandles({ ...teamHandles(), [team.slug]: event.currentTarget.value })} /></label><button class="button-secondary" type="submit">Set members</button></form></PanelItem>}</For></Show>
          <form class="settings-form" onSubmit={(event) => void createTeam(event)}><h4>Add team</h4><label class="field">Slug<input value={teamSlug()} onInput={(event) => setTeamSlug(event.currentTarget.value)} required /></label><label class="field">Name<input value={teamName()} onInput={(event) => setTeamName(event.currentTarget.value)} required /></label><label class="field">Email<input type="email" value={teamEmail()} onInput={(event) => setTeamEmail(event.currentTarget.value)} /></label><label class="field">Slack channel<input value={teamChannel()} onInput={(event) => setTeamChannel(event.currentTarget.value)} /></label><button class="button-secondary" type="submit">Add team</button></form>
        </div>
      </div></Panel>
      <Panel title="API tokens"><p>Create tokens with <code>telescreen token create --label &lt;label&gt; --scopes read,write</code>.</p></Panel>
      <Panel title="Collector credentials"><Show when={(data()?.collectors.length ?? 0) > 0} fallback={<p>No collector credential references configured.</p>}><For each={data()?.collectors.filter((config) => config.credential_ref !== null)}>{(config) => <PanelItem><strong>{config.name}</strong><code>{config.credential_ref}</code></PanelItem>}</For></Show><p>Only secret references are shown. Secret values are never stored here.</p></Panel>
      <Panel title="About"><PanelItem><span>Reporting Bureau status</span><Tag variant={data()?.health === "ok" ? "success" : "warning"}>{data()?.health ?? "unknown"}</Tag></PanelItem></Panel>
    </Show>
  </section>;
}
