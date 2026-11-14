import { A, useLocation } from "@solidjs/router";
import { For, createSignal, onCleanup, onMount, type JSX } from "solid-js";
import { AREAS, type AreaKey } from "../../routes";
import { entriesFor } from "./nav";
import { readStorage, writeStorage } from "./storage";
import { useAuth } from "../../auth/useAuth";
export function SecondaryPanel(props: Readonly<{ area: AreaKey; collapsed: boolean; onToggle(): void }>): JSX.Element {
  const [width, setWidth] = createSignal(Number(readStorage("telescreen.panelWidth")) || 190);
  const [revision, refresh] = createSignal(0);
  const update = (): void => { refresh((value) => value + 1); };
  onMount(() => { addEventListener("storage", update); addEventListener("telescreen:issue-views", update); });
  onCleanup(() => { removeEventListener("storage", update); removeEventListener("telescreen:issue-views", update); });
  const location = useLocation();
  const auth = useAuth();
  const panelEntries = () => props.area === "settings"
    ? [{ label: "General", href: "/settings" }, { label: "Notification templates", href: "/settings/templates" }, ...(auth.can("users.read") ? [{ label: "Users and roles", href: "/settings/users" }] : []), ...(auth.can("profile.self") ? [{ label: "Profile", href: "/settings/profile" }] : []), ...(auth.can("integrations.read") ? [{ label: "Integrations", href: "/settings/integrations" }] : []), ...(auth.can("tokens.read") ? [{ label: "Tokens", href: "/settings/tokens" }] : []), ...(auth.can("ownership.read") ? [{ label: "Ownership rules", href: "/settings/ownership" }] : []), ...(auth.can("data_fields.admin") ? [{ label: "Field schema", href: "/settings/data-fields" }] : [])]
    : entriesFor(props.area, auth.can);
  const title = (): string => AREAS.find((area) => area.key === props.area)?.label ?? "Issues";
  const resize = (event: PointerEvent): void => { const next = Math.min(450, Math.max(150, event.clientX - 74)); setWidth(next); writeStorage("telescreen.panelWidth", String(next)); };
  return <aside classList={{ "secondary-panel": true, collapsed: props.collapsed }} style={{ width: `${width()}px` }} aria-label={`${title()} navigation`}><div class="panel-head"><strong>{title()}</strong><button type="button" aria-label="Toggle navigation panel" onClick={props.onToggle}>Collapse</button></div><nav><For each={(revision(), panelEntries())}>{(entry) => <A href={entry.href} class="panel-link" aria-current={location.pathname + location.search === entry.href ? "location" : undefined}>{entry.label}</A>}</For></nav><div class="panel-resizer" role="separator" aria-orientation="vertical" onPointerDown={(event) => { const move = (pointer: PointerEvent): void => resize(pointer); addEventListener("pointermove", move); addEventListener("pointerup", () => removeEventListener("pointermove", move), { once: true }); event.currentTarget.setPointerCapture(event.pointerId); }} /></aside>;
}
