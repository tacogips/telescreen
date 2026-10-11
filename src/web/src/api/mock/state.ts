import type { CollectorConfig, Issue, IssueException, MapJob, MemberIdentity, PolicySummary, PolicyVersion, PoliceWorker, TokenSummary, UserSummary, WorkflowVersion, WorkflowVersionRef } from "../types";
import { collectorFixtures, mapJobFixtures, policeFixtures } from "./fixtures/operations";
import { memberFixtures, teamFixtures } from "./fixtures/directory";
import { auditFixtures, escalationFixtures, evidenceFixtures, runFixtures, searchFixtures, taskFixtures, runDetailFixture } from "./fixtures/operations";
import { coverageResourceFixtures, coverageSummaryFixture, exceptionFixtures, occurrenceFixtures, policyFixtures, policyVersionFixtures, v11Issues } from "./fixtures/v11";
import { mockRoleMatrix, mockTemplates, mockTokens, mockUsers } from "./fixtures/v12";

export const state = {
  issues: structuredClone(v11Issues) as Issue[],
  exceptions: structuredClone(exceptionFixtures) as IssueException[],
  policies: structuredClone(policyFixtures) as PolicySummary[],
  policyVersions: structuredClone(policyVersionFixtures) as PolicyVersion[],
  identities: [] as MemberIdentity[],
  occurrences: structuredClone(occurrenceFixtures),
  coverageSummary: structuredClone(coverageSummaryFixture),
  coverageResources: structuredClone(coverageResourceFixtures),
  collectors: structuredClone(collectorFixtures) as CollectorConfig[],
  mapJobs: structuredClone(mapJobFixtures) as MapJob[],
  police: structuredClone(policeFixtures) as PoliceWorker[],
  members: structuredClone(memberFixtures),
  teams: structuredClone(teamFixtures),
  audits: structuredClone(auditFixtures),
  escalations: structuredClone(escalationFixtures),
  evidence: structuredClone(evidenceFixtures),
  runs: structuredClone(runFixtures),
  search: structuredClone(searchFixtures),
  tasks: structuredClone(taskFixtures),
  comments: [] as Readonly<{ issue_id: string; display_text: string }>[] ,
  runDetail: { ...structuredClone(runDetailFixture), steps: [...structuredClone(runDetailFixture.steps), { id: "step-notify", step_id: "notify-owner", visit: 1, branch_token: "main", status: "succeeded", attempt: 1, input_json: "{}", output_json: "{\"targets\":[{\"index\":0,\"channel\":\"email\",\"queued\":1,\"blocked\":0,\"skipped\":0}]}", error_code: null }] },
  workflows: [
    { id: "wf-01", name: "Credential remediation", version: 1, active: true, source_format: "yaml", source_text: "apiVersion: telescreen.workflow/v2\nname: Credential remediation\nnotification_sets:\n  - name: owner-alert\n    targets:\n      - recipients: [team:security]\n        channels: [slack, email]\n  - name: workflow-review\n    targets:\n      - recipients: [member:alex]\n        channels: [email]\n", compiled_json: "{\"apiVersion\":\"telescreen.workflow/v2\",\"notification_sets\":[{\"name\":\"owner-alert\",\"targets\":[{\"recipients\":[\"team:security\"],\"channels\":[\"slack\",\"email\"]}]},{\"name\":\"workflow-review\",\"targets\":[{\"recipients\":[\"member:alex\"],\"channels\":[\"email\"]}]}]}" },
    { id: "wf-02", name: "Sensitive data response", version: 2, active: true, source_format: "json", source_text: "{}", compiled_json: "{}" },
  ] as WorkflowVersion[],
  nextIssueNumber: 7,
  nextEvidenceNumber: 2,
  nextCollectorNumber: 8,
  nextWorkflowNumber: 3,
  nextRunNumber: 6,
  nextExceptionNumber: 2,
  nextPolicyVersion: 2,
  isMockAdmin: true,
  templates: structuredClone(mockTemplates),
  users: structuredClone(mockUsers) as UserSummary[],
  roleMatrix: structuredClone(mockRoleMatrix),
  tokens: structuredClone(mockTokens) as TokenSummary[],
  nextTokenNumber: 2,
};

export function workflowRefs(): WorkflowVersionRef[] {
  return state.workflows.map(({ id, name, version, active }) => ({ id, name, version, active }));
}
