import { A, useLocation, useParams } from "@solidjs/router";
import { For, lazy, Match, Show, Suspense, Switch, type Component, type JSX } from "solid-js";
import { useAuth } from "../../auth/useAuth";
import { Unavailable } from "../../components/Unavailable";
import { GeneralSettings } from "./GeneralSettings";
import { IntegrationsSettings } from "./IntegrationsSettings";
import { OwnershipRules } from "./OwnershipRules";
import { TemplateDetail } from "./TemplateDetail";
import { TemplatesList } from "./TemplatesList";
import { TokensSettings } from "./TokensSettings";
import { UsersRoles } from "./UsersRoles";
import "./settings.css";

const profileModules = import.meta.glob<{ default: Component }>("../profile/ProfilePage.tsx");
const profileModule = profileModules["../profile/ProfilePage.tsx"];
const ProfilePage: Component = profileModule === undefined ? () => <Unavailable label="Profile" /> : lazy(profileModule);

type SettingsLink = Readonly<{ label: string; href: string }>;

function SettingsNavigation(): JSX.Element {
  const auth = useAuth();
  const location = useLocation();
  const links = (): SettingsLink[] => [
    { label: "General", href: "/settings" },
    ...(auth.can("templates.read") ? [{ label: "Notification templates", href: "/settings/templates" }] : []),
    ...(auth.can("users.read") ? [{ label: "Users and roles", href: "/settings/users" }] : []),
    ...(auth.can("profile.self") ? [{ label: "Profile", href: "/settings/profile" }] : []),
    ...(auth.can("integrations.read") ? [{ label: "Integrations", href: "/settings/integrations" }] : []),
    ...(auth.can("tokens.read") ? [{ label: "Tokens", href: "/settings/tokens" }] : []),
    ...(auth.can("ownership.read") ? [{ label: "Ownership rules", href: "/settings/ownership" }] : []),
  ];
  return <nav class="settings-page-nav" aria-label="Settings sections"><For each={links()}>{(item) => <A href={item.href} classList={{ active: location.pathname === item.href || (item.href === "/settings/templates" && location.pathname.startsWith("/settings/templates/")) }}>{item.label}</A>}</For></nav>;
}

function decodeName(value: string): string {
  try { return decodeURIComponent(value); } catch { return value; }
}

/** Settings section switch: account, organization and notification settings. */
export default function SettingsPage(): JSX.Element {
  // The template name is the whole `:sub` segment; names such as `system.workflow_notify` keep their dots.
  const params = useParams<{ id?: string; sub?: string }>();
  return <><SettingsNavigation /><Switch fallback={<GeneralSettings />}>
    <Match when={params.id === "profile"}><Suspense fallback={<p>Loading profile…</p>}><ProfilePage /></Suspense></Match>
    <Match when={params.id === "integrations"}><IntegrationsSettings /></Match>
    <Match when={params.id === "tokens"}><TokensSettings /></Match>
    <Match when={params.id === "ownership"}><OwnershipRules /></Match>
    <Match when={params.id === "templates" && params.sub !== undefined}><TemplateDetail name={decodeName(params.sub ?? "")} /></Match>
    <Match when={params.id === "templates"}><TemplatesList /></Match>
    <Match when={params.id === "users"}><UsersRoles /></Match>
  </Switch></>;
}
