import { Portal } from "solid-js/web";
import type { JSX } from "solid-js";
import { A, useLocation } from "@solidjs/router";
export function TopBar(props: Readonly<{ children?: JSX.Element }>): JSX.Element {
  return <header class="top-bar"><div id="topbar-title" class="top-title"/><div id="topbar-actions" class="top-actions">{props.children}<A href={`/assistant?context=${encodeURIComponent(useLocation().pathname)}`}>Ask Assistant</A><A href="/search" aria-label="Search">Search</A></div></header>;
}
TopBar.Slot = function Slot(props: Readonly<{ name: "title" | "actions"; children: JSX.Element }>): JSX.Element {
  const target = document.getElementById(props.name === "title" ? "topbar-title" : "topbar-actions");
  return target === null ? <>{props.children}</> : <Portal mount={target}>{props.name === "title" ? <h1>{props.children}</h1> : props.children}</Portal>;
};
