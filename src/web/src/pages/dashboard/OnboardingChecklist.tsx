import { For, Show, createResource, type JSX } from "solid-js";
import { alertsApi, collectorsApi, directoryApi, integrationsApi } from "../../api/client";
import { useAuth } from "../../auth/useAuth";
import { Panel } from "../../components/ui";

type ChecklistItem = Readonly<{ title: string; href: string; done: boolean; docs: string }>;

/** A permission-aware first-run checklist derived from existing endpoints. */
export function OnboardingChecklist(props: Readonly<{ onDismiss: () => void }>): JSX.Element {
  const auth = useAuth();
  const [items] = createResource(auth.user, async (user): Promise<ChecklistItem[]> => {
    if (user === undefined) return [];
    const can = (permission: string): boolean => user.permissions.includes(permission);
    const checks: Promise<ChecklistItem | null>[] = [];
    if (can("collectors.read")) checks.push(collectorsApi.list().then((value) => ({ title: "Connect a collector", href: "/collectors", done: value.items.length > 0, docs: "#collectors" })).catch(() => null));
    if (can("integrations.read")) checks.push(integrationsApi.list().then((value) => ({ title: "Configure notifications", href: "/settings/integrations", done: value.items.some((item) => item.configured && (item.kind === "slack" || item.kind === "email")), docs: "#notifications" })).catch(() => null));
    if (can("users.read")) checks.push(directoryApi.members().then((value) => ({ title: "Invite users", href: "/settings/users", done: value.items.filter((item) => item.active).length > 1, docs: "#members" })).catch(() => null));
    if (can("alerts.read")) checks.push(alertsApi.list().then((value) => ({ title: "Create an alert rule", href: "/alerts/new", done: value.items.length > 0, docs: "#alert-rules" })).catch(() => null));
    return (await Promise.all(checks)).filter((item): item is ChecklistItem => item !== null);
  });
  return <Panel title="Getting started"><div class="onboarding-list"><Show when={!items.loading} fallback={<p>Loading checklist…</p>}><For each={items()}>{(item) => <div class="onboarding-item"><span aria-label={item.done ? "Complete" : "Not complete"}>{item.done ? "✓" : "○"}</span><a href={item.href}>{item.title}</a><a href={`https://github.com/tacogips/telescreen#${item.docs.slice(1)}`}>Docs</a></div>}</For></Show></div><button class="button-secondary" onClick={props.onDismiss}>Dismiss checklist</button></Panel>;
}
