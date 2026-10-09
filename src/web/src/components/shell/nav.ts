import { readStorage } from "./storage";
import type { AreaKey } from "../../routes";
export type NavEntry = Readonly<{ label: string; href: string }>;
export const entriesFor = (area: AreaKey): readonly NavEntry[] => {
  if (area === "issues") return [
    { label: "Feed", href: "/issues" },
    ...["slack", "gdrive", "gdocs", "gsheets", "github", "custom", "police"].map((source) => ({ label: source, href: `/issues?query=source:${source}` })),
    ...["credential", "token", "private_key", "password", "payment_card", "national_id", "email", "phone", "address"].map((rule) => ({ label: rule.replaceAll("_", " "), href: `/issues?query=rule:${rule}` })),
    ...savedViews(),
  ];
  if (area === "workflows") return [{ label: "Workflows", href: "/workflows" }, { label: "Runs", href: "/runs" }];
  if (area === "runs") return [{ label: "Runs", href: "/runs" }, { label: "Workflows", href: "/workflows" }];
  return [{ label: area === "escalation" ? "Escalation Policies" : area[0]!.toUpperCase() + area.slice(1), href: area === "chat" ? "/assistant" : `/${area}` }];
};
function savedViews(): readonly NavEntry[] {
  try {
    const parsed: unknown = JSON.parse(readStorage("telescreen.issueViews") ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item: unknown) => {
      if (typeof item !== "object" || item === null || !("id" in item) || !("name" in item) || !("query" in item) || !("sort" in item)) return [];
      const view = item as { id: string; name: string; query: string; sort: string };
      if ([view.id, view.name, view.query, view.sort].some((value) => typeof value !== "string")) return [];
      return [{ label: view.name, href: `/issues?query=${encodeURIComponent(view.query)}&sort=${encodeURIComponent(view.sort)}` }];
    });
  } catch { return []; }
}
