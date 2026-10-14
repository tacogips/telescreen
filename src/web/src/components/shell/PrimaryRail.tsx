import { useLocation, useNavigate } from "@solidjs/router";
import { Activity, Bell, Bot, ClipboardCheck, ClipboardList, Database, FileSearch, GitBranch, LayoutDashboard, ScrollText, Settings, Shield, Users, Workflow } from "lucide-solid";
import { createResource, For, Show, type JSX } from "solid-js";
import { exceptionsApi, humanTasksApi } from "../../api/client";
import { AREAS } from "../../routes";
const icons = { dashboard: LayoutDashboard, alerts: Bell, data: Database, issues: ClipboardList, tasks: ClipboardCheck, search: FileSearch, workflows: Workflow, police: Shield, collectors: Users, policies: ScrollText, escalation: Activity, audit: GitBranch, chat: Bot, settings: Settings } as const;
export function PrimaryRail(): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const items = AREAS.filter((area) => area.key !== "runs");
  const [taskCount] = createResource(async (): Promise<number> => {
    try {
      const [tasks, approvals] = await Promise.all([
        humanTasksApi.list(new URLSearchParams({ assignee: "me", status: "open" })),
        exceptionsApi.list({ approver: "me", status: "requested" }),
      ]);
      return tasks.items.length + approvals.items.length;
    } catch { return 0; }
  });
  const go = (event: MouseEvent, path: string): void => {
    if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    navigate(path);
  };
  return <nav class="primary-rail" aria-label="Primary navigation"><a class="rail-brand" href="/issues" aria-label="Telescreen" onClick={(event) => go(event, "/issues")}>T</a><For each={items}>{(area) => { const Icon = icons[area.key as keyof typeof icons]; const path = area.key === "chat" ? "/assistant" : area.path; const active = (): boolean => (area.key === "workflows" && location.pathname.startsWith("/runs")) || location.pathname === area.path || location.pathname.startsWith(`${area.path}/`); return <a class="rail-item" classList={{ active: active(), inactive: !active() }} href={path} aria-current={active() ? "location" : undefined} onClick={(event) => go(event, path)}><Icon size={20}/><span>{area.label === "Escalation Policies" ? "Escalation" : area.label}</span><Show when={area.key === "tasks" && (taskCount() ?? 0) > 0}><span class="rail-count" aria-label={`${taskCount()} items need attention`}>{taskCount()}</span></Show></a>; }}</For></nav>;
}
