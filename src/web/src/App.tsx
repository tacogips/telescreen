import { Router, useLocation } from "@solidjs/router";
import { Show, type JSX } from "solid-js";
import { AppShell } from "./components/shell/AppShell";
import { AppRoutes } from "./routes";
function ShellRoot(props: Readonly<{ children?: JSX.Element }>): JSX.Element { const location = useLocation(); return <Show when={location.pathname !== "/login" && location.pathname !== "/auth/callback"} fallback={props.children}><AppShell>{props.children}</AppShell></Show>; }
export default function App(): JSX.Element { return <Router root={ShellRoot}><AppRoutes/></Router>; }
