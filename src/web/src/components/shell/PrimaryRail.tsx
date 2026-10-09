import { useLocation, useNavigate } from "@solidjs/router";
import { Activity, Bot, ClipboardList, FileSearch, GitBranch, Settings, Shield, Users, Workflow } from "lucide-solid";
import { For, type JSX } from "solid-js";
import { AREAS } from "../../routes";
const icons = [ClipboardList, FileSearch, Workflow, Shield, Users, Activity, GitBranch, Bot, Settings];
export function PrimaryRail(): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const items = AREAS.filter((area) => area.key !== "runs");
  const go = (event: MouseEvent, path: string): void => {
    if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    navigate(path);
  };
  return <nav class="primary-rail" aria-label="Primary navigation"><a class="rail-brand" href="/issues" aria-label="Telescreen" onClick={(event) => go(event, "/issues")}>T</a><For each={items}>{(area, index) => { const Icon = icons[index()]!; const path = area.key === "chat" ? "/assistant" : area.path; const active = (): boolean => (area.key === "workflows" && location.pathname.startsWith("/runs")) || location.pathname === area.path || location.pathname.startsWith(`${area.path}/`); return <a class="rail-item" classList={{ active: active(), inactive: !active() }} href={path} aria-current={active() ? "location" : undefined} onClick={(event) => go(event, path)}><Icon size={20}/><span>{area.label === "Escalation Policies" ? "Escalation" : area.label}</span></a>; }}</For></nav>;
}
