import type { SavedView } from "../../api/types";

export type IssueView = SavedView;
export type LegacyIssueView = Readonly<{
  id: string;
  name: string;
  query: string;
  sort: string;
  starred: boolean;
}>;

export const LEGACY_VIEW_KEY = "telescreen.issueViews";

function isLegacyIssueView(value: unknown): value is LegacyIssueView {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return typeof record.id === "string" && typeof record.name === "string" &&
    typeof record.query === "string" && typeof record.sort === "string" &&
    typeof record.starred === "boolean";
}

export function readLegacyIssueViews(): LegacyIssueView[] {
  try {
    const raw = globalThis.localStorage?.getItem(LEGACY_VIEW_KEY);
    if (raw === null || raw === undefined) return [];
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? value.filter(isLegacyIssueView) : [];
  } catch {
    return [];
  }
}

export function clearLegacyIssueViews(): void {
  globalThis.localStorage?.removeItem(LEGACY_VIEW_KEY);
}
