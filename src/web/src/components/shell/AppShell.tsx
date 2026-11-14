import { A, useLocation, useNavigate } from "@solidjs/router";
import { For, Show, createEffect, createSignal, onCleanup, onMount, type JSX } from "solid-js";
import { AREAS, type AreaKey } from "../../routes";
import { issuesApi } from "../../api/client";
import type { Issue } from "../../api/types";
import { Tag } from "../ui";
import { ShortcutHelp } from "../ShortcutHelp";
import { PrimaryRail } from "./PrimaryRail";
import { SecondaryPanel } from "./SecondaryPanel";
import { TopBar } from "./TopBar";
import { registerShortcuts } from "./shortcuts";
import { readStorage, writeStorage } from "./storage";
function keyFor(path: string): AreaKey { if (path.startsWith("/runs")) return "workflows"; return AREAS.find((area) => area.path !== "/chat" && (path === area.path || path.startsWith(`${area.path}/`)))?.key ?? (path.startsWith("/assistant") ? "chat" : "issues"); }
export function AppShell(props: Readonly<{ children?: JSX.Element }>): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = createSignal(readStorage("telescreen.panelCollapsed") === "true");
  const [mobile, setMobile] = createSignal(false);
  const [palette, setPalette] = createSignal(false);
  const [help, setHelp] = createSignal(false);
  const [text, setText] = createSignal("");
  const [results, setResults] = createSignal<Issue[]>([]);
  const [selected, setSelected] = createSignal(0);
  const toggle = (): void => { const next = !collapsed(); setCollapsed(next); writeStorage("telescreen.panelCollapsed", String(next)); };
  onMount(() => registerShortcuts("app-shell", [
    { key: "k", modifier: true, handler: () => setPalette((value) => !value) },
    { key: "?", handler: () => setHelp((value) => !value) },
    { key: "b", handler: toggle },
    { key: "/", handler: () => navigate(`/assistant?context=${encodeURIComponent(location.pathname)}`) },
  ]));
  createEffect(() => {
    const query = text().trim();
    if (!palette() || query.length === 0) { setResults([]); return; }
    const timer = setTimeout(() => { void issuesApi.listQuery({ query, limit: 8 }).then((page) => setResults(page.items)).catch(() => setResults([])); }, 200);
    onCleanup(() => clearTimeout(timer));
  });
  const close = (): void => { setPalette(false); setText(""); setSelected(0); };
  const choose = (): void => { const item = results()[selected()]; if (item !== undefined) { navigate(`/issues/${encodeURIComponent(item.id)}`); close(); } };
  const onPaletteKey = (event: KeyboardEvent): void => { if (event.key === "Escape") close(); else if (event.key === "ArrowDown") { event.preventDefault(); setSelected((value) => Math.min(value + 1, results().length - 1)); } else if (event.key === "ArrowUp") { event.preventDefault(); setSelected((value) => Math.max(value - 1, 0)); } else if (event.key === "Enter") { event.preventDefault(); choose(); } };
  return <div class="app-shell"><a class="skip-link" href="#main">Skip to main content</a><PrimaryRail/><SecondaryPanel area={keyFor(location.pathname)} collapsed={collapsed()} onToggle={toggle}/><div class="main-panel"><TopBar><Show when={import.meta.env.VITE_TELESCREEN_MOCK === "1"}><Tag variant="info">Mock data</Tag></Show></TopBar><main id="main">{props.children}</main></div><div id="toast-region" class="toast-region" aria-live="polite"/><Show when={mobile()}><div class="mobile-overlay" onClick={() => setMobile(false)}><nav aria-label="Mobile navigation"><button onClick={() => setMobile(false)}>Close</button><For each={AREAS}>{(area) => <A href={area.key === "chat" ? "/assistant" : area.path}>{area.label}</A>}</For></nav></div></Show><Show when={palette()}><div class="palette-backdrop" role="presentation" onClick={close}><div class="palette" role="dialog" aria-label="Go to" onClick={(event) => event.stopPropagation()}><input aria-label="Go to" placeholder="Search issues and pages" value={text()} onInput={(event) => { setText(event.currentTarget.value); setSelected(0); }} onKeyDown={onPaletteKey}/><For each={results()}>{(issue, index) => <button type="button" role="option" aria-selected={selected() === index()} onMouseEnter={() => setSelected(index())} onClick={choose}>{issue.short_id} {issue.display_title}</button>}</For><For each={AREAS}>{(area) => <A href={area.key === "chat" ? "/assistant" : area.path} onClick={close}>{area.label}</A>}</For></div></div></Show><ShortcutHelp open={help()} onClose={() => setHelp(false)}/><button class="mobile-menu" aria-label="Open navigation" onClick={() => setMobile(true)}>Menu</button></div>;
}
