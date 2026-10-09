import type {
  ApiErrorBody, ApiMessage, AuditRecord, AuditVerify, ChatResponse, CollectorConfig, Comment,
  Evidence, EscalationPolicy, EscalationTier, HumanTask, IngestResult, Issue, IssueEvent, IssueHistory,
  IssueStats, JsonValue, MapJob, Member, Page, PoliceClaim, PoliceCredentials, PoliceHeartbeat, PoliceHeartbeatResponse,
  PoliceManifest, PoliceResult, PoliceWorker, SearchResponse, Team, ValidateResponse, Workflow,
  WorkflowRun, WorkflowRunDetail, WorkflowSchema, WorkflowVersion,
} from "./types";

const TOKEN_KEY = "telescreen.token";

type ApiRequestInit = Omit<RequestInit, "body"> & Readonly<{ body?: BodyInit | null; json?: JsonValue }>;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly errorId: string | undefined;

  constructor(status: number, code: string, detail: string, errorId?: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.errorId = errorId;
  }
}

export function getToken(): string | null {
  return globalThis.sessionStorage?.getItem(TOKEN_KEY) ?? null;
}

export function setToken(token: string | null): void {
  if (token === null || token.trim() === "") {
    globalThis.sessionStorage?.removeItem(TOKEN_KEY);
  } else {
    globalThis.sessionStorage?.setItem(TOKEN_KEY, token);
  }
}

export async function apiFetch<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const { json: jsonBody, ...requestInit } = init;
  const headers = new Headers(requestInit.headers);
  const token = getToken();
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);
  let body = requestInit.body;
  if (jsonBody !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(jsonBody);
  }
  const response = await fetch(path, { ...requestInit, headers, body });
  if (!response.ok) {
    let parsed: ApiErrorBody | undefined;
    try {
      parsed = (await response.json()) as ApiErrorBody;
    } catch {
      parsed = undefined;
    }
    const error = parsed?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "request_failed",
      error?.detail ?? "The request could not be completed.",
      error?.error_id,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function query(values: URLSearchParams): string {
  const encoded = values.toString();
  return encoded.length === 0 ? "" : `?${encoded}`;
}

function json(method: string, value?: JsonValue): ApiRequestInit {
  return value === undefined ? { method } : { method, json: value };
}

export const healthApi = {
  get: (): Promise<Readonly<{ status: string }>> => apiFetch("/api/health"),
};

export const issuesApi = {
  list: (filters: URLSearchParams = new URLSearchParams()): Promise<Page<Issue>> => apiFetch(`/api/issues${query(filters)}`),
  get: (id: string): Promise<Issue> => apiFetch(`/api/issues/${encodeURIComponent(id)}`),
  events: (id: string, cursor?: string): Promise<Page<IssueEvent>> => {
    const params = new URLSearchParams();
    if (cursor !== undefined) params.set("cursor", cursor);
    return apiFetch(`/api/issues/${encodeURIComponent(id)}/events${query(params)}`);
  },
  history: (id: string, cursor?: string): Promise<Page<IssueHistory>> => {
    const params = new URLSearchParams();
    if (cursor !== undefined) params.set("cursor", cursor);
    return apiFetch(`/api/issues/${encodeURIComponent(id)}/history${query(params)}`);
  },
  stats: (id: string): Promise<IssueStats> => apiFetch(`/api/issues/${encodeURIComponent(id)}/stats`),
  update: (id: string, patch: Readonly<{ status?: string; assignee_ref?: string | null }>): Promise<Issue> => apiFetch(`/api/issues/${encodeURIComponent(id)}`, json("PATCH", patch)),
  comment: (id: string, body: string): Promise<Comment> => apiFetch(`/api/issues/${encodeURIComponent(id)}/comments`, json("POST", { body })),
};

export const workflowsApi = {
  list: (): Promise<Page<Workflow>> => apiFetch("/api/workflows"),
  get: (name: string): Promise<Workflow> => apiFetch(`/api/workflows/${encodeURIComponent(name)}`),
  version: (name: string, version: number): Promise<WorkflowVersion> => apiFetch(`/api/workflows/${encodeURIComponent(name)}/versions/${version}`),
  schema: (): Promise<WorkflowSchema> => apiFetch("/api/workflows/schema"),
  create: (source_format: "yaml" | "json", source_text: string): Promise<WorkflowVersion> => apiFetch("/api/workflows", json("POST", { source_format, source_text })),
  validate: (source_format: "yaml" | "json", source_text: string): Promise<ValidateResponse> => apiFetch("/api/workflows/validate", json("POST", { source_format, source_text })),
  activate: (name: string, version: number): Promise<Workflow> => apiFetch(`/api/workflows/${encodeURIComponent(name)}/activate`, json("POST", { version })),
  startRun: (name: string, issue_id?: string): Promise<WorkflowRun> => apiFetch(`/api/workflows/${encodeURIComponent(name)}/runs`, json("POST", issue_id === undefined ? {} : { issue_id })),
};

export const runsApi = {
  list: (filters: URLSearchParams = new URLSearchParams()): Promise<Page<WorkflowRun>> => apiFetch(`/api/runs${query(filters)}`),
  get: (id: string): Promise<WorkflowRunDetail> => apiFetch(`/api/runs/${encodeURIComponent(id)}`),
  cancel: (id: string): Promise<WorkflowRun> => apiFetch(`/api/runs/${encodeURIComponent(id)}/cancel`, json("POST")),
};

export const humanTasksApi = {
  list: (filters: URLSearchParams = new URLSearchParams()): Promise<Page<HumanTask>> => apiFetch(`/api/human-tasks${query(filters)}`),
  complete: (id: string, outcome: string, comment?: string): Promise<HumanTask> => apiFetch(`/api/human-tasks/${encodeURIComponent(id)}/complete`, json("POST", comment === undefined ? { outcome } : { outcome, comment })),
};

export const evidenceApi = {
  upload: (file: Blob, issueId: string, filename: string, stepRunId?: string): Promise<Evidence> => {
    const params = new URLSearchParams({ issue_id: issueId, filename });
    if (stepRunId !== undefined) params.set("step_run_id", stepRunId);
    return apiFetch(`/api/evidence${query(params)}`, { method: "PUT", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
  },
  list: (filters: URLSearchParams): Promise<Page<Evidence>> => apiFetch(`/api/evidence${query(filters)}`),
  get: (id: string): Promise<Evidence> => apiFetch(`/api/evidence/${encodeURIComponent(id)}`),
  content: (id: string): Promise<Blob> => fetchEvidenceBlob(`/api/evidence/${encodeURIComponent(id)}/content`),
};

async function fetchEvidenceBlob(path: string): Promise<Blob> {
  const headers = new Headers();
  const token = getToken();
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { headers });
  if (!response.ok) throw new ApiError(response.status, "request_failed", "The request could not be completed.");
  return response.blob();
}

export const escalationApi = {
  list: (): Promise<Page<EscalationPolicy>> => apiFetch("/api/escalation-policies"),
  create: (value: Readonly<{ name: string; tiers: EscalationTier[] }>): Promise<EscalationPolicy> => apiFetch("/api/escalation-policies", json("POST", value)),
  update: (name: string, value: Readonly<{ tiers: EscalationTier[] }>): Promise<EscalationPolicy> => apiFetch(`/api/escalation-policies/${encodeURIComponent(name)}`, json("PUT", value)),
};

export const directoryApi = {
  members: (): Promise<Page<Member>> => apiFetch("/api/members"),
  createMember: (value: Readonly<{ handle: string; display_name: string; email?: string }>): Promise<Member> => apiFetch("/api/members", json("POST", value)),
  teams: (): Promise<Page<Team>> => apiFetch("/api/teams"),
  createTeam: (value: Readonly<{ slug: string; name: string }>): Promise<Team> => apiFetch("/api/teams", json("POST", value)),
  setTeamMembers: (slug: string, member_ids: string[]): Promise<Team> => apiFetch(`/api/teams/${encodeURIComponent(slug)}/members`, json("PUT", { member_ids })),
};

export const collectorsApi = {
  list: (): Promise<Page<CollectorConfig>> => apiFetch("/api/collectors"),
  create: (value: Readonly<{ name: string; kind: string; trigger_mode: string; selector_json: string; cron?: string; credential_ref?: string }>): Promise<CollectorConfig> => apiFetch("/api/collectors", json("POST", value)),
  update: (id: string, value: Readonly<{ enabled?: boolean; trigger_mode?: string; selector_json?: string; cron?: string | null }>): Promise<CollectorConfig> => apiFetch(`/api/collectors/${encodeURIComponent(id)}`, json("PATCH", value)),
  run: (id: string): Promise<Readonly<{ scan_job_id: string }>> => apiFetch(`/api/collectors/${encodeURIComponent(id)}/run`, json("POST")),
  sweep: (id: string): Promise<MapJob> => apiFetch(`/api/collectors/${encodeURIComponent(id)}/sweep`, json("POST")),
  webhook: (kind: "slack" | "github" | "gdrive", payload: Blob): Promise<Readonly<{ accepted: boolean }>> => apiFetch(`/api/collectors/${kind}/webhook`, { method: "POST", headers: { "Content-Type": payload.type || "application/json" }, body: payload }),
};

export const mapJobsApi = {
  list: (filters: URLSearchParams = new URLSearchParams()): Promise<Page<MapJob>> => apiFetch(`/api/map-jobs${query(filters)}`),
  get: (id: string): Promise<MapJob> => apiFetch(`/api/map-jobs/${encodeURIComponent(id)}`),
  cancel: (id: string): Promise<MapJob> => apiFetch(`/api/map-jobs/${encodeURIComponent(id)}/cancel`, json("POST")),
  retryFailed: (id: string): Promise<MapJob> => apiFetch(`/api/map-jobs/${encodeURIComponent(id)}/retry-failed`, json("POST")),
};

export const policeApi = {
  workers: (): Promise<Page<PoliceWorker>> => apiFetch("/api/police/workers"),
  disable: (id: string): Promise<PoliceWorker> => apiFetch(`/api/police/workers/${encodeURIComponent(id)}/disable`, json("POST")),
  openapi: (): Promise<JsonValue> => apiFetch("/api/police/v1/openapi.json"),
  register: (value: PoliceManifest): Promise<PoliceCredentials> => apiFetch("/api/police/v1/workers/register", json("POST", value)),
  claim: (workerId: string, value: JsonValue): Promise<PoliceClaim> => apiFetch(`/api/police/v1/workers/${encodeURIComponent(workerId)}/claim`, json("POST", value)),
  heartbeat: (workerId: string, value: PoliceHeartbeat): Promise<PoliceHeartbeatResponse> => apiFetch(`/api/police/v1/workers/${encodeURIComponent(workerId)}/heartbeat`, json("POST", value)),
  result: (taskId: string, value: PoliceResult): Promise<Readonly<{ accepted: boolean }>> => apiFetch(`/api/police/v1/tasks/${encodeURIComponent(taskId)}/result`, json("POST", value)),
  callback: (taskId: string, value: PoliceResult): Promise<Readonly<{ accepted: boolean }>> => apiFetch(`/api/police/v1/callbacks/${encodeURIComponent(taskId)}`, json("POST", value)),
};

export const auditApi = {
  search: (filters: URLSearchParams = new URLSearchParams()): Promise<Page<AuditRecord>> => apiFetch(`/api/audit/search${query(filters)}`),
  verify: (): Promise<AuditVerify> => apiFetch("/api/audit/verify"),
};

export const searchApi = {
  search: (filters: URLSearchParams): Promise<SearchResponse> => apiFetch(`/api/search${query(filters)}`),
};

export const chatApi = {
  send: (messages: ApiMessage[]): Promise<ChatResponse> => apiFetch("/api/chat", json("POST", { messages })),
};

export const ingestApi = {
  scan: (value: JsonValue): Promise<IngestResult> => apiFetch("/api/ingest/v1/scan", json("POST", value)),
  findings: (value: JsonValue): Promise<IngestResult> => apiFetch("/api/ingest/v1/findings", json("POST", value)),
  sourceConfig: (name: string): Promise<JsonValue> => apiFetch(`/api/ingest/v1/sources/${encodeURIComponent(name)}/config`),
};
