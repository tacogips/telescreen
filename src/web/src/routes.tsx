import { lazy, type Component, type JSX } from "solid-js";
import { Navigate, Route } from "@solidjs/router";
import { Unavailable } from "./components/Unavailable";

export type AreaKey =
  | "issues"
  | "search"
  | "workflows"
  | "runs"
  | "police"
  | "collectors"
  | "escalation"
  | "audit"
  | "chat"
  | "settings";

export type Area = Readonly<{ key: AreaKey; label: string; path: string }>;
export type PageModule = Readonly<{ default: () => JSX.Element }>;

export const AREAS: readonly Area[] = [
  { key: "issues", label: "Issues", path: "/issues" },
  { key: "search", label: "Search", path: "/search" },
  { key: "workflows", label: "Workflows", path: "/workflows" },
  { key: "runs", label: "Runs", path: "/runs" },
  { key: "police", label: "Police", path: "/police" },
  { key: "collectors", label: "Collectors", path: "/collectors" },
  { key: "escalation", label: "Escalation Policies", path: "/escalation" },
  { key: "audit", label: "Audit", path: "/audit" },
  { key: "chat", label: "Assistant", path: "/chat" },
  { key: "settings", label: "Settings", path: "/settings" },
];

const pageModules = import.meta.glob<PageModule>("./pages/*/index.tsx");
const areaKeys = new Map<string, AreaKey>(AREAS.map((area) => [area.key, area.key]));
const pages = new Map<AreaKey, Component>();

for (const [path, loader] of Object.entries(pageModules)) {
  const match = /^\.\/pages\/([^/]+)\/index\.tsx$/.exec(path);
  const key = match?.[1];
  if (key !== undefined && areaKeys.has(key)) {
    pages.set(areaKeys.get(key)!, lazy(loader));
  }
}

export function AreaPage(
  props: Readonly<{ area: Area; pages?: ReadonlyMap<AreaKey, Component> }>,
): JSX.Element {
  const Page = (props.pages ?? pages).get(props.area.key);
  return Page === undefined ? <Unavailable label={props.area.label} /> : <Page />;
}

export function AppRoutes(): JSX.Element {
  return (
    <>
      <Route path="/" component={() => <Navigate href="/issues" />} />
      {AREAS.map((area) => (
        <>
          <Route path={area.path} component={() => <AreaPage area={area} />} />
          <Route path={`${area.path}/:id`} component={() => <AreaPage area={area} />} />
        </>
      ))}
    </>
  );
}
