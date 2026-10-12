import type {
  Evidence,
  ActivityItem,
  ExternalIssueLink,
  IssueDetail,
  IssueEvent,
  IssueHistory,
  OccurrencesResponse,
  Member,
  RunDetail,
  RunSummary,
  Team,
  WorkflowVersionRef,
} from "../../../api/types";

export type DetailStatsBucket = Readonly<{ start: string; count: number }>;
export type DetailStats = Readonly<{ buckets: readonly DetailStatsBucket[] }>;
export type DetailTagItem = Readonly<{
  key: string;
  value: string;
  count: number;
  percent: number;
}>;
export type DetailTags = Readonly<{
  total: number;
  items: readonly DetailTagItem[];
}>;

export type DetailData = Readonly<{
  issue: IssueDetail;
  occurrences: OccurrencesResponse;
  events: IssueEvent[];
  history: IssueHistory[];
  runs: RunSummary[];
  evidence: Evidence[];
  members: Member[];
  teams: Team[];
  workflows: WorkflowVersionRef[];
  activeRun: RunDetail | null;
  selection: Readonly<{ event: IssueEvent; previous_id: string | null; next_id: string | null; position: number; total: number }> | null;
  stats: DetailStats;
  tags: DetailTags;
  activity: readonly DetailActivity[];
  externalIssues: readonly ExternalIssueLink[];
  githubRepos: readonly string[];
  subscription: Readonly<{ subscribed: boolean; reason: string | null }>;
  mergedInto: Readonly<{ id: string; short_id: string }> | null;
  mergedChildren: readonly Readonly<{ id: string; short_id: string; source: string; display_locator: string; status: string }>[];
  suggestions: readonly Readonly<{ ref: string; display_name: string; reason: string }>[];
}>;

export type DetailActivity = Readonly<{
  id: string;
  kind: string;
  actor: ActivityItem["actor"];
  body: string | null;
  at: string;
  comment: Readonly<{ id: string; body: string; edited_at: string | null; deleted: boolean; can_edit: boolean }> | null;
}>;

const HISTORY_LABELS: Readonly<Record<string, string>> = {
  exposure_changed: "Exposure changed",
  policy_reevaluated: "Policy re-evaluated",
  verification_requested: "Verification requested",
  verification_failed: "Verification failed",
  verification_inconclusive: "Verification inconclusive",
  exception_requested: "Exception requested",
  exception_approved: "Exception approved",
  exception_rejected: "Exception rejected",
  exception_revoked: "Exception revoked",
  exception_expired: "Exception expired",
};

export function historyLabel(kind: string): string {
  return HISTORY_LABELS[kind] ?? kind.replaceAll("_", " ");
}
