import type { JSX } from "solid-js";
import { Sidebar } from "./Sidebar";

export function Layout(props: Readonly<{ children?: JSX.Element }>): JSX.Element {
  return (
    <div class="app-shell">
      <Sidebar />
      <main class="main-panel">
        <header class="topbar"><span>Reporting Bureau</span><span class="environment-label">Environment: default</span></header>
        <div class="page-content">{props.children}</div>
      </main>
    </div>
  );
}
