export type IssueView = Readonly<{
  id: string;
  name: string;
  query: string;
  sort: string;
  starred: boolean;
}>;

const VIEW_KEY = "telescreen.issueViews";
const VIEW_EVENT = "telescreen:issue-views";

function isIssueView(value: unknown): value is IssueView {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === "string" &&
    typeof record.name === "string" &&
    typeof record.query === "string" &&
    typeof record.sort === "string" &&
    typeof record.starred === "boolean"
  );
}

export function readIssueViews(): IssueView[] {
  try {
    const raw = globalThis.localStorage?.getItem(VIEW_KEY);
    const parsed: unknown =
      raw === null || raw === undefined ? [] : JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isIssueView) : [];
  } catch {
    return [];
  }
}

function writeIssueViews(views: readonly IssueView[]): void {
  globalThis.localStorage?.setItem(VIEW_KEY, JSON.stringify(views));
  globalThis.window?.dispatchEvent(new Event(VIEW_EVENT));
}

export function saveIssueView(
  views: readonly IssueView[],
  name: string,
  query: string,
  sort: string,
): IssueView[] {
  const normalized = name.trim();
  if (normalized === "") return [...views];
  const created: IssueView = {
    id: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${views.length}`,
    name: normalized,
    query,
    sort,
    starred: false,
  };
  const next = [...views, created];
  writeIssueViews(next);
  return next;
}

export function removeIssueView(
  views: readonly IssueView[],
  id: string,
): IssueView[] {
  const next = views.filter((view) => view.id !== id);
  writeIssueViews(next);
  return next;
}
