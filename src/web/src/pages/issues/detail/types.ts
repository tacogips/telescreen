import type {
  Evidence,
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
