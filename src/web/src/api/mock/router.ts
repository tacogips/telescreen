import type { Evidence, Issue, IssueStatus, MapJob, WorkflowVersion } from "../types";
import { eventFixtures, historyFixtures } from "./fixtures/issues";
import { mapJobSummaryFixtures, runDetailFixture } from "./fixtures/operations";
import { teamFixtures } from "./fixtures/directory";
import { state, workflowRefs } from "./state";
import type { MockTemplateVersion } from "./fixtures/v12";
import { evaluateIssueQuery, tokenizeIssueQuery } from "../issueQuery";

type Handler = (url: URL, request: Request, match: RegExpExecArray) => Response | Promise<Response>;
type MockRoute = Readonly<{ method: string; pattern: RegExp; handler: Handler }>;
type UnknownRecord = Readonly<Record<string, unknown>>;

function response(value: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}
function error(code: string, detail: string, status = 400, extra: Readonly<Record<string, unknown>> = {}): Response {
  return response({ error: { code, detail, ...extra } }, status);
}
function record(value: unknown): UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as UnknownRecord : {};
}
async function body(request: Request): Promise<UnknownRecord> {
  try { return record(await request.json()); } catch { return {}; }
}
function page<T>(items: T[], url: URL): Readonly<{ items: T[]; next_cursor: string | null }> {
  const limit = Math.max(1, Number(url.searchParams.get("limit") ?? 100));
  const cursorOffset = Number(url.searchParams.get("cursor")?.replace(/^next-/, ""));
  const offset = Number.isFinite(cursorOffset) && url.searchParams.has("cursor") ? cursorOffset : Number(url.searchParams.get("offset") ?? 0);
  const selected = items.slice(offset, offset + limit);
  return { items: selected, next_cursor: offset + limit < items.length ? `next-${offset + limit}` : null };
}
function filtered<T>(items: T[], url: URL, fields: readonly string[]): T[] {
  let result = items;
  for (const key of fields) {
    const value = url.searchParams.get(key);
    if (value !== null) result = result.filter((item) => String(record(item)[key] ?? "") === value);
  }
  const query = url.searchParams.get("q")?.toLowerCase();
  if (query) result = result.filter((item) => JSON.stringify(item).toLowerCase().includes(query));
  return result;
}
function byId<T extends Readonly<{ id: string }>>(items: T[], id: string): T | undefined {
  return items.find((item) => item.id === id);
}
function inPeriod(issue: Issue, url: URL): boolean {
  const startText = url.searchParams.get("start");
  const endText = url.searchParams.get("end");
  const period = url.searchParams.get("period") ?? "all";
  const end = endText === null ? Date.now() : Date.parse(endText);
  const start = startText !== null ? Date.parse(startText) : period === "all" ? Number.NEGATIVE_INFINITY : end - (period === "24h" ? 86_400_000 : Number.parseInt(period, 10) * 86_400_000);
  const seen = Date.parse(issue.last_seen);
  return Number.isFinite(start) || start === Number.NEGATIVE_INFINITY ? seen >= start && seen <= end : true;
}
function issuePage(url: URL): Readonly<{ items: Issue[]; next_cursor: string | null }> {
  const queryText = url.searchParams.get("query") ?? "";
  let items = state.issues.filter((item) => item.merged_into_id == null && inPeriod(item, url) && evaluateIssueQuery(item, tokenizeIssueQuery(queryText), Date.now()));
  items = filtered(items, url, ["status", "source", "rule_class", "severity"]);
  const assignee = url.searchParams.get("assignee");
  if (assignee === "me") items = items.filter((item) => item.assignee_member_id === "mem-01");
  else if (assignee === "my_teams") items = items.filter((item) => item.assignee_team_id === "team-01");
  else if (assignee === "none") items = items.filter((item) => item.assignee_member_id === null && item.assignee_team_id === null);
  else if (assignee !== null) items = items.filter((item) => assignee === `member:${item.assignee_member_id}` || assignee === `team:${item.assignee_team_id}` || assignee === item.assignee_member_id || assignee === item.assignee_team_id);
  const exposure = url.searchParams.get("exposure");
  if (exposure !== null) items = items.filter((item) => item.exposure_visibility === exposure);
  const sort = url.searchParams.get("sort") ?? "last_seen";
  items.sort((left, right) => sort === "risk" ? (right.risk_score ?? 0) - (left.risk_score ?? 0) : sort === "times_seen" ? right.times_seen - left.times_seen : sort === "priority" ? (left.priority ?? "medium").localeCompare(right.priority ?? "medium") : sort === "values" ? right.distinct_fingerprints - left.distinct_fingerprints : sort === "trending" ? (right.trend?.[13] ?? 0) - (left.trend?.[13] ?? 0) || right.last_seen.localeCompare(left.last_seen) : (right[sort === "first_seen" ? "first_seen" : "last_seen"].localeCompare(left[sort === "first_seen" ? "first_seen" : "last_seen"])));
  return page(items, url);
}

async function routeV13(url: URL, request: Request, method: string): Promise<Response | null> {
  const path = url.pathname;
  const issueId = /^\/api\/issues\/([^/]+)/.exec(path)?.[1];
  const issue = issueId === undefined ? undefined : byId(state.issues, decodeURIComponent(issueId));
  if (method === "GET" && path === "/api/issues/counts") {
    const statuses = ["unresolved", "for_review", "regressed", "escalating", "archived", "resolved"] as const;
    const queryText = url.searchParams.get("query") ?? "";
    const queryTokens = tokenizeIssueQuery(queryText);
    const tabTokens = queryTokens.filter((token) => token.key !== "is");
    const matchingTabs = state.issues.filter((item) => item.merged_into_id == null && inPeriod(item, url) && evaluateIssueQuery(item, tabTokens, Date.now()));
    const matchingQuery = state.issues.filter((item) => item.merged_into_id == null && inPeriod(item, url) && evaluateIssueQuery(item, queryTokens, Date.now()));
    const open = ["unresolved", "acknowledged", "in_progress", "regressed", "escalating"];
    const updatedSince = Date.parse(url.searchParams.get("updated_since") ?? "");
    return response({ tabs: statuses.map((tab) => ({ tab, count: matchingTabs.filter((item) => tab === "for_review" ? item.needs_review && open.includes(item.status) : tab === "unresolved" ? open.includes(item.status) : item.status === tab).length })), updated_since_count: Number.isFinite(updatedSince) ? matchingQuery.filter((item) => Date.parse(item.updated_at ?? item.last_seen) > updatedSince).length : 0 });
  }
  if (path === "/api/issues/bulk" && method === "POST") {
    const value = await body(request);
    const bulkPeriod = new URLSearchParams();
    bulkPeriod.set("period", String(value.period ?? "all"));
    const params = record(value.params);
    const archive = record(params.archive);
    const verification = record(params.verification);
    const requested = Array.isArray(value.ids) ? value.ids.filter((id): id is string => typeof id === "string") : state.issues.filter((item) => item.merged_into_id == null && inPeriod(item, new URL(`http://mock.local/api/issues?${bulkPeriod}`)) && evaluateIssueQuery(item, tokenizeIssueQuery(String(value.query ?? "")), Date.now())).map((item) => item.id);
    const succeeded: string[] = [];
    const failed: { issue_id: string; code: string }[] = [];
    for (const id of requested) {
      const target = byId(state.issues, id);
      if (target === undefined) { failed.push({ issue_id: id, code: "not_found" }); continue; }
      const action = String(value.action ?? "");
      const status = typeof params.status === "string" ? params.status as IssueStatus : target.status;
      const priority = typeof params.priority === "string" ? params.priority as Issue["priority"] : target.priority;
      const assigneeRef = typeof params.assignee_ref === "string" ? params.assignee_ref : null;
      const assigneeMemberId = assigneeRef?.startsWith("member:") ? assigneeRef.slice("member:".length) : params.assignee_ref === null ? null : target.assignee_member_id;
      const assigneeTeamId = assigneeRef?.startsWith("team:") ? assigneeRef.slice("team:".length) : params.assignee_ref === null ? null : target.assignee_team_id;
      const archiveState = action === "archive" ? { kind: String(archive.kind ?? "for_duration"), until: new Date(Date.now() + Number(archive.duration_minutes ?? 1440) * 60_000).toISOString(), threshold: typeof archive.count === "number" ? archive.count : null, baseline: null } : target.archive;
      const updated: Issue = action === "priority" ? { ...target, priority: priority ?? "medium", priority_manual: true } : action === "assign" ? { ...target, assignee_member_id: assigneeMemberId, assignee_team_id: assigneeTeamId } : action === "mark_reviewed" ? { ...target, needs_review: false } : action === "archive" ? { ...target, status: "archived", archive: archiveState } : action === "unarchive" ? { ...target, status: "unresolved", archive: null } : action === "status" ? { ...target, status } : action === "resolve" ? { ...target, status: "resolved", resolution: typeof params.reason === "string" ? params.reason : "manual_unverified" } : action === "request_verification" ? { ...target, status: "pending_verification", resolution: typeof verification.action === "string" ? verification.action : target.resolution } : target;
      state.issues[state.issues.findIndex((item) => item.id === id)] = updated;
      succeeded.push(id);
    }
    return response({ matched: requested.length, processed: succeeded.length + failed.length, truncated: false, succeeded, failed });
  }
  if (path === "/api/issues/merge" && method === "POST") {
    const value = await body(request);
    const primary = byId(state.issues, String(value.primary_id ?? ""));
    const childIds = Array.isArray(value.ids) ? value.ids.filter((id): id is string => typeof id === "string") : [];
    if (primary === undefined) return error("not_found", "Not found.", 404);
    state.issues = state.issues.map((item) => childIds.includes(item.id) ? { ...item, merged_into_id: primary.id } : item);
    const children = state.issues.filter((item) => item.merged_into_id === primary.id);
    const updated = { ...primary, merged_count: children.length };
    state.issues[state.issues.findIndex((item) => item.id === primary.id)] = updated;
    return response(updated);
  }
  if (path === "/api/external-issues/github/repos" && method === "GET") return response({ items: ["example-org/security-tools"] });
  if (path === "/api/ownership-rules/reorder" && method === "POST") {
    const value = await body(request);
    const ids = Array.isArray(value.ids) ? value.ids.filter((id): id is string => typeof id === "string") : [];
    state.ownershipRules = ids.flatMap((id, position) => { const rule = state.ownershipRules.find((item) => item.id === id); return rule === undefined ? [] : [{ ...rule, position }]; });
    return response(null, 204);
  }
  const memberAction = /^\/api\/members\/([^/]+)(?:\/(deactivate|reactivate))?$/.exec(path);
  if (memberAction !== null && method === "PATCH" || memberAction !== null && method === "POST") {
    const handle = decodeURIComponent(memberAction![1]!);
    const current = state.members.find((item) => item.handle === handle);
    if (current === undefined) return error("not_found", "Not found.", 404);
    if (method === "POST") {
      const active = memberAction![2] === "reactivate";
      state.members = state.members.map((item) => item.handle === handle ? { ...item, active } : item);
      return response(null, 204);
    }
    const value = await body(request);
    const updated = { ...current, display_name: typeof value.display_name === "string" ? value.display_name : current.display_name, email: typeof value.email === "string" || value.email === null ? value.email : current.email };
    state.members = state.members.map((item) => item.handle === handle ? updated : item);
    return response(updated);
  }
  if (path === "/api/views" && method === "GET") return response({ items: state.savedViews });
  if (path === "/api/views" && method === "POST") { const value = await body(request); const view = { id: `view-${state.savedViews.length + 1}`, name: String(value.name ?? "Saved view"), query: String(value.query ?? ""), sort: String(value.sort ?? "last_seen"), visibility: value.visibility === "shared" ? "shared" as const : "personal" as const, starred: false, is_default: false }; state.savedViews.push(view); return response(view, 201); }
  if (path === "/api/views/default" && method === "PUT") { const value = await body(request); state.savedViews = state.savedViews.map((item) => ({ ...item, is_default: item.id === value.id })); return response(null, 204); }
  const viewMatch = /^\/api\/views\/([^/]+)(?:\/(star))?$/.exec(path);
  if (viewMatch !== null) { const index = state.savedViews.findIndex((item) => item.id === decodeURIComponent(viewMatch[1]!)); if (index < 0) return error("not_found", "Not found.", 404); const view = state.savedViews[index]!; if (viewMatch[2] === "star") { state.savedViews[index] = { ...view, starred: method === "PUT" }; return response(state.savedViews[index]); } if (method === "DELETE") { state.savedViews.splice(index, 1); return response(null, 204); } if (method === "PATCH") { const value = await body(request); state.savedViews[index] = { ...view, name: typeof value.name === "string" ? value.name : view.name, query: typeof value.query === "string" ? value.query : view.query, sort: typeof value.sort === "string" ? value.sort : view.sort }; return response(state.savedViews[index]); } }
  if (path === "/api/alert-rules" && method === "GET") return response({ items: state.alertRules });
  if (path === "/api/alert-rules" && method === "POST") { const value = await body(request); const rule = { id: `alert-${state.alertRules.length + 1}`, name: String(value.name ?? "New alert"), enabled: false, trigger: String(value.trigger ?? "issue_created"), query: String(value.query ?? ""), action_interval_minutes: Number(value.action_interval_minutes ?? 60), muted_until: null }; state.alertRules.push(rule); return response(rule, 201); }
  if (path === "/api/alert-rules/validate" && method === "POST") return response({ valid: true, errors: [] });
  const alertMatch = /^\/api\/alert-rules\/([^/]+)(?:\/(history|enable|disable|mute|unmute))?$/.exec(path);
  if (alertMatch !== null) { const id = decodeURIComponent(alertMatch[1]!); const index = state.alertRules.findIndex((item) => item.id === id); if (index < 0) return error("not_found", "Not found.", 404); const rule = state.alertRules[index]!; if (alertMatch[2] === "history") return response({ items: state.alertFires.filter((fire) => fire.rule_id === id) }); if (alertMatch[2] !== undefined) { const value = alertMatch[2] === "mute" ? await body(request) : {}; const updated = { ...rule, enabled: alertMatch[2] === "enable" ? true : alertMatch[2] === "disable" ? false : rule.enabled, muted_until: alertMatch[2] === "mute" ? typeof value.until === "string" ? value.until : new Date(Date.now() + 3_600_000).toISOString() : alertMatch[2] === "unmute" ? null : rule.muted_until }; state.alertRules[index] = updated; return response(updated); } if (method === "GET") return response(rule); if (method === "DELETE") { state.alertRules.splice(index, 1); return response(null, 204); } if (method === "PUT") { const value = await body(request); state.alertRules[index] = { ...rule, name: typeof value.name === "string" ? value.name : rule.name, query: typeof value.query === "string" ? value.query : rule.query, action_interval_minutes: typeof value.action_interval_minutes === "number" ? value.action_interval_minutes : rule.action_interval_minutes }; return response(state.alertRules[index]); } }
  if (path === "/api/auth/me/notification-settings" && method === "GET") return response({ items: state.notificationSettings });
  if (path === "/api/auth/me/notification-settings" && method === "PUT") { const value = await body(request); if (Array.isArray(value.items)) state.notificationSettings = value.items.filter((item): item is { category: string; preference: "email" | "slack" | "off" } => typeof item === "object" && item !== null && "category" in item && "preference" in item) as typeof state.notificationSettings; return response({ items: state.notificationSettings }); }
  if (path === "/api/auth/me/tokens" && method === "GET") return response({ items: state.selfTokens });
  if (path === "/api/auth/me/tokens" && method === "POST") { const value = await body(request); const token = { id: `self-token-${state.selfTokens.length + 1}`, label: String(value.label ?? "Personal token"), scopes: Array.isArray(value.scopes) ? value.scopes.filter((item): item is string => typeof item === "string") : [], preview: "ts_live_••••71a2", created_at: new Date().toISOString() }; state.selfTokens.push(token); return response({ id: token.id, token: "mock-token-value" }, 201); }
  const tokenMatch = /^\/api\/auth\/me\/tokens\/([^/]+)\/revoke$/.exec(path); if (tokenMatch !== null && method === "POST") { state.selfTokens = state.selfTokens.filter((item) => item.id !== decodeURIComponent(tokenMatch[1]!)); return response(null, 204); }
  if (path === "/api/dashboard" && method === "GET") return response({ period: url.searchParams.get("period") ?? "14d", open_issues: state.issues.filter((item) => ["unresolved", "acknowledged", "in_progress", "regressed", "escalating"].includes(item.status)).length, new_issues: 8, resolved_issues: 4, findings: state.issues.reduce((total, item) => total + item.times_seen, 0), trend: Array.from({ length: 14 }, (_, index) => ({ start: new Date(Date.now() - (13 - index) * 86_400_000).toISOString(), count: index + 2 })) });
  if (path === "/api/integrations" && method === "GET") return response({ items: state.integrations });
  const integrationMatch = /^\/api\/integrations\/([^/]+)\/test$/.exec(path); if (integrationMatch !== null && method === "POST") { const item = state.integrations.find((entry) => entry.kind === decodeURIComponent(integrationMatch[1]!)); return item?.configured ? response({ status: "passed", detail: "Connection test passed." }) : error("integration_unconfigured", "Integration is not configured.", 422); }
  if (path === "/api/ownership-rules" && method === "GET") return response({ items: state.ownershipRules });
  if (path === "/api/ownership-rules" && method === "POST") { const value = await body(request); const rule = { id: `ownership-${state.ownershipRules.length + 1}`, source: typeof value.source === "string" ? value.source : null, pattern: String(value.pattern ?? "example.test/*"), team_ref: String(value.team_ref ?? "team:engineering"), position: state.ownershipRules.length }; state.ownershipRules.push(rule); return response(rule, 201); }
  if (path === "/api/ownership-rules/test" && method === "POST") { const value = await body(request); const source = typeof value.source === "string" ? value.source : ""; const locator = typeof value.display_locator === "string" ? value.display_locator : ""; const matched = state.ownershipRules.find((rule) => (rule.source === null || rule.source === source) && locator.startsWith(rule.pattern.replace(/\*.*$/, ""))); return response({ team_ref: matched?.team_ref ?? null }); }
  if (path === "/api/ownership-rules/reorder" && method === "POST") return response(null, 204);
  const ownershipMatch = /^\/api\/ownership-rules\/([^/]+)$/.exec(path); if (ownershipMatch !== null) { const id = decodeURIComponent(ownershipMatch[1]!); const index = state.ownershipRules.findIndex((item) => item.id === id); if (index < 0) return error("not_found", "Not found.", 404); if (method === "DELETE") { state.ownershipRules.splice(index, 1); return response(null, 204); } if (method === "PUT") { const value = await body(request); state.ownershipRules[index] = { ...state.ownershipRules[index]!, ...value } as typeof state.ownershipRules[number]; return response(state.ownershipRules[index]); } }
  if (issue !== undefined && issueId !== undefined) {
    const decoded = decodeURIComponent(issueId);
    const eventSelection = /^\/api\/issues\/[^/]+\/events\/([^/]+)$/.exec(path);
    if (eventSelection !== null) { const selector = decodeURIComponent(eventSelection[1]!); const issueEvents = eventFixtures.filter((item) => item.issue_id === decoded).sort((left, right) => left.observed_at.localeCompare(right.observed_at) || left.id.localeCompare(right.id)); const events = issueEvents.length > 0 ? issueEvents : eventFixtures; const foundIndex = events.findIndex((item) => item.id === selector); const index = selector === "oldest" || selector === "recommended" ? 0 : selector === "latest" ? events.length - 1 : Math.max(0, foundIndex); const event = events[index]!; return response({ event, previous_id: index > 0 ? events[index - 1]!.id : null, next_id: index + 1 < events.length ? events[index + 1]!.id : null, position: index + 1, total: events.length }); }
    if (path.endsWith("/tags")) return response({ items: [{ key: "source", value: issue.source, count: issue.times_seen }, { key: "rule", value: issue.rule_class, count: issue.times_seen }], total: issue.times_seen });
    if (path.endsWith("/stats")) return response({ buckets: Array.from({ length: 14 }, (_, index) => ({ start: new Date(Date.now() - (13 - index) * 86_400_000).toISOString(), count: issue.trend?.[index] ?? 0 })) });
    if (path.endsWith("/activity")) return response({ items: state.comments.filter((item) => item.issue_id === decoded).map((item) => ({ id: item.id, kind: "commented", actor: { kind: "member" as const, ref: "member:mem-01", display_name: "Alice Example" }, body: item.display_text, at: new Date().toISOString() })) });
    if (path.endsWith("/external-issues") && method === "GET") return response({ items: state.externalLinks.filter((item) => item.issue_id === decoded) });
    if (path.endsWith("/external-issues/github") && method === "POST") { const value = await body(request); const link = { id: `ext-${state.externalLinks.length + 1}`, issue_id: decoded, kind: "github" as const, url: `https://github.example.test/${String(value.repo ?? "example-org/security-tools")}/issues/42`, title: typeof value.title === "string" ? value.title : "Example issue", created_at: new Date().toISOString() }; state.externalLinks.push(link); return response(link, 201); }
    const extId = /\/external-issues\/([^/]+)$/.exec(path)?.[1]; if (extId !== undefined && method === "DELETE") { state.externalLinks = state.externalLinks.filter((item) => item.id !== decodeURIComponent(extId)); return response(null, 204); }
    if (path.endsWith("/external-issues") && method === "POST") { const value = await body(request); const link = { id: `ext-${state.externalLinks.length + 1}`, issue_id: decoded, kind: "url" as const, url: String(value.url ?? "https://example.test/issue"), title: null, created_at: new Date().toISOString() }; state.externalLinks.push(link); return response(link, 201); }
    if (path.endsWith("/bookmark")) { state.issues = state.issues.map((item) => item.id === decoded ? { ...item, bookmarked: method === "PUT" } : item); return response(null, 204); }
    if (path.endsWith("/subscription")) { const value = await body(request); const subscribed = value.subscribed !== false; state.subscriptions = subscribed ? [...new Set([...state.subscriptions, decoded])] : state.subscriptions.filter((id) => id !== decoded); return response({ subscribed }); }
    if (path.endsWith("/archive") && method === "POST") { state.issues = state.issues.map((item) => item.id === decoded ? { ...item, status: "archived", archive: { kind: "for_duration", until: new Date(Date.now() + 86_400_000).toISOString(), threshold: null, baseline: null } } : item); return response(state.issues.find((item) => item.id === decoded)); }
    if (path.endsWith("/unarchive") || path.endsWith("/review")) { state.issues = state.issues.map((item) => item.id === decoded ? { ...item, status: path.endsWith("/unarchive") ? "unresolved" : item.status, archive: null, needs_review: false } : item); return response(state.issues.find((item) => item.id === decoded)); }
    const unmerge = /\/unmerge$/.test(path); if (unmerge && method === "POST") { const value = await body(request); const requestedIds = Array.isArray(value.ids) ? value.ids.filter((id): id is string => typeof id === "string") : []; const selectedIds = requestedIds.length === 0 ? null : requestedIds; state.issues = state.issues.map((item) => item.merged_into_id === decoded && (selectedIds === null || selectedIds.includes(item.id)) ? { ...item, merged_into_id: null, needs_review: true } : item); const primary = state.issues.find((item) => item.id === decoded); if (primary === undefined) return error("not_found", "Not found.", 404); const updated = { ...primary, merged_count: state.issues.filter((item) => item.merged_into_id === decoded).length }; state.issues[state.issues.findIndex((item) => item.id === decoded)] = updated; return response(updated); }
    const commentMatch = /\/comments\/([^/]+)$/.exec(path); if (commentMatch !== null && (method === "PATCH" || method === "DELETE")) { const id = decodeURIComponent(commentMatch[1]!); const found = state.comments.some((item) => item.issue_id === decoded && item.id === id); if (!found) return error("not_found", "Not found.", 404); if (method === "DELETE") { state.comments = state.comments.filter((item) => !(item.issue_id === decoded && item.id === id)); return response(null, 204); } const value = await body(request); state.comments = state.comments.map((item) => item.id === id ? { ...item, display_text: String(value.body ?? item.display_text) } : item); return response({ id }); }
  }
  return null;
}
const routes: MockRoute[] = [
  { method: "GET", pattern: /^\/api\/auth\/config$/, handler: () => response({ oidc: { issuer: "https://idp.example.test/realms/telescreen", client_id: "telescreen-ui", authorization_endpoint: "https://idp.example.test/authorize", token_endpoint: "https://idp.example.test/token", end_session_endpoint: "https://idp.example.test/logout", scopes: ["openid", "profile", "email"] }, token_login: true }) },
  { method: "POST", pattern: /^\/api\/auth\/session$/, handler: () => response({ member: { handle: "alex", display_name: "Alex Morgan" }, role: "admin", permissions: ["users.read", "users.write", "templates.read", "templates.write"] }) },
  { method: "GET", pattern: /^\/api\/auth\/me$/, handler: () => response({ kind: "token", member: { handle: "alex", display_name: "Alex Morgan" }, role: "admin", permissions: ["users.read", "users.write", "templates.read", "templates.write"] }) },
  { method: "GET", pattern: /^\/api\/notification-templates$/, handler: () => response({ items: [...new Map(state.templates.map((item) => [item.name, item])).values()].map((item) => ({ name: item.name, event_kind: item.event_kind, builtin: item.name.startsWith("system."), description: item.description, active_version: state.templates.find((version) => version.name === item.name && version.active)?.version ?? null, latest_version: Math.max(...state.templates.filter((version) => version.name === item.name).map((version) => version.version)), updated_at: null })) }) },
  { method: "GET", pattern: /^\/api\/notification-templates\/([^/]+)\/versions\/(\d+)$/, handler: (_url, _request, match) => { const item = state.templates.find((value) => value.name === decodeURIComponent(match[1]!) && value.version === Number(match[2])); return item ? response(item) : error("not_found", "Not found.", 404); } },
  { method: "GET", pattern: /^\/api\/notification-templates\/([^/]+)$/, handler: (_url, _request, match) => { const name = decodeURIComponent(match[1]!); const versions = state.templates.filter((item) => item.name === name); if (!versions.length) return error("not_found", "Not found.", 404); return response({ name, event_kind: versions[0]!.event_kind, builtin: name.startsWith("system."), active_version: versions.find((item) => item.active)?.version ?? null, versions: versions.map(({ version, active }) => ({ version, active, created_by: "member:alex", created_at: null })), variables: ["issue.short_id", "workflow.name"] }); } },
  { method: "POST", pattern: /^\/api\/notification-templates\/validate$/, handler: async (_url, request) => { const value = await body(request); const valid = typeof value.subject === "string" && typeof value.body === "string"; return response({ valid, errors: valid ? [] : [{ field: "body", code: "required" }] }); } },
  { method: "POST", pattern: /^\/api\/notification-templates\/preview$/, handler: async (_url, request) => { const value = await body(request); const subject = String(value.subject ?? "Workflow update"); const text = String(value.body ?? "A masked Telescreen notification is available."); return response({ email: { subject, body: text, dlp_verdict: "allow" }, slack: { subject, body: text, dlp_verdict: "allow" } }); } },
  { method: "POST", pattern: /^\/api\/notification-templates$/, handler: async (_url, request) => { const value = await body(request); const name = String(value.name ?? "custom.template"); const versions = state.templates.filter((item) => item.name === name); const version = Math.max(0, ...versions.map((item) => item.version)) + 1; const item: MockTemplateVersion = { name, version, event_kind: String(value.event_kind ?? "workflow_notify"), description: typeof value.description === "string" ? value.description : null, subject: String(value.subject ?? ""), body: String(value.body ?? ""), ...(typeof value.email_subject === "string" ? { email_subject: value.email_subject } : {}), ...(typeof value.email_body === "string" ? { email_body: value.email_body } : {}), ...(typeof value.slack_body === "string" ? { slack_body: value.slack_body } : {}), active: false }; state.templates.push(item); return response({ name, version }, 201); } },
  { method: "POST", pattern: /^\/api\/notification-templates\/([^/]+)\/activate$/, handler: async (_url, request, match) => { const name = decodeURIComponent(match[1]!); const value = await body(request); const version = Number(value.version); if (!state.templates.some((item) => item.name === name && item.version === version)) return error("not_found", "Not found.", 404); state.templates = state.templates.map((item) => item.name === name ? { ...item, active: item.version === version } : item); const selected = state.templates.find((item) => item.name === name && item.version === version)!; return response({ name, event_kind: selected.event_kind, builtin: name.startsWith("system."), description: selected.description, active_version: version, latest_version: Math.max(...state.templates.filter((item) => item.name === name).map((item) => item.version)), updated_at: null }); } },
  { method: "GET", pattern: /^\/api\/users$/, handler: () => response({ items: state.users }) },
  { method: "GET", pattern: /^\/api\/roles$/, handler: () => response(state.roleMatrix) },
  { method: "PUT", pattern: /^\/api\/users\/([^/]+)\/role$/, handler: async (_url, request, match) => { const handle = decodeURIComponent(match[1]!); const user = state.users.find((item) => item.handle === handle); if (!user) return error("not_found", "Not found.", 404); const value = await body(request); const role = typeof value.role === "string" ? value.role : null; if (user.role === "admin" && role !== "admin" && state.users.filter((item) => item.role === "admin").length === 1) return error("last_admin", "At least one administrator is required.", 409); const updated = { ...user, role }; state.users[state.users.findIndex((item) => item.handle === handle)] = updated; return response(updated); } },
  { method: "GET", pattern: /^\/api\/tokens$/, handler: () => response({ items: state.tokens }) },
  { method: "POST", pattern: /^\/api\/tokens$/, handler: async (_url, request) => { const value = await body(request); const id = `tok-0${state.nextTokenNumber++}`; const token = { id, label: String(value.label ?? "API token"), scopes: Array.isArray(value.scopes) ? value.scopes.filter((item): item is string => typeof item === "string") : [], member_handle: typeof value.member_handle === "string" ? value.member_handle : null, created_at: new Date().toISOString(), revoked_at: null }; state.tokens.push(token); return response({ id, token: "mock-token-value" }, 201); } },
  { method: "POST", pattern: /^\/api\/tokens\/([^/]+)\/revoke$/, handler: (_url, _request, match) => { const token = state.tokens.find((item) => item.id === match[1]); if (token) state.tokens[state.tokens.findIndex((item) => item.id === token.id)] = { ...token, revoked_at: new Date().toISOString() }; return response(null, 204); } },
  { method: "GET", pattern: /^\/api\/health$/, handler: () => response({ status: "ok" }) },
  { method: "GET", pattern: /^\/api\/exceptions$/, handler: (url) => { let items = filtered(state.exceptions, url, ["status", "issue_id"]); const approver = url.searchParams.get("approver"); if (approver === "me") items = items.filter((item) => item.approver_ref === "member:alex"); else if (approver !== null) items = items.filter((item) => item.approver_ref === approver); return response(page(items, url)); } },
  { method: "POST", pattern: /^\/api\/exceptions\/([^/]+)\/(approve|reject|revoke)$/, handler: async (_url, request, match) => {
    const item = byId(state.exceptions, match[1]!); if (!item) return error("not_found", "Not found.", 404);
    const action = match[2]!; const updated = { ...item, status: action === "approve" ? "active" as const : action === "reject" ? "rejected" as const : "revoked" as const, decided_by: "member:alex", decided_at: new Date().toISOString(), expires_at: action === "approve" && item.duration !== "until_regressed" ? new Date(Date.now() + (item.duration === "7d" ? 7 : item.duration === "30d" ? 30 : 90) * 86_400_000).toISOString() : null };
    state.exceptions[state.exceptions.findIndex((entry) => entry.id === item.id)] = updated;
    if (action === "approve") { const issue = byId(state.issues, item.issue_id); if (issue) state.issues[state.issues.findIndex((entry) => entry.id === issue.id)] = { ...issue, status: "ignored" }; }
    return response(updated);
  } },
  { method: "GET", pattern: /^\/api\/policies$/, handler: (url) => response(page(state.policies, url)) },
  { method: "POST", pattern: /^\/api\/policies\/validate$/, handler: async (_url, request) => { const value = await body(request); const valid = typeof value.source_text === "string" && value.source_text.trim().length > 0; return response({ valid, errors: valid ? [] : [{ path: "$", code: "policy_empty" }] }); } },
  { method: "POST", pattern: /^\/api\/policies$/, handler: async (_url, request) => {
    const value = await body(request); const name = typeof value.source_text === "string" ? (/^name:\s*(.+)$/m.exec(value.source_text)?.[1]?.trim() ?? "New policy") : "New policy";
    const version = state.nextPolicyVersion++; const policyVersion = { name, version, source_format: value.source_format === "yaml" ? "yaml" as const : "json" as const, source_text: String(value.source_text ?? ""), active: false };
    state.policyVersions.push(policyVersion); if (!state.policies.some((item) => item.name === name)) state.policies.push({ name, active_version: null, effect: "violate", enabled: false, owner_team: null });
    return response(policyVersion, 201);
  } },
  { method: "GET", pattern: /^\/api\/policies\/([^/]+)\/versions\/(\d+)$/, handler: (_url, _request, match) => { const item = state.policyVersions.find((entry) => entry.name === decodeURIComponent(match[1]!) && entry.version === Number(match[2])); return item ? response(item) : error("not_found", "Not found.", 404); } },
  { method: "POST", pattern: /^\/api\/policies\/([^/]+)\/(activate|disable)$/, handler: async (_url, request, match) => {
    const name = decodeURIComponent(match[1]!); const policy = state.policies.find((item) => item.name === name); if (!policy) return error("not_found", "Not found.", 404);
    const value = await body(request); const activate = match[2] === "activate"; const activeVersion = Number(value.version ?? 0);
    const updated = { ...policy, enabled: activate, active_version: activate ? activeVersion : policy.active_version }; state.policies[state.policies.findIndex((entry) => entry.name === name)] = updated;
    state.policyVersions = state.policyVersions.map((entry) => entry.name === name ? { ...entry, active: activate && entry.version === activeVersion } : entry); return response(updated);
  } },
  { method: "GET", pattern: /^\/api\/policies\/([^/]+)$/, handler: (_url, _request, match) => { const name = decodeURIComponent(match[1]!); return response({ name, versions: state.policyVersions.filter((item) => item.name === name) }); } },
  { method: "GET", pattern: /^\/api\/collectors\/([^/]+)\/coverage$/, handler: () => response(state.coverageSummary) },
  { method: "GET", pattern: /^\/api\/collectors\/([^/]+)\/coverage\/resources$/, handler: (url) => response(page(filtered(state.coverageResources, url, ["state"]), url)) },
  { method: "GET", pattern: /^\/api\/members\/([^/]+)\/identities$/, handler: (_url, _request, match) => response({ items: state.identities.filter((identity) => identity.id.startsWith(`${decodeURIComponent(match[1]!)}:`)), next_cursor: null }) },
  { method: "POST", pattern: /^\/api\/members\/([^/]+)\/identities$/, handler: async (_url, request, match) => { const value = await body(request); const identity = { id: `${decodeURIComponent(match[1]!)}:identity-${state.identities.length + 1}`, kind: String(value.kind ?? "provider"), origin: "manual", key_prefix: "hmac:v1:", created_at: new Date().toISOString() }; state.identities.push(identity); return response(identity, 201); } },
  { method: "DELETE", pattern: /^\/api\/members\/([^/]+)\/identities\/([^/]+)$/, handler: (_url, _request, match) => { state.identities = state.identities.filter((identity) => identity.id !== decodeURIComponent(match[2]!)); return response(null, 204); } },
  { method: "POST", pattern: /^\/api\/issues\/([^/]+)\/(remediation|resolve|exceptions)$/, handler: async (_url, request, match) => {
    const issue = byId(state.issues, match[1]!); if (!issue) return error("not_found", "Not found.", 404); const value = await body(request); const action = match[2]!;
    if (action === "remediation") { const updated = { ...issue, status: "pending_verification" as const }; state.issues[state.issues.findIndex((entry) => entry.id === issue.id)] = updated; return response({ issue_ids: [issue.id], rescans_enqueued: 1 }, 202); }
    if (action === "resolve") { const updated = { ...issue, status: "resolved" as const, resolution: String(value.reason ?? "false_positive") }; state.issues[state.issues.findIndex((entry) => entry.id === issue.id)] = updated; return response(updated); }
    const duration = ["7d", "30d", "90d", "until_regressed"].includes(String(value.duration)) ? String(value.duration) as "7d" | "30d" | "90d" | "until_regressed" : "7d";
    const decidedAt = state.isMockAdmin ? new Date().toISOString() : null;
    const expiryDays = duration === "7d" ? 7 : duration === "30d" ? 30 : duration === "90d" ? 90 : 0;
    const expiresAt = state.isMockAdmin && expiryDays > 0 ? new Date(Date.now() + expiryDays * 86_400_000).toISOString() : null;
    const exception = { id: `exc-0${state.nextExceptionNumber++}`, issue_id: issue.id, status: state.isMockAdmin ? "active" as const : "requested" as const, reason: "Mock exception request", duration, requested_by: "member:alex", decided_by: state.isMockAdmin ? "member:alex" : null, approver_ref: "member:alex", expires_at: expiresAt, created_at: new Date().toISOString(), decided_at: decidedAt };
    state.exceptions.push(exception); if (state.isMockAdmin) state.issues[state.issues.findIndex((entry) => entry.id === issue.id)] = { ...issue, status: "ignored" }; return response(exception, 201);
  } },
  { method: "GET", pattern: /^\/api\/issues\/([^/]+)\/occurrences$/, handler: (_url, _request, match) => response({ items: state.occurrences.filter((item) => item.issue_id === match[1] || match[1] === "iss-01" && ["iss-01", "iss-06"].includes(item.issue_id)), key_versions: [1] }) },
  { method: "GET", pattern: /^\/api\/issues$/, handler: (url) => response(issuePage(url)) },
  { method: "GET", pattern: /^\/api\/issues\/stats$/, handler: (url) => {
    const days = Number(url.searchParams.get("days") ?? 14);
    return response({ buckets: Array.from({ length: days }, (_, index) => ({ start: new Date(Date.now() - (days - index - 1) * 86_400_000).toISOString(), count: index % 4 })) });
  } },
  { method: "GET", pattern: /^\/api\/issues\/([^/]+)\/events$/, handler: (_url, _request, match) => response({ items: eventFixtures.filter((item) => item.issue_id === match[1]), next_cursor: null }) },
  { method: "GET", pattern: /^\/api\/issues\/([^/]+)\/history$/, handler: (_url, _request, match) => response({ items: historyFixtures.filter((item) => item.issue_id === match[1]), next_cursor: null }) },
  { method: "GET", pattern: /^\/api\/issues\/([^/]+)$/, handler: (_url, _request, match) => {
    const issue = byId(state.issues, match[1]!); return issue ? response({ ...issue, exposure: { visibility: issue.exposure_visibility ?? "unknown", external_principal_count: issue.exposure_visibility === "external_shared" ? 2 : 0, shared_drive: false, channel_kind: null, repo_visibility: null, is_fork: false }, exposure_changed_at: null, asset_tier: "standard", assignee_ref: issue.assignee_member_id === null ? null : `member:${issue.assignee_member_id}`, owner: { present: false, member_ref: null }, active_exception: state.exceptions.find((item) => item.issue_id === issue.id && item.status === "active") ?? null }) : error("not_found", "Not found.", 404);
  } },
  { method: "PATCH", pattern: /^\/api\/issues\/([^/]+)$/, handler: async (_url, request, match) => {
    const issue = byId(state.issues, match[1]!); if (!issue) return error("not_found", "Not found.", 404);
    const value = await body(request);
    const status = value.status;
    const updated: Issue = {
      ...issue,
      status: typeof status === "string" ? status as IssueStatus : issue.status,
      ...(value.priority === "high" || value.priority === "medium" || value.priority === "low" ? { priority: value.priority, priority_manual: true } : {}),
      assignee_member_id: value.assignee_ref === null ? null : typeof value.assignee_ref === "string" && value.assignee_ref.startsWith("member:") ? value.assignee_ref.slice(7) : issue.assignee_member_id,
      assignee_team_id: value.assignee_ref === null ? null : typeof value.assignee_ref === "string" && value.assignee_ref.startsWith("team:") ? value.assignee_ref.slice(5) : issue.assignee_team_id,
    };
    const index = state.issues.findIndex((item) => item.id === issue.id);
    state.issues[index] = updated;
    return response(updated);
  } },
  { method: "POST", pattern: /^\/api\/issues\/([^/]+)\/comments$/, handler: (_url, _request, match) => {
    if (!byId(state.issues, match[1]!)) return error("not_found", "Not found.", 404);
    state.comments = [...state.comments, { id: `comment-${state.comments.length + 1}`, issue_id: match[1]!, display_text: "Comment saved in mock mode." }];
    return response({ accepted: true }, 201);
  } },
  { method: "GET", pattern: /^\/api\/workflows$/, handler: () => response({ items: workflowRefs() }) },
  { method: "GET", pattern: /^\/api\/workflows\/schema$/, handler: () => new Response("{\"type\":\"object\"}", { headers: { "Content-Type": "application/schema+json" } }) },
  { method: "GET", pattern: /^\/api\/workflows\/([^/]+)\/versions\/(\d+)$/, handler: (_url, _request, match) => {
    const workflow = state.workflows.find((item) => item.name === decodeURIComponent(match[1]!) && item.version === Number(match[2]));
    return workflow ? response(workflow) : error("not_found", "Not found.", 404);
  } },
  { method: "GET", pattern: /^\/api\/workflows\/([^/]+)$/, handler: (_url, _request, match) => response({ items: workflowRefs().filter((item) => item.name === decodeURIComponent(match[1]!)) }) },
  { method: "POST", pattern: /^\/api\/workflows\/validate$/, handler: async (_url, request) => {
    const value = await body(request); const source = String(value.source_text ?? "");
    return response({ valid: source.trim().length > 0, errors: source.trim().length > 0 ? [] : [{ path: "$", code: "workflow_empty" }] });
  } },
  { method: "POST", pattern: /^\/api\/workflows$/, handler: async (_url, request) => {
    const value = await body(request); const source = String(value.source_text ?? "");
    if (!source.trim()) return response({ valid: false, errors: [{ path: "$", code: "workflow_empty" }] }, 422);
    const workflow: WorkflowVersion = { id: `wf-0${state.nextWorkflowNumber}`, name: `Workflow ${state.nextWorkflowNumber}`, version: 1, active: false, source_format: value.source_format === "yaml" ? "yaml" : "json", source_text: "name: Mock workflow", compiled_json: "{}" };
    state.nextWorkflowNumber += 1; state.workflows.push(workflow); return response({ id: workflow.id, name: workflow.name, version: workflow.version, active: workflow.active }, 201);
  } },
  { method: "POST", pattern: /^\/api\/workflows\/([^/]+)\/activate$/, handler: async (_url, request, match) => {
    const value = await body(request); const workflow = state.workflows.find((item) => item.name === decodeURIComponent(match[1]!) && item.version === Number(value.version));
    if (!workflow) return error("not_found", "Not found.", 404);
    const updated: WorkflowVersion = { ...workflow, active: true };
    const index = state.workflows.findIndex((item) => item.id === workflow.id);
    state.workflows[index] = updated;
    return response({ id: updated.id, name: updated.name, version: updated.version, active: true });
  } },
  { method: "POST", pattern: /^\/api\/workflows\/([^/]+)\/runs$/, handler: () => {
    const run = { id: `run-0${state.nextRunNumber}`, definition_id: "wf-01", issue_id: null, status: "running", trigger: "manual", started_at: new Date().toISOString(), finished_at: null } as const;
    state.nextRunNumber += 1; state.runs.unshift(run); return response({ run_id: run.id }, 201);
  } },
  { method: "GET", pattern: /^\/api\/runs$/, handler: (url) => {
    let items = filtered(state.runs, url, ["status", "issue_id"]);
    const workflow = url.searchParams.get("workflow");
    if (workflow !== null) items = items.filter((run) => run.definition_id === workflow || state.workflows.some((item) => item.id === run.definition_id && item.name === workflow));
    return response({ items });
  } },
  { method: "GET", pattern: /^\/api\/runs\/([^/]+)$/, handler: (_url, _request, match) => {
    const run = state.runs.find((item) => item.id === match[1]); return response(run ? { ...runDetailFixture, run } : { run: state.runs[0], steps: [], transitions: [], human_tasks: [] });
  } },
  { method: "POST", pattern: /^\/api\/runs\/([^/]+)\/cancel$/, handler: (_url, _request, match) => {
    const run = byId(state.runs, match[1]!);
    if (run) { const index = state.runs.findIndex((item) => item.id === run.id); state.runs[index] = { ...run, status: "cancelled" }; }
    return response({ run_id: match[1], status: "cancelled" });
  } },
  { method: "GET", pattern: /^\/api\/human-tasks$/, handler: (url) => { const assignee = url.searchParams.get("assignee"); let tasks = filtered(state.tasks, url, ["status"]); if (assignee === "me") tasks = tasks.filter((task) => task.assignee_ref === "member:alex"); else if (assignee !== null) tasks = tasks.filter((task) => task.assignee_ref === assignee); return response({ items: tasks }); } },
  { method: "POST", pattern: /^\/api\/human-tasks\/([^/]+)\/complete$/, handler: (_url, _request, match) => {
    const task = byId(state.tasks, match[1]!);
    if (task) { const index = state.tasks.findIndex((item) => item.id === task.id); state.tasks[index] = { ...task, status: "completed" }; }
    return response({ id: match[1], status: "completed" });
  } },
  { method: "GET", pattern: /^\/api\/evidence$/, handler: (url) => response({ items: filtered(state.evidence, url, ["issue_id"]) }) },
  { method: "PUT", pattern: /^\/api\/evidence$/, handler: (url) => {
    const evidence: Evidence = { id: `evi-0${state.nextEvidenceNumber}`, issue_id: url.searchParams.get("issue_id") ?? "iss-01", step_run_id: url.searchParams.get("step_run_id"), display_filename: `evidence-${state.nextEvidenceNumber}.txt`, content_type: "text/plain", size_bytes: 0, sha256: null, verdict: "accepted", rejection_rule_classes: [], uploaded_by: "member:alex", created_at: new Date().toISOString() };
    state.nextEvidenceNumber += 1; state.evidence.push(evidence); return response(evidence, 201);
  } },
  { method: "GET", pattern: /^\/api\/evidence\/([^/]+)\/content$/, handler: () => new Response("masked evidence", { headers: { "Content-Type": "application/octet-stream" } }) },
  { method: "GET", pattern: /^\/api\/evidence\/([^/]+)$/, handler: (_url, _request, match) => {
    const evidence = byId(state.evidence, match[1]!); return evidence ? response(evidence) : error("not_found", "Not found.", 404);
  } },
  { method: "GET", pattern: /^\/api\/escalation-policies$/, handler: () => response({ items: state.escalations }) },
  { method: "POST", pattern: /^\/api\/escalation-policies$/, handler: async (_url, request) => {
    const value = await body(request); const policy = { id: `esc-0${state.escalations.length + 1}`, name: String(value.name ?? "Response policy"), tiers_json: String(value.tiers_json ?? "[]") };
    state.escalations.push(policy); return response(policy, 201);
  } },
  { method: "PUT", pattern: /^\/api\/escalation-policies\/([^/]+)$/, handler: async (_url, request, match) => {
    const value = await body(request); const policy = state.escalations.find((item) => item.name === decodeURIComponent(match[1]!));
    if (!policy) return error("not_found", "Not found.", 404);
    const updated = { ...policy, tiers_json: String(value.tiers_json ?? "[]") };
    const index = state.escalations.findIndex((item) => item.id === policy.id);
    state.escalations[index] = updated;
    return response(updated);
  } },
  { method: "GET", pattern: /^\/api\/members$/, handler: (url) => response(page(state.members, url)) },
  { method: "GET", pattern: /^\/api\/teams$/, handler: (url) => response(page(teamFixtures, url)) },
  { method: "POST", pattern: /^\/api\/teams$/, handler: async (_url, request) => { const value = await body(request); const team = { id: `team-${state.teams.length + 1}`, slug: String(value.slug ?? "new-team"), name: String(value.name ?? "New team"), email: null, slack_channel: null, lead: typeof value.lead === "string" ? value.lead : null }; state.teams.push(team); return response(team, 201); } },
  { method: "PUT", pattern: /^\/api\/teams\/([^/]+)\/lead$/, handler: async (_url, request, match) => { const team = state.teams.find((item) => item.slug === decodeURIComponent(match[1]!)); if (!team) return error("not_found", "Not found.", 404); const value = await body(request); const lead = typeof value.lead === "string" ? value.lead : null; state.teams[state.teams.findIndex((item) => item.id === team.id)] = { ...team, lead }; return response(null, 204); } },
  { method: "PUT", pattern: /^\/api\/teams\/([^/]+)\/members$/, handler: async (_url, request, match) => { const team = state.teams.find((item) => item.slug === decodeURIComponent(match[1]!)); if (!team) return error("not_found", "Not found.", 404); const value = await body(request); if (typeof value.lead === "string" || value.lead === null) state.teams[state.teams.findIndex((item) => item.id === team.id)] = { ...team, lead: value.lead }; return response(null, 204); } },
  { method: "GET", pattern: /^\/api\/collectors$/, handler: () => response({ items: state.collectors.map((item) => ({ ...item, health: item.health ?? { status: "ok", conditions: [], last_success_at: null, last_failure_at: null, consecutive_failures: 0, last_event_at: null, watch_expires_at: null, event_only: false } })) }) },
  { method: "POST", pattern: /^\/api\/collectors$/, handler: async (_url, request) => {
    const value = await body(request); const config = { ...value, id: `col-0${state.nextCollectorNumber}` } as unknown as typeof state.collectors[number];
    state.nextCollectorNumber += 1; state.collectors.push(config); return response({ id: config.id }, 201);
  } },
  { method: "PATCH", pattern: /^\/api\/collectors\/([^/]+)$/, handler: async (_url, request, match) => {
    const config = byId(state.collectors, match[1]!); if (!config) return error("not_found", "Not found.", 404);
    const value = await body(request); Object.assign(config, value); return response({ id: config.id });
  } },
  { method: "POST", pattern: /^\/api\/collectors\/([^/]+)\/run$/, handler: (_url, _request, match) => response({ id: match[1] }, 202) },
  { method: "POST", pattern: /^\/api\/collectors\/([^/]+)\/sweep$/, handler: (_url, _request, match) => response({ map_job_id: state.mapJobs.find((job) => job.collector_config_id === match[1])?.id ?? "map-01" }, 202) },
  { method: "GET", pattern: /^\/api\/map-jobs$/, handler: (url) => response(page(filtered(mapJobSummaryFixtures, url, ["collector_config_id", "status"]), url)) },
  { method: "GET", pattern: /^\/api\/map-jobs\/([^/]+)$/, handler: (_url, _request, match) => { const job = byId(state.mapJobs, match[1]!); return job ? response(job) : error("not_found", "Not found.", 404); } },
  { method: "POST", pattern: /^\/api\/map-jobs\/([^/]+)\/(cancel|retry-failed)$/, handler: (_url, _request, match) => {
    const job = byId(state.mapJobs, match[1]!);
    const status = match[2] === "cancel" ? "cancelled" : "running";
    if (job) { const index = state.mapJobs.findIndex((item) => item.id === job.id); state.mapJobs[index] = { ...job, status }; }
    return response({ id: match[1], status });
  } },
  { method: "GET", pattern: /^\/api\/police\/workers$/, handler: () => response({ items: state.police.map((item) => ({ ...item, health: item.health ?? { status: "ok", conditions: [] } })), next_cursor: null }) },
  { method: "POST", pattern: /^\/api\/police\/workers\/([^/]+)\/disable$/, handler: (_url, _request, match) => {
    const worker = byId(state.police, match[1]!);
    if (worker) {
      const index = state.police.findIndex((item) => item.id === worker.id);
      state.police[index] = { ...worker, status: "disabled" };
    }
    return response({ id: match[1], status: "disabled" });
  } },
  { method: "GET", pattern: /^\/api\/audit\/search$/, handler: (url) => {
    let items = filtered(state.audits, url, ["action", "target_kind"]);
    const actor = url.searchParams.get("actor");
    if (actor !== null) items = items.filter((item) => item.actor_ref === actor || item.actor_kind === actor);
    const from = url.searchParams.get("from"); const to = url.searchParams.get("to");
    if (from !== null) items = items.filter((item) => item.at !== null && item.at >= from);
    if (to !== null) items = items.filter((item) => item.at !== null && item.at <= to);
    const result = page(items, url); return response({ items: result.items, total: items.length });
  } },
  { method: "GET", pattern: /^\/api\/audit\/verify$/, handler: () => response({ ok: true, first_broken_seq: null, checked: state.audits.length }) },
  { method: "GET", pattern: /^\/api\/search$/, handler: (url) => {
    const index = url.searchParams.get("index"); const items = state.search.filter((hit) => index === null || hit.index === index).filter((hit) => JSON.stringify(hit).toLowerCase().includes((url.searchParams.get("q") ?? "").toLowerCase())); return response({ items, total: items.length });
  } },
  { method: "POST", pattern: /^\/api\/chat$/, handler: () => response({ answer: `${state.issues.filter((issue) => issue.status === "unresolved").length} unresolved issues are in the masked fixture set.` }) },
  { method: "GET", pattern: /^\/api\/police\/v1\/openapi\.json$/, handler: () => response({ openapi: "3.1.0", info: { title: "Police API", version: "v1" } }) },
];

export async function routeMockRequest(path: string, init: RequestInit): Promise<Response> {
  const url = new URL(path, globalThis.location?.origin ?? "http://localhost");
  const request = new Request(url, init);
  const method = request.method.toUpperCase();
  const v13Response = await routeV13(url, request, method);
  if (v13Response !== null) return v13Response;
  for (const route of routes) {
    if (route.method !== method) continue;
    const match = route.pattern.exec(url.pathname);
    if (match) return route.handler(url, request, match);
  }
  return error("not_found", "Not found.", 404);
}
