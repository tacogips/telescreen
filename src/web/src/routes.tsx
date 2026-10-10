import { lazy, type Component, type JSX } from "solid-js";
import { Navigate, Route, useLocation } from "@solidjs/router";
import { Unavailable } from "./components/Unavailable";
export type AreaKey = "issues" | "tasks" | "search" | "workflows" | "runs" | "police" | "collectors" | "policies" | "escalation" | "audit" | "chat" | "settings";
export type Area = Readonly<{ key: AreaKey; label: string; path: string; group: AreaKey }>;
export type PageModule = Readonly<{ default: () => JSX.Element }>;
export const AREAS: readonly Area[] = [
  { key: "issues", label: "Issues", path: "/issues", group: "issues" },
  { key: "tasks", label: "My tasks", path: "/my-tasks", group: "tasks" },
  { key: "search", label: "Search", path: "/search", group: "search" },
  { key: "workflows", label: "Workflows", path: "/workflows", group: "workflows" },
  { key: "runs", label: "Runs", path: "/runs", group: "workflows" },
  { key: "police", label: "Police", path: "/police", group: "police" },
  { key: "collectors", label: "Collectors", path: "/collectors", group: "collectors" },
  { key: "policies", label: "Policies", path: "/policies", group: "policies" },
  { key: "escalation", label: "Escalation Policies", path: "/escalation", group: "escalation" },
  { key: "audit", label: "Audit", path: "/audit", group: "audit" },
  { key: "chat", label: "Assistant", path: "/assistant", group: "chat" },
  { key: "settings", label: "Settings", path: "/settings", group: "settings" },
];
const pageModules = import.meta.glob<PageModule>("./pages/*/index.tsx");
const areaKeys = new Map<string, AreaKey>(AREAS.map((area) => [area.key, area.key]));
const pages = new Map<AreaKey, Component>();
for (const [path, loader] of Object.entries(pageModules)) { const match = /^\.\/pages\/([^/]+)\/index\.tsx$/.exec(path); const key = match?.[1]; if (key !== undefined && areaKeys.has(key)) pages.set(areaKeys.get(key)!, lazy(loader)); }
export function AreaPage(props: Readonly<{ area: Area; pages?: ReadonlyMap<AreaKey, Component> }>): JSX.Element { const Page = (props.pages ?? pages).get(props.area.key); return Page === undefined ? <Unavailable label={props.area.label}/> : <Page/>; }
function ChatRedirect(): JSX.Element { const location = useLocation(); return <Navigate href={`/assistant${location.search}`}/>; }
export function AppRoutes(): JSX.Element { return <><Route path="/" component={() => <Navigate href="/issues"/>}/><Route path="/chat" component={ChatRedirect}/><Route path="/chat/:id" component={ChatRedirect}/>{AREAS.map((area) => <><Route path={area.path} component={() => <AreaPage area={area}/>}/><Route path={`${area.path}/:id`} component={() => <AreaPage area={area}/>}/></>)}</>; }
