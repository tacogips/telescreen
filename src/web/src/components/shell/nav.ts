import type { AreaKey } from "../../routes";
import { RULE_KEYS, SOURCE_KEYS, ruleLabel, sourceLabel } from "../labels";
export type NavEntry = Readonly<{ label: string; href: string }>;
export const entriesFor = (area: AreaKey): readonly NavEntry[] => {
  if (area === "issues") return [
    { label: "Feed", href: "/issues" },
    { label: "Assigned to me", href: "/issues?query=assigned%3Ame" },
    { label: "Bookmarks", href: "/issues?query=bookmarks%3Ame" },
    { label: "Allowed", href: "/issues?query=is%3Aallowed" },
    { label: "Verifying", href: "/issues?query=is%3Apending_verification" },
    ...SOURCE_KEYS.map((source) => ({ label: sourceLabel(source), href: `/issues?query=source:${source}` })),
    ...RULE_KEYS.map((rule) => ({ label: ruleLabel(rule), href: `/issues?query=rule:${rule}` })),
  ];
  if (area === "workflows") return [{ label: "Workflows", href: "/workflows" }, { label: "Runs", href: "/runs" }];
  if (area === "runs") return [{ label: "Runs", href: "/runs" }, { label: "Workflows", href: "/workflows" }];
  return [{ label: area === "escalation" ? "Escalation Policies" : area[0]!.toUpperCase() + area.slice(1), href: area === "chat" ? "/assistant" : area === "tasks" ? "/my-tasks" : `/${area}` }];
};
