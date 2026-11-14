import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { directoryApi, tokensApi } from "../../api/client";
import { useAuth } from "../../auth/useAuth";
import { TopBar } from "../../components/shell/TopBar";
import { Panel, PanelItem, Tag } from "../../components/ui";

const SCOPES = ["read", "write", "admin", "ingest"] as const;

/** Administrative API token management; a newly created secret is rendered once. */
export function TokensSettings(): JSX.Element {
  const auth = useAuth();
  const [tokens, { refetch }] = createResource(() => auth.can("tokens.read"), async (enabled) => enabled ? (await tokensApi.list()).items : []);
  const [members] = createResource(() => auth.can("tokens.write") && auth.can("users.read"), async (enabled) => enabled ? (await directoryApi.members()).items : []);
  const [label, setLabel] = createSignal("");
  const [member, setMember] = createSignal("");
  const [scopes, setScopes] = createSignal<string[]>(["read"]);
  const [secret, setSecret] = createSignal<string | null>(null);
  const [error, setError] = createSignal<string | null>(null);
  const save = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault(); setSecret(null); setError(null);
    try {
      const created = await tokensApi.create({ label: label().trim(), scopes: scopes(), ...(member() === "" ? {} : { member_handle: member() }) });
      setSecret(created.token); setLabel(""); await refetch();
    } catch { setError("The token could not be created."); }
  };
  const revoke = async (id: string): Promise<void> => { try { await tokensApi.revoke(id); await refetch(); } catch { setError("The token could not be revoked."); } };
  const toggleScope = (scope: string): void => { setScopes((current) => current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope]); };
  return <section class="page-content settings-page"><TopBar.Slot name="title">API tokens</TopBar.Slot>
    <Show when={auth.can("tokens.read")} fallback={<p role="alert">You do not have permission to view API tokens.</p>}>
      <Panel title="Tokens"><Show when={!tokens.loading} fallback={<p>Loading tokens…</p>}>
        <For each={tokens()}>{(token) => <PanelItem><div><strong>{token.label}</strong><span>{token.member_handle ?? "Organization token"} · {token.scopes.join(", ")}</span><Show when={token.revoked_at}><Tag>Revoked</Tag></Show></div><Show when={auth.can("tokens.write") && token.revoked_at === null}><button class="button-secondary" onClick={() => void revoke(token.id)}>Revoke</button></Show></PanelItem>}</For>
      </Show></Panel>
      <Show when={auth.can("tokens.write")}><Panel title="Create token"><form class="settings-form" onSubmit={(event) => void save(event)}>
        <label class="field">Label<input aria-label="Token label" value={label()} onInput={(event) => setLabel(event.currentTarget.value)} required /></label>
        <label class="field">Member<SelectMember value={member()} onChange={setMember} members={members() ?? []} /></label>
        <fieldset><legend>Scopes</legend><For each={SCOPES}>{(scope) => <label><input type="checkbox" checked={scopes().includes(scope)} onChange={() => toggleScope(scope)} />{scope}</label>}</For></fieldset>
        <button class="button-primary" type="submit">Create token</button>
      </form></Panel></Show>
      <Show when={secret()}>{(value) => <Panel title="Copy your token now"><p>This secret is shown once.</p><code>{value()}</code><button class="button-secondary" onClick={() => setSecret(null)}>Hide token</button></Panel>}</Show>
      <Show when={error()}>{(message) => <p role="alert">{message()}</p>}</Show>
    </Show>
  </section>;
}

function SelectMember(props: Readonly<{ value: string; onChange: (value: string) => void; members: readonly Readonly<{ handle: string; display_name: string }>[] }>): JSX.Element {
  return <select aria-label="Token member" value={props.value} onChange={(event) => props.onChange(event.currentTarget.value)}><option value="">Organization token</option><For each={props.members}>{(item) => <option value={item.handle}>{item.display_name}</option>}</For></select>;
}
