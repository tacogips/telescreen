import { createSignal } from "solid-js";
import { getToken, setToken } from "../../api/client";

export default function SettingsPage() {
  const [token, setTokenInput] = createSignal("");
  const [hasToken, setHasToken] = createSignal(getToken() !== null);
  function save(event: SubmitEvent): void { event.preventDefault(); setToken(token()); setTokenInput(""); setHasToken(getToken() !== null); }
  function clear(): void { setToken(null); setTokenInput(""); setHasToken(false); }
  return <section>
    <h1>Settings</h1>
    <p>Environment: default</p>
    <form onSubmit={save}>
      <label class="field">Bearer API token<input type="password" autocomplete="off" value={token()} onInput={(event) => setTokenInput(event.currentTarget.value)} /></label>
      <button class="button-secondary" type="submit">Save token</button>
    </form>
    <p role="status">{hasToken() ? "Token set" : "No token set"}</p>
    <button class="button-secondary" type="button" onClick={clear} disabled={!hasToken()}>Clear token</button>
  </section>;
}
