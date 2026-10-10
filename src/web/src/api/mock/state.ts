import type { CollectorConfig, Issue, IssueException, MapJob, MemberIdentity, PolicySummary, PolicyVersion, PoliceWorker, WorkflowVersion, WorkflowVersionRef } from "../types";
import { collectorFixtures, mapJobFixtures, policeFixtures } from "./fixtures/operations";
import { memberFixtures, teamFixtures } from "./fixtures/directory";
import { auditFixtures, escalationFixtures, evidenceFixtures, runFixtures, searchFixtures, taskFixtures, runDetailFixture } from "./fixtures/operations";
import { coverageResourceFixtures, coverageSummaryFixture, exceptionFixtures, occurrenceFixtures, policyFixtures, policyVersionFixtures, v11Issues } from "./fixtures/v11";

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
  runDetail: structuredClone(runDetailFixture),
  workflows: [
    { id: "wf-01", name: "Credential remediation", version: 1, active: true, source_format: "yaml", source_text: "name: Credential remediation", compiled_json: "{}" },
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
};

export function workflowRefs(): WorkflowVersionRef[] {
  return state.workflows.map(({ id, name, version, active }) => ({ id, name, version, active }));
}
