import { A, useLocation } from "@solidjs/router";
import { For, type JSX } from "solid-js";
import { AREAS } from "../routes";

export function Sidebar(): JSX.Element {
  const location = useLocation();
  return (
    <aside class="sidebar" aria-label="Main navigation">
      <A class="brand" href="/issues">Telescreen</A>
      <nav>
        <For each={AREAS}>{(area) => {
          const active = () => location.pathname === area.path || location.pathname.startsWith(`${area.path}/`);
          return <A href={area.path} classList={{ "nav-link": true, active: active() }} aria-current={active() ? "page" : undefined}>{area.label}</A>;
        }}</For>
      </nav>
    </aside>
  );
}
