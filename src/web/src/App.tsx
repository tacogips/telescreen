import { Router } from "@solidjs/router";
import type { JSX } from "solid-js";
import { AppShell } from "./components/shell/AppShell";
import { AppRoutes } from "./routes";
export default function App(): JSX.Element { return <Router root={AppShell}><AppRoutes/></Router>; }
