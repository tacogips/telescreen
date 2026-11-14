import { Router } from "@solidjs/router";
import type { JSX } from "solid-js";
import { Layout } from "./components/Layout";
import { AppRoutes } from "./routes";

export default function App(): JSX.Element {
  return (
    <Router root={Layout}>
      <AppRoutes />
    </Router>
  );
}
