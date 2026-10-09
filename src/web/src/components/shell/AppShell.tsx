import { A, useLocation, useNavigate } from "@solidjs/router";
import { For, Show, createSignal, onCleanup, onMount, type JSX } from "solid-js";
import { Portal } from "solid-js/web";
import { AREAS, type AreaKey } from "../../routes";
import { Tag } from "../ui";
import { PrimaryRail } from "./PrimaryRail";
import { SecondaryPanel } from "./SecondaryPanel";
import { TopBar } from "./TopBar";
import { isTextTarget, shortcutKey } from "./shortcuts";
import { readStorage, writeStorage } from "./storage";
function keyFor(path: string): AreaKey { if (path.startsWith("/runs")) return "workflows"; return AREAS.find((area) => area.path !== "/chat" && (path === area.path || path.startsWith(`${area.path}/`)))?.key ?? (path.startsWith("/assistant") ? "chat" : "issues"); }
export function AppShell(props: Readonly<{ children?: JSX.Element }>): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = createSignal(readStorage("telescreen.panelCollapsed") === "true");
  const [mobile, setMobile] = createSignal(false);
  const [palette, setPalette] = createSignal(false);
  const toggle = (): void => { const next = !collapsed(); setCollapsed(next); writeStorage("telescreen.panelCollapsed", String(next)); };
  const onKey = (event: KeyboardEvent): void => { if (event.key === "Escape") { setPalette(false); return; } const key = shortcutKey(event); if (key === null) return; if (key === "k" && (!isTextTarget(event.target) || key === "k")) { event.preventDefault(); setPalette((value) => !value); } else if (!isTextTarget(event.target) && key === "b") { event.preventDefault(); toggle(); } else if (!isTextTarget(event.target) && key === "/") { event.preventDefault(); navigate(`/assistant?context=${encodeURIComponent(location.pathname)}`); } else if (key === "escape") setPalette(false); };
  onMount(() => addEventListener("keydown", onKey)); onCleanup(() => removeEventListener("keydown", onKey));
  return <div class="app-shell"><a class="skip-link" href="#main">Skip to main content</a><PrimaryRail/><SecondaryPanel area={keyFor(location.pathname)} collapsed={collapsed()} onToggle={toggle}/><div class="main-panel"><TopBar><Show when={import.meta.env.VITE_TELESCREEN_MOCK === "1"}><Tag variant="info">Mock data</Tag></Show></TopBar><main id="main">{props.children}</main></div><div id="toast-region" class="toast-region" aria-live="polite"/><Show when={mobile()}><div class="mobile-overlay" onClick={() => setMobile(false)}><nav aria-label="Mobile navigation"><button onClick={() => setMobile(false)}>Close</button><For each={AREAS}>{(area) => <A href={area.key === "chat" ? "/assistant" : area.path}>{area.label}</A>}</For></nav></div></Show><Show when={palette()}><div class="palette-backdrop" role="presentation" onClick={() => setPalette(false)}><div class="palette" role="dialog" aria-label="Go to" onClick={(event) => event.stopPropagation()}><input aria-label="Go to" placeholder="Go to…"/><For each={AREAS}>{(area) => <A href={area.key === "chat" ? "/assistant" : area.path} onClick={() => setPalette(false)}>{area.label}</A>}</For></div></div></Show><button class="mobile-menu" aria-label="Open navigation" onClick={() => setMobile(true)}>Menu</button></div>;
}
