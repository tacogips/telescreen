import type {
  ApiErrorBody, ApiMessage, AuditRecord, AuditSearchResponse, AuditVerify, ChatResponse, CollectorConfig,
  CoverageResource, CoverageSummary, Evidence, EscalationPolicy, ExceptionDuration, HumanTask, IngestFindingsResult, IngestScanResult, Issue, IssueDetail, IssueEvent, IssueException, IssueHistory,
  IssueStatsBuckets, JsonValue, MapJob, MapJobSummary, Member, MemberIdentity, Page, PoliceClaim, PoliceCredentials,
  PoliceHeartbeat, PoliceHeartbeatResponse, PoliceManifest, PoliceResult, PoliceWorker, SearchResponse,
  OccurrencesResponse, PolicySummary, PolicyValidation, PolicyVersion, RemediationReceipt, Team, ValidateResponse, WorkflowVersion, WorkflowVersionRef, RunDetail, RunSummary, RunHumanTask,
  AuthConfig, AuthMe, AuthSession, CreatedToken, RoleMatrix, TemplateDetail, TemplatePreview, TemplateSummary, TemplateValidation, TemplateValidationErrorItem, TemplateVersionText, TokenSummary, UserSummary,
} from "./types";
import type * as V13 from "./types";
import { clearSession, getSession, setSession } from "../auth/session";
import { refreshIfNeeded } from "../auth/oidc";

const TOKEN_KEY = "telescreen.token";
const MOCK = import.meta.env.VITE_TELESCREEN_MOCK === "1";

type ApiRequestInit = Omit<RequestInit, "body"> & Readonly<{ body?: BodyInit | null; json?: JsonValue }>;
type UnknownRecord = Readonly<Record<string, unknown>>;
type IssueListFilters = Readonly<{ exposure?: string; sort?: "last_seen" | "first_seen" | "times_seen" | "risk"; assignee?: "me" | "my_teams" | string; status?: string; source?: string; rule_class?: string; severity?: string; q?: string }>;
type QueryFilters = Readonly<{ state?: string; limit?: number; cursor?: string }>;

function params(filters: URLSearchParams | IssueListFilters | QueryFilters): URLSearchParams {
  if (filters instanceof URLSearchParams) return filters;
  return new URLSearchParams(Object.entries(filters).flatMap(([key, value]) => value === undefined ? [] : [[key, String(value)]]));
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly errorId: string | undefined;
  readonly ruleClasses: readonly string[] | undefined;

  constructor(status: number, code: string, detail: string, errorId?: string, ruleClasses?: readonly string[]) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.errorId = errorId;
    this.ruleClasses = ruleClasses;
  }
}

export class WorkflowValidationError extends ApiError {
  readonly errors: readonly Readonly<{ path: string; code: string }>[];

  constructor(errors: readonly Readonly<{ path: string; code: string }>[]) {
    super(422, "validation_error", "Workflow validation failed.");
    this.name = "WorkflowValidationError";
    this.errors = errors;
  }
}

export class TemplateValidationError extends ApiError {
  readonly errors: readonly TemplateValidationErrorItem[];
  constructor(errors: readonly TemplateValidationErrorItem[]) { super(422, "validation_error", "Template validation failed."); this.name = "TemplateValidationError"; this.errors = errors; }
}

export function getToken(): string | null {
  return getSession()?.access_token ?? null;
}

export function setToken(token: string | null): void {
  if (token === null || token.trim() === "") clearSession();
  else setSession({ kind: "token", access_token: token });
}

export async function rawFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (!MOCK && !path.startsWith("/api/auth/")) await refreshIfNeeded();
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);
  const request = { ...init, headers };
  if (MOCK) {
    const { mockFetch } = await import("./mock");
    return mockFetch(path, request);
  }
  const response = await fetch(path, request);
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    const returnTo = `${location.pathname}${location.search}`;
    clearSession();
    location.assign(`/login?returnTo=${encodeURIComponent(returnTo)}`);
  }
  return response;
}

export async function apiFetch<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const { json: jsonBody, ...requestInit } = init;
  const headers = new Headers(requestInit.headers);
  let body = requestInit.body;
  if (jsonBody !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(jsonBody);
  }
  const response = await rawFetch(path, { ...requestInit, headers, body });
  if (!response.ok) await throwApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function throwApiError(response: Response): Promise<never> {
  let body: unknown;
  try { body = await response.json(); } catch { body = undefined; }
  const error: UnknownRecord | undefined = record(record(body)?.error);
  if (response.status === 422 && Array.isArray(record(body)?.errors)) {
    const fieldErrors = (record(body)?.errors as unknown[]).flatMap((item): TemplateValidationErrorItem[] => {
      const value = record(item);
      return typeof value?.field === "string" && typeof value.code === "string" ? [{ field: value.field, code: value.code }] : [];
    });
    if (fieldErrors.length > 0) throw new TemplateValidationError(fieldErrors);
    const pathErrors = (record(body)?.errors as unknown[]).flatMap((item): Readonly<{ path: string; code: string }>[] => {
      const value = record(item); return typeof value?.path === "string" && typeof value.code === "string" ? [{ path: value.path, code: value.code }] : [];
    });
    throw new WorkflowValidationError(pathErrors);
  }
  const ruleClasses = Array.isArray(error?.rule_classes)
    ? error.rule_classes.filter((item): item is string => typeof item === "string") : undefined;
  throw new ApiError(
    response.status,
    typeof error?.code === "string" ? error.code : "request_failed",
    typeof error?.detail === "string" ? error.detail : "The request could not be completed.",
    typeof error?.error_id === "string" ? error.error_id : undefined,
    ruleClasses,
  );
}

function record(value: unknown): UnknownRecord | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as UnknownRecord : undefined;
}

function query(values: URLSearchParams): string {
  const encoded = values.toString();
  return encoded.length === 0 ? "" : `?${encoded}`;
}

function json(method: string, value?: JsonValue): ApiRequestInit {
  return value === undefined ? { method } : { method, json: value };
}

function normalizePage<T>(value: Readonly<{ items: T[]; next_cursor?: string | null }>): Page<T> {
  return { items: value.items, next_cursor: value.next_cursor ?? null };
}

export const healthApi = { get: (): Promise<Readonly<{ status: string }>> => apiFetch("/api/health") };

export const authApi = {
  config: (): Promise<AuthConfig> => apiFetch("/api/auth/config"),
  session: (): Promise<AuthSession> => apiFetch("/api/auth/session", { method: "POST" }),
  me: (): Promise<AuthMe> => apiFetch("/api/auth/me"),
};
export const templatesApi = {
  list: (): Promise<Readonly<{ items: TemplateSummary[] }>> => apiFetch("/api/notification-templates"),
  get: (name: string): Promise<TemplateDetail> => apiFetch(`/api/notification-templates/${encodeURIComponent(name)}`),
  version: (name: string, version: number): Promise<TemplateVersionText> => apiFetch(`/api/notification-templates/${encodeURIComponent(name)}/versions/${version}`),
  validate: (value: Readonly<{ event_kind: string; subject: string; body: string; email_subject?: string; email_body?: string; slack_body?: string }>): Promise<TemplateValidation> => apiFetch("/api/notification-templates/validate", json("POST", value)),
  preview: (value: Readonly<{ event_kind?: string; subject?: string; body?: string; email_subject?: string; email_body?: string; slack_body?: string; name?: string; version?: number }>): Promise<TemplatePreview> => apiFetch("/api/notification-templates/preview", json("POST", value)),
  create: (value: Readonly<{ name: string; event_kind: string; description?: string; subject: string; body: string; email_subject?: string; email_body?: string; slack_body?: string }>): Promise<Readonly<{ name: string; version: number }>> => apiFetch("/api/notification-templates", json("POST", value)),
  activate: (name: string, version: number): Promise<TemplateSummary> => apiFetch(`/api/notification-templates/${encodeURIComponent(name)}/activate`, json("POST", { version })),
};
export const usersApi = {
  list: (): Promise<Readonly<{ items: UserSummary[] }>> => apiFetch("/api/users"),
  roles: (): Promise<RoleMatrix> => apiFetch("/api/roles"),
  setRole: (handle: string, role: "admin" | "responder" | "viewer" | null): Promise<UserSummary> => apiFetch(`/api/users/${encodeURIComponent(handle)}/role`, json("PUT", { role })),
};
export const tokensApi = {
  list: (): Promise<Readonly<{ items: TokenSummary[] }>> => apiFetch("/api/tokens"),
  create: (value: Readonly<{ label: string; scopes: readonly string[]; member_handle?: string }>): Promise<CreatedToken> => apiFetch("/api/tokens", json("POST", { ...value, scopes: [...value.scopes] })),
  revoke: (id: string): Promise<void> => apiFetch(`/api/tokens/${encodeURIComponent(id)}/revoke`, json("POST")),
};

export const issuesApi = {
  list: (filters: URLSearchParams | IssueListFilters = new URLSearchParams()): Promise<Page<Issue>> => apiFetch(`/api/issues${query(params(filters))}`),
  get: (id: string): Promise<IssueDetail> => apiFetch(`/api/issues/${encodeURIComponent(id)}`),
  events: (id: string, cursor?: string): Promise<Page<IssueEvent>> => {
    const params = new URLSearchParams(); if (cursor !== undefined) params.set("cursor", cursor);
    return apiFetch(`/api/issues/${encodeURIComponent(id)}/events${query(params)}`);
  },
  history: (id: string, cursor?: string): Promise<Page<IssueHistory>> => {
    const params = new URLSearchParams(); if (cursor !== undefined) params.set("cursor", cursor);
    return apiFetch(`/api/issues/${encodeURIComponent(id)}/history${query(params)}`);
  },
  stats: (params: Readonly<{ bucket: "day" | "hour"; days: number }>): Promise<IssueStatsBuckets> => {
    const search = new URLSearchParams({ bucket: params.bucket, days: String(params.days) });
    return apiFetch(`/api/issues/stats${query(search)}`);
  },
  update: (id: string, patch: Readonly<{ status?: string; assignee_ref?: string | null }>): Promise<Issue> => apiFetch(`/api/issues/${encodeURIComponent(id)}`, json("PATCH", patch)),
  remediation: (id: string, value: Readonly<{ action: "remove" | "restrict"; include_linked: boolean; note?: string }>): Promise<RemediationReceipt> => apiFetch(`/api/issues/${encodeURIComponent(id)}/remediation`, json("POST", value)),
  resolve: (id: string, value: Readonly<{ reason: "false_positive" | "manual_unverified"; note: string }>): Promise<Issue> => apiFetch(`/api/issues/${encodeURIComponent(id)}/resolve`, json("POST", value)),
  occurrences: (id: string): Promise<OccurrencesResponse> => apiFetch(`/api/issues/${encodeURIComponent(id)}/occurrences`),
  requestException: (id: string, value: Readonly<{ reason: string; duration: ExceptionDuration }>): Promise<IssueException> => apiFetch(`/api/issues/${encodeURIComponent(id)}/exceptions`, json("POST", value)),
  comment: (id: string, body: string): Promise<Readonly<{ accepted: boolean }>> => apiFetch(`/api/issues/${encodeURIComponent(id)}/comments`, json("POST", { body })),
  listQuery: (filters: V13.StreamQuery): Promise<Page<Issue>> => apiFetch(`/api/issues${query(params(filters))}`),
  counts: (filters: Readonly<{ query?: string; period?: string; updated_since?: string }> = {}): Promise<V13.CountsResponse> => apiFetch(`/api/issues/counts${query(new URLSearchParams(Object.entries(filters).flatMap(([key, value]) => value === undefined ? [] : [[key, value]])))}`),
  archive: (id: string, value: Readonly<{ kind: string; duration_days?: number; count?: number }>): Promise<Issue> => apiFetch(`/api/issues/${encodeURIComponent(id)}/archive`, json("POST", value)),
  unarchive: (id: string): Promise<Issue> => apiFetch(`/api/issues/${encodeURIComponent(id)}/unarchive`, json("POST")),
  review: (id: string): Promise<Issue> => apiFetch(`/api/issues/${encodeURIComponent(id)}/review`, json("POST")),
  setPriority: (id: string, priority: V13.IssuePriority): Promise<Issue> => apiFetch(`/api/issues/${encodeURIComponent(id)}`, json("PATCH", { priority })),
  bulk: (value: Readonly<{ ids?: readonly string[]; query?: string; period?: string; action: string; params?: V13.BulkParams; status?: string }>): Promise<V13.BulkResult> => apiFetch("/api/issues/bulk", json("POST", { ...(value.ids === undefined ? {} : { ids: [...value.ids] }), ...(value.query === undefined ? {} : { query: value.query }), ...(value.period === undefined ? {} : { period: value.period }), action: value.action, params: { ...value.params, ...(value.status === undefined ? {} : { status: value.status }) } })),
  merge: (value: Readonly<{ primary_id: string; child_ids: readonly string[] }>): Promise<Issue> => apiFetch("/api/issues/merge", json("POST", { primary_id: value.primary_id, ids: [...value.child_ids] })),
  unmerge: (id: string, ids: readonly string[] = []): Promise<Issue> => apiFetch(`/api/issues/${encodeURIComponent(id)}/unmerge`, json("POST", { ids: [...ids] })),
  eventAt: (id: string, selector: "oldest" | "latest" | "recommended" | string): Promise<V13.EventSelection> => apiFetch(`/api/issues/${encodeURIComponent(id)}/events/${encodeURIComponent(selector)}`),
  tags: (id: string): Promise<V13.TagDistribution> => apiFetch(`/api/issues/${encodeURIComponent(id)}/tags`),
  issueStats: (id: string, period = "14d"): Promise<IssueStatsBuckets> => apiFetch(`/api/issues/${encodeURIComponent(id)}/stats${query(new URLSearchParams({ period }))}`),
  activity: (id: string): Promise<Readonly<{ items: V13.ActivityItem[] }>> => apiFetch(`/api/issues/${encodeURIComponent(id)}/activity`),
  editComment: (id: string, commentId: string, body: string): Promise<Readonly<{ id: string }>> => apiFetch(`/api/issues/${encodeURIComponent(id)}/comments/${encodeURIComponent(commentId)}`, json("PATCH", { body })),
  deleteComment: (id: string, commentId: string): Promise<void> => apiFetch(`/api/issues/${encodeURIComponent(id)}/comments/${encodeURIComponent(commentId)}`, json("DELETE")),
  setSubscription: (id: string, subscribed: boolean): Promise<Readonly<{ subscribed: boolean }>> => apiFetch(`/api/issues/${encodeURIComponent(id)}/subscription`, json("PUT", { subscribed })),
  bookmark: (id: string): Promise<void> => apiFetch(`/api/issues/${encodeURIComponent(id)}/bookmark`, json("PUT")),
  unbookmark: (id: string): Promise<void> => apiFetch(`/api/issues/${encodeURIComponent(id)}/bookmark`, json("DELETE")),
  externalIssues: (id: string): Promise<Readonly<{ items: V13.ExternalIssueLink[] }>> => apiFetch(`/api/issues/${encodeURIComponent(id)}/external-issues`),
  linkExternal: (id: string, url: string): Promise<V13.ExternalIssueLink> => apiFetch(`/api/issues/${encodeURIComponent(id)}/external-issues`, json("POST", { url })),
  unlinkExternal: (id: string, linkId: string): Promise<void> => apiFetch(`/api/issues/${encodeURIComponent(id)}/external-issues/${encodeURIComponent(linkId)}`, json("DELETE")),
  githubRepos: (): Promise<Readonly<{ items: string[] }>> => apiFetch("/api/external-issues/github/repos"),
  createGithubIssue: (id: string, repo: string, title: string): Promise<V13.ExternalIssueLink> => apiFetch(`/api/issues/${encodeURIComponent(id)}/external-issues/github`, json("POST", { repo, title })),
};

export const viewsApi = {
  list: (): Promise<Readonly<{ items: V13.SavedView[] }>> => apiFetch("/api/views"),
  create: (value: Readonly<{ name: string; query: string; sort: string; visibility: "personal" | "shared" }>): Promise<V13.SavedView> => apiFetch("/api/views", json("POST", value)),
  update: (id: string, value: Readonly<{ name?: string; query?: string; sort?: string }>): Promise<V13.SavedView> => apiFetch(`/api/views/${encodeURIComponent(id)}`, json("PATCH", value)),
  delete: (id: string): Promise<void> => apiFetch(`/api/views/${encodeURIComponent(id)}`, json("DELETE")),
  star: (id: string): Promise<void> => apiFetch(`/api/views/${encodeURIComponent(id)}/star`, json("PUT")),
  unstar: (id: string): Promise<void> => apiFetch(`/api/views/${encodeURIComponent(id)}/star`, json("DELETE")),
  setDefault: (id: string): Promise<void> => apiFetch("/api/views/default", json("PUT", { id })),
};
export const alertsApi = {
  list: (): Promise<Readonly<{ items: V13.AlertRule[] }>> => apiFetch("/api/alert-rules"),
  get: (id: string): Promise<V13.AlertRule> => apiFetch(`/api/alert-rules/${encodeURIComponent(id)}`),
  create: (value: Readonly<{ name: string; trigger: string; query: string; action_interval_minutes: number }>): Promise<V13.AlertRule> => apiFetch("/api/alert-rules", json("POST", value)),
  update: (id: string, value: Readonly<{ name?: string; query?: string; action_interval_minutes?: number }>): Promise<V13.AlertRule> => apiFetch(`/api/alert-rules/${encodeURIComponent(id)}`, json("PUT", value)),
  delete: (id: string): Promise<void> => apiFetch(`/api/alert-rules/${encodeURIComponent(id)}`, json("DELETE")),
  validate: (value: Readonly<{ trigger: string; query: string }>): Promise<Readonly<{ valid: boolean; errors: readonly string[] }>> => apiFetch("/api/alert-rules/validate", json("POST", value)),
  enable: (id: string): Promise<V13.AlertRule> => apiFetch(`/api/alert-rules/${encodeURIComponent(id)}/enable`, json("POST")),
  disable: (id: string): Promise<V13.AlertRule> => apiFetch(`/api/alert-rules/${encodeURIComponent(id)}/disable`, json("POST")),
  mute: (id: string, until: string): Promise<V13.AlertRule> => apiFetch(`/api/alert-rules/${encodeURIComponent(id)}/mute`, json("POST", { until })),
  unmute: (id: string): Promise<V13.AlertRule> => apiFetch(`/api/alert-rules/${encodeURIComponent(id)}/unmute`, json("POST")),
  history: (id: string): Promise<Readonly<{ items: V13.AlertFire[] }>> => apiFetch(`/api/alert-rules/${encodeURIComponent(id)}/history`),
};
export const meApi = {
  notificationSettings: (): Promise<Readonly<{ items: V13.NotificationSetting[] }>> => apiFetch("/api/auth/me/notification-settings"),
  updateNotificationSettings: (items: readonly V13.NotificationSetting[]): Promise<Readonly<{ items: V13.NotificationSetting[] }>> => apiFetch("/api/auth/me/notification-settings", json("PUT", { items: [...items] })),
  tokens: (): Promise<Readonly<{ items: V13.SelfToken[] }>> => apiFetch("/api/auth/me/tokens"),
  createToken: (value: Readonly<{ label: string; scopes: readonly string[] }>): Promise<Readonly<{ id: string; token: string }>> => apiFetch("/api/auth/me/tokens", json("POST", { ...value, scopes: [...value.scopes] })),
  revokeToken: (id: string): Promise<void> => apiFetch(`/api/auth/me/tokens/${encodeURIComponent(id)}/revoke`, json("POST")),
};
export const dashboardApi = { get: (period = "14d"): Promise<V13.DashboardResponse> => apiFetch(`/api/dashboard${query(new URLSearchParams({ period }))}`) };
export const integrationsApi = {
  list: (): Promise<Readonly<{ items: V13.IntegrationStatus[] }>> => apiFetch("/api/integrations"),
  test: (kind: string): Promise<Readonly<{ status: string; detail: string }>> => apiFetch(`/api/integrations/${encodeURIComponent(kind)}/test`, json("POST")),
};
export const membersApi = {
  update: (handle: string, value: Readonly<{ display_name?: string; email?: string | null }>): Promise<Member> => apiFetch(`/api/members/${encodeURIComponent(handle)}`, json("PATCH", value)),
  deactivate: (handle: string): Promise<void> => apiFetch(`/api/members/${encodeURIComponent(handle)}/deactivate`, json("POST")),
  reactivate: (handle: string): Promise<void> => apiFetch(`/api/members/${encodeURIComponent(handle)}/reactivate`, json("POST")),
};
export const ownershipApi = {
  list: (): Promise<Readonly<{ items: V13.OwnershipRule[] }>> => apiFetch("/api/ownership-rules"),
  create: (value: Readonly<{ source?: string; pattern: string; team_ref: string }>): Promise<V13.OwnershipRule> => apiFetch("/api/ownership-rules", json("POST", value)),
  update: (id: string, value: Readonly<{ source?: string | null; pattern?: string; team_ref?: string }>): Promise<V13.OwnershipRule> => apiFetch(`/api/ownership-rules/${encodeURIComponent(id)}`, json("PUT", value)),
  delete: (id: string): Promise<void> => apiFetch(`/api/ownership-rules/${encodeURIComponent(id)}`, json("DELETE")),
  reorder: (ids: readonly string[]): Promise<void> => apiFetch("/api/ownership-rules/reorder", json("POST", { ids: [...ids] })),
  test: (source: string, display_locator: string): Promise<Readonly<{ team_ref: string | null }>> => apiFetch("/api/ownership-rules/test", json("POST", { source, display_locator })),
};

export const exceptionsApi = {
  list: (filters: Readonly<{ status?: string; issue_id?: string; approver?: string }> = {}): Promise<Page<IssueException>> => apiFetch(`/api/exceptions${query(params(filters))}`),
  approve: (id: string, note?: string): Promise<IssueException> => apiFetch(`/api/exceptions/${encodeURIComponent(id)}/approve`, json("POST", note === undefined ? {} : { note })),
  reject: (id: string, note?: string): Promise<IssueException> => apiFetch(`/api/exceptions/${encodeURIComponent(id)}/reject`, json("POST", note === undefined ? {} : { note })),
  revoke: (id: string): Promise<IssueException> => apiFetch(`/api/exceptions/${encodeURIComponent(id)}/revoke`, json("POST")),
};

export const policiesApi = {
  list: (): Promise<Page<PolicySummary>> => apiFetch("/api/policies"),
  get: (name: string): Promise<Readonly<{ name: string; versions: PolicyVersion[] }>> => apiFetch(`/api/policies/${encodeURIComponent(name)}`),
  version: (name: string, version: number): Promise<PolicyVersion> => apiFetch(`/api/policies/${encodeURIComponent(name)}/versions/${version}`),
  validate: (body: Readonly<{ source_format: "yaml" | "json"; source_text: string }>): Promise<PolicyValidation> => apiFetch("/api/policies/validate", json("POST", body)),
  create: (body: Readonly<{ source_format: "yaml" | "json"; source_text: string }>): Promise<PolicyVersion> => apiFetch("/api/policies", json("POST", body)),
  activate: (name: string, version: number): Promise<PolicySummary> => apiFetch(`/api/policies/${encodeURIComponent(name)}/activate`, json("POST", { version })),
  disable: (name: string): Promise<PolicySummary> => apiFetch(`/api/policies/${encodeURIComponent(name)}/disable`, json("POST")),
};

export const workflowsApi = {
  list: async (): Promise<Page<WorkflowVersionRef>> => normalizePage(await apiFetch<{ items: WorkflowVersionRef[] }>("/api/workflows")),
  get: (name: string): Promise<Readonly<{ items: WorkflowVersionRef[] }>> => apiFetch(`/api/workflows/${encodeURIComponent(name)}`),
  version: (name: string, version: number): Promise<WorkflowVersion> => apiFetch(`/api/workflows/${encodeURIComponent(name)}/versions/${version}`),
  schema: async (): Promise<string> => (await rawFetch("/api/workflows/schema")).text(),
  create: (source_format: "yaml" | "json", source_text: string): Promise<WorkflowVersionRef> => apiFetch("/api/workflows", json("POST", { source_format, source_text })),
  validate: (source_format: "yaml" | "json", source_text: string): Promise<ValidateResponse> => apiFetch("/api/workflows/validate", json("POST", { source_format, source_text })),
  activate: (name: string, version: number): Promise<WorkflowVersionRef> => apiFetch(`/api/workflows/${encodeURIComponent(name)}/activate`, json("POST", { version })),
  startRun: (name: string, issue_id?: string): Promise<Readonly<{ run_id: string }>> => apiFetch(`/api/workflows/${encodeURIComponent(name)}/runs`, json("POST", issue_id === undefined ? {} : { issue_id })),
};

export const runsApi = {
  list: async (filters: URLSearchParams = new URLSearchParams()): Promise<Page<RunSummary>> => normalizePage(await apiFetch<{ items: RunSummary[] }>(`/api/runs${query(filters)}`)),
  get: (id: string): Promise<RunDetail> => apiFetch(`/api/runs/${encodeURIComponent(id)}`),
  cancel: (id: string): Promise<Readonly<{ run_id: string; status: string }>> => apiFetch(`/api/runs/${encodeURIComponent(id)}/cancel`, json("POST")),
};

export const humanTasksApi = {
  list: async (filters: URLSearchParams = new URLSearchParams()): Promise<Page<HumanTask>> => normalizePage(await apiFetch<{ items: HumanTask[] }>(`/api/human-tasks${query(filters)}`)),
  complete: (id: string, outcome: string, comment?: string, exception_duration?: ExceptionDuration): Promise<Readonly<{ id: string; status: string }>> => apiFetch(`/api/human-tasks/${encodeURIComponent(id)}/complete`, json("POST", { outcome, ...(comment === undefined ? {} : { comment }), ...(exception_duration === undefined ? {} : { exception_duration }) })),
};

export const evidenceApi = {
  upload: async (file: Blob, issueId: string, filename: string, stepRunId?: string): Promise<Evidence> => {
    const params = new URLSearchParams({ issue_id: issueId, filename });
    if (stepRunId !== undefined) params.set("step_run_id", stepRunId);
    const response = await rawFetch(`/api/evidence${query(params)}`, { method: "PUT", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
    if (!response.ok) await throwApiError(response);
    return await response.json() as Evidence;
  },
  list: async (filters: URLSearchParams): Promise<Page<Evidence>> => normalizePage(await apiFetch<{ items: Evidence[]; next_cursor?: string | null }>(`/api/evidence${query(filters)}`)),
  get: (id: string): Promise<Evidence> => apiFetch(`/api/evidence/${encodeURIComponent(id)}`),
  content: async (id: string): Promise<Blob> => {
    const response = await rawFetch(`/api/evidence/${encodeURIComponent(id)}/content`);
    if (!response.ok) await throwApiError(response);
    return response.blob();
  },
};

export const escalationApi = {
  list: async (): Promise<Page<EscalationPolicy>> => normalizePage(await apiFetch<{ items: EscalationPolicy[] }>("/api/escalation-policies")),
  create: (name: string, tiers_json: string, source_format: "json" | "yaml" = "json"): Promise<EscalationPolicy> => apiFetch("/api/escalation-policies", json("POST", { name, tiers_json, source_format })),
  update: (name: string, tiers_json: string, source_format: "json" | "yaml" = "json"): Promise<EscalationPolicy> => apiFetch(`/api/escalation-policies/${encodeURIComponent(name)}`, json("PUT", { tiers_json, source_format })),
};

export const directoryApi = {
  members: (filters: URLSearchParams = new URLSearchParams()): Promise<Page<Member>> => apiFetch(`/api/members${query(filters)}`),
  createMember: (value: Readonly<{ handle: string; display_name: string; email?: string; slack_user_id?: string }>): Promise<Member> => apiFetch("/api/members", json("POST", value)),
  teams: (filters: URLSearchParams = new URLSearchParams()): Promise<Page<Team>> => apiFetch(`/api/teams${query(filters)}`),
  createTeam: (value: Readonly<{ slug: string; name: string; email?: string; slack_channel?: string; lead?: string | null }>): Promise<Team> => apiFetch("/api/teams", json("POST", value)),
  identities: (handle: string): Promise<Page<MemberIdentity>> => apiFetch(`/api/members/${encodeURIComponent(handle)}/identities`),
  addIdentity: (handle: string, value: Readonly<{ kind: string; value: string }>): Promise<MemberIdentity> => apiFetch(`/api/members/${encodeURIComponent(handle)}/identities`, json("POST", value)),
  removeIdentity: (handle: string, id: string): Promise<void> => apiFetch(`/api/members/${encodeURIComponent(handle)}/identities/${encodeURIComponent(id)}`, json("DELETE")),
  setTeamLead: async (slug: string, lead: string | null): Promise<void> => { await apiFetch<void>(`/api/teams/${encodeURIComponent(slug)}/lead`, json("PUT", { lead })); },
  setTeamMembers: async (slug: string, handles: string[], lead?: string | null): Promise<void> => { await apiFetch<void>(`/api/teams/${encodeURIComponent(slug)}/members`, json("PUT", { handles, ...(lead === undefined ? {} : { lead }) })); },
};

type CollectorBody = Readonly<{ name: string; kind: string; trigger_mode: string; cron: string | null; sweep_cron: string | null; selector_json: string; credential_ref: string | null; enabled: boolean }>;
export const collectorsApi = {
  list: async (): Promise<Page<CollectorConfig>> => normalizePage(await apiFetch<{ items: CollectorConfig[] }>("/api/collectors")),
  create: (value: CollectorBody): Promise<Readonly<{ id: string }>> => apiFetch("/api/collectors", json("POST", value)),
  update: (id: string, value: CollectorBody): Promise<Readonly<{ id: string }>> => apiFetch(`/api/collectors/${encodeURIComponent(id)}`, json("PATCH", value)),
  run: (id: string): Promise<Readonly<{ id: string }>> => apiFetch(`/api/collectors/${encodeURIComponent(id)}/run`, json("POST")),
  sweep: (id: string): Promise<Readonly<{ map_job_id: string }>> => apiFetch(`/api/collectors/${encodeURIComponent(id)}/sweep`, json("POST")),
  coverage: (id: string): Promise<CoverageSummary> => apiFetch(`/api/collectors/${encodeURIComponent(id)}/coverage`),
  coverageResources: (id: string, filters: Readonly<{ state?: string; limit?: number; cursor?: string }> = {}): Promise<Page<CoverageResource>> => apiFetch(`/api/collectors/${encodeURIComponent(id)}/coverage/resources${query(params(filters))}`),
};

export const mapJobsApi = {
  list: async (filters: URLSearchParams = new URLSearchParams()): Promise<Page<MapJobSummary>> => normalizePage(await apiFetch<{ items: MapJobSummary[]; next_cursor?: string | null }>(`/api/map-jobs${query(filters)}`)),
  get: (id: string): Promise<MapJob> => apiFetch(`/api/map-jobs/${encodeURIComponent(id)}`),
  cancel: (id: string): Promise<Readonly<{ id: string; status: string }>> => apiFetch(`/api/map-jobs/${encodeURIComponent(id)}/cancel`, json("POST")),
  retryFailed: (id: string): Promise<Readonly<{ id: string; status: string }>> => apiFetch(`/api/map-jobs/${encodeURIComponent(id)}/retry-failed`, json("POST")),
};

export const policeApi = {
  workers: (): Promise<Page<PoliceWorker>> => apiFetch("/api/police/workers"),
  disable: (id: string): Promise<Readonly<{ id: string; status: "disabled" }>> => apiFetch(`/api/police/workers/${encodeURIComponent(id)}/disable`, json("POST")),
  openapi: (): Promise<JsonValue> => apiFetch("/api/police/v1/openapi.json"),
  register: (value: PoliceManifest): Promise<PoliceCredentials> => apiFetch("/api/police/v1/workers/register", json("POST", value)),
  claim: (workerId: string, value: JsonValue): Promise<PoliceClaim> => apiFetch(`/api/police/v1/workers/${encodeURIComponent(workerId)}/claim`, json("POST", value)),
  heartbeat: (workerId: string, value: PoliceHeartbeat): Promise<PoliceHeartbeatResponse> => apiFetch(`/api/police/v1/workers/${encodeURIComponent(workerId)}/heartbeat`, json("POST", value)),
  result: (taskId: string, value: PoliceResult): Promise<void> => apiFetch(`/api/police/v1/tasks/${encodeURIComponent(taskId)}/result`, json("POST", value)),
  callback: (taskId: string, value: PoliceResult): Promise<void> => apiFetch(`/api/police/v1/callbacks/${encodeURIComponent(taskId)}`, json("POST", value)),
};

export const auditApi = {
  search: (filters: URLSearchParams = new URLSearchParams()): Promise<AuditSearchResponse> => apiFetch(`/api/audit/search${query(filters)}`),
  verify: (): Promise<AuditVerify> => apiFetch("/api/audit/verify"),
};

export const searchApi = { search: (filters: URLSearchParams): Promise<SearchResponse> => apiFetch(`/api/search${query(filters)}`) };
export const chatApi = { send: (messages: ApiMessage[]): Promise<ChatResponse> => apiFetch("/api/chat", json("POST", { messages })) };
export const ingestApi = {
  scan: (value: JsonValue): Promise<IngestScanResult> => apiFetch("/api/ingest/v1/scan", json("POST", value)),
  findings: (value: JsonValue): Promise<IngestFindingsResult> => apiFetch("/api/ingest/v1/findings", json("POST", value)),
  sourceConfig: (name: string): Promise<JsonValue> => apiFetch(`/api/ingest/v1/sources/${encodeURIComponent(name)}/config`),
};
