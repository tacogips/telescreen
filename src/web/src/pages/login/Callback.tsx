import { useNavigate } from "@solidjs/router";
import { createSignal, onMount, type JSX } from "solid-js";
import { ApiError } from "../../api/client";
import { completeLogin } from "../../auth/oidc";
import "./login.css";
export default function CallbackPage(): JSX.Element {
  const navigate = useNavigate();
  onMount(() => { void completeLogin(new URL(location.href)).then((path) => navigate(path, { replace: true })).catch((reason: unknown) => { const code = reason instanceof ApiError ? reason.code : "invalid_token"; navigate(`/login?error=${encodeURIComponent(code)}`, { replace: true }); }); });
  return <main class="login-page"><section class="login-card" aria-live="polite"><h1>Signing in</h1><span class="login-spinner" role="status" aria-label="Signing in"/><p>Completing secure sign-in…</p></section></main>;
}
