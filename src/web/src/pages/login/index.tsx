import { useNavigate, useSearchParams } from "@solidjs/router";
import { Show, createSignal, onMount, type JSX } from "solid-js";
import { ApiError, authApi, setToken } from "../../api/client";
import type { AuthConfig } from "../../api/types";
import { beginLogin } from "../../auth/oidc";
import { safeReturnTo } from "../../auth/session";
const messages: Readonly<Record<string, string>> = { no_role: "Your account has no Telescreen role. Ask an administrator.", not_provisioned: "Your account is not provisioned in Telescreen.", identity_conflict: "Your identity conflicts with an existing account. Ask an administrator.", idp_unavailable: "The identity provider is unavailable. Try again later.", invalid_token: "Sign-in could not be verified. Try again.", idp_error: "The identity provider did not complete the sign-in. Try again." };
export default function LoginPage(): JSX.Element {
  const [config, setConfig] = createSignal<AuthConfig>(); const [token, setValue] = createSignal(""); const [error, setError] = createSignal(""); const navigate = useNavigate(); const [params] = useSearchParams();
  onMount(() => { void authApi.config().then(setConfig).catch(() => setError("Sign-in configuration could not be loaded.")); });
  const signIn = (): void => { const value = config()?.oidc; if (value) void beginLogin(value, typeof params.returnTo === "string" ? params.returnTo : null).catch(() => setError("The identity provider is unavailable. Try again later.")); };
  const submit = (event: SubmitEvent): void => { event.preventDefault(); setToken(token()); navigate(safeReturnTo(typeof params.returnTo === "string" ? params.returnTo : null), { replace: true }); };
  const code = typeof params.error === "string" ? params.error : "";
  return <main class="login-page"><section class="login-card"><h1>Sign in to Telescreen</h1><Show when={messages[code] ?? error()}>{(message) => <div role="alert" class="error-banner">{message()}</div>}</Show><Show when={config()?.oidc}><button type="button" class="button button-primary" onClick={signIn}>Sign in with SSO</button></Show><Show when={config()?.token_login}><form onSubmit={submit}><label for="api-token">API token</label><input id="api-token" type="password" autocomplete="off" value={token()} onInput={(event) => setValue(event.currentTarget.value)}/><button class="button button-primary" type="submit">Use an API token</button></form></Show></section></main>;
}
