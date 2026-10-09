import type {
  Evidence,
  Issue,
  IssueEvent,
  IssueHistory,
  Member,
  RunDetail,
  RunSummary,
  Team,
  WorkflowVersionRef,
} from "../../../api/types";

export type DetailData = Readonly<{
  issue: Issue;
  events: IssueEvent[];
  history: IssueHistory[];
  runs: RunSummary[];
  evidence: Evidence[];
  members: Member[];
  teams: Team[];
  workflows: WorkflowVersionRef[];
  activeRun: RunDetail | null;
}>;
