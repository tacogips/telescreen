import { Portal } from "solid-js/web";
import type { JSX } from "solid-js";
import { A, useLocation } from "@solidjs/router";
import { Show } from "solid-js";
import { authApi } from "../../api/client";
import { logout } from "../../auth/oidc";
import { useAuth } from "../../auth/useAuth";
export function TopBar(props: Readonly<{ children?: JSX.Element }>): JSX.Element {
  const auth = useAuth();
  const signOut = async (): Promise<void> => { const config = await authApi.config().catch(() => null); await logout(config?.oidc); };
  return <header class="top-bar"><div id="topbar-title" class="top-title"/><div id="topbar-actions" class="top-actions">{props.children}<A href={`/assistant?context=${encodeURIComponent(useLocation().pathname)}`}>Ask Assistant</A><A href="/search" aria-label="Search">Search</A><Show when={auth.user()?.member}><span>{auth.user()?.member?.handle} · {auth.user()?.role ?? "No role"}</span><button type="button" onClick={() => void signOut()}>Sign out</button></Show></div></header>;
}
TopBar.Slot = function Slot(props: Readonly<{ name: "title" | "actions"; children: JSX.Element }>): JSX.Element {
  const target = document.getElementById(props.name === "title" ? "topbar-title" : "topbar-actions");
  return target === null ? <>{props.children}</> : <Portal mount={target}>{props.name === "title" ? <h1>{props.children}</h1> : props.children}</Portal>;
};
