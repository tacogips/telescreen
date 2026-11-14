import type { AuditRecord, CollectorConfig, EscalationPolicy, Evidence, HumanTask, MapJob, MapJobSummary, PoliceWorker, RunDetail, RunSummary, SearchHit } from "../../types";

const LOAD_TIME = Date.now();
const ago = (minutes: number): string => new Date(LOAD_TIME - minutes * 60_000).toISOString();

export const runFixtures: RunSummary[] = [
  { id: "run-01", definition_id: "wf-01", issue_id: "iss-01", status: "running", trigger: "manual", started_at: ago(20), finished_at: null },
  { id: "run-02", definition_id: "wf-02", issue_id: "iss-02", status: "waiting", trigger: "issue_created", started_at: ago(80), finished_at: null },
  { id: "run-03", definition_id: "wf-01", issue_id: "iss-03", status: "succeeded", trigger: "manual", started_at: ago(240), finished_at: ago(220) },
  { id: "run-04", definition_id: "wf-02", issue_id: "iss-04", status: "failed", trigger: "schedule", started_at: ago(400), finished_at: ago(380) },
  { id: "run-05", definition_id: "wf-01", issue_id: null, status: "cancelled", trigger: "manual", started_at: ago(600), finished_at: ago(590) },
];

export const runDetailFixture: RunDetail = {
  run: runFixtures[1]!,
  steps: [
    { id: "step-01", step_id: "triage", visit: 1, branch_token: "main", status: "succeeded", attempt: 1, input_json: "{}", output_json: "{}", error_code: null },
    { id: "step-02", step_id: "remediate", visit: 2, branch_token: "main", status: "waiting", attempt: 1, input_json: "{}", output_json: null, error_code: null },
    { id: "step-03", step_id: "map-sweep", visit: 1, branch_token: "main", status: "running", attempt: 1, input_json: "{}", output_json: "{\"map_job_id\":\"map-01\"}", error_code: null },
  ],
  transitions: [
    { from_step_run_id: "step-01", to_step_id: "remediate", kind: "send_back", reason: "Additional remediation requested", at: ago(12) },
  ],
  human_tasks: [{ id: "task-01", step_run_id: "step-02", kind: "human", title: "Remove exposed value", assignee_ref: "team:engineering", status: "open", outcome: null, outcomes: ["completed", "send_back"] }],
};

export const taskFixtures: HumanTask[] = [{ id: "task-01", run_id: "run-02", step_run_id: "step-02", kind: "human", title: "Remove exposed value", assignee_ref: "team:engineering", due_at: ago(-120), status: "open", outcome: null }];
export const policeFixtures: PoliceWorker[] = [
  { id: "pol-01", name: "Local verifier", version: "1.0", mode: "pull", task_types: ["verify_remediation"], labels: ["private-network"], concurrency: 2, status: "active", last_heartbeat_at: ago(3) },
  { id: "pol-02", name: "Webhook verifier", version: "1.0", mode: "push", task_types: ["verify_remediation"], labels: ["github"], concurrency: 1, status: "active", last_heartbeat_at: ago(800) },
  { id: "pol-03", name: "Retired verifier", version: "1.0", mode: "pull", task_types: [], labels: [], concurrency: 1, status: "disabled", last_heartbeat_at: ago(3000) },
];
export const collectorFixtures: CollectorConfig[] = ["slack", "gdrive", "gdocs", "gsheets", "github", "custom", "police"].map((kind, index) => ({
  id: `col-0${index + 1}`, name: `${kind} collection`, kind, trigger_mode: index % 2 === 0 ? "both" : "schedule",
  cron: index % 2 === 0 ? "0 2 * * *" : null, sweep_cron: "0 3 * * 0", selector_json: "{}",
  credential_ref: "env:COLLECTOR_TOKEN", enabled: index !== 5,
}));
export const mapJobFixtures: MapJob[] = [{ id: "map-01", origin: "collector:col-01", status: "running", collector_config_id: "col-01", counts: { ready: 2, dispatched: 1, succeeded: 4, failed: 0, cancelled: 0 }, partitions_done: 4, partitions_total: 8, findings_so_far: 2, paused_until: null, failed_shards: [], result_json: null }];
export const mapJobSummaryFixtures: MapJobSummary[] = mapJobFixtures.map(({ id, origin, status, collector_config_id }) => ({ id, origin, status, collector_config_id, created_at: ago(9) }));
export const evidenceFixtures: Evidence[] = [{ id: "evi-01", issue_id: "iss-01", step_run_id: "step-02", display_filename: "remediation-notes.txt", content_type: "text/plain", size_bytes: 128, sha256: null, verdict: "accepted", rejection_rule_classes: [], uploaded_by: "member:alex", created_at: ago(15) }];
export const escalationFixtures: EscalationPolicy[] = [
  { id: "esc-01", name: "Default response", tiers_json: "{\"name\":\"Default response\",\"tiers\":[{\"after\":\"PT1H\",\"assign_to\":\"team:engineering\",\"notify\":[\"team:engineering\"],\"channels\":[\"email\"]}]}" },
  { id: "esc-02", name: "Security response", tiers_json: "{\"name\":\"Security response\",\"tiers\":[{\"after\":\"PT15M\",\"assign_to\":\"team:security\",\"notify\":[\"team:security\"],\"channels\":[\"slack\",\"email\"]}]}" },
];
export const auditFixtures: AuditRecord[] = Array.from({ length: 22 }, (_, index) => ({
  id: `aud-${String(index + 1).padStart(2, "0")}`, seq: index + 1, at: ago(index * 17), actor_kind: "member",
  actor_ref: "member:alex", action: index % 2 === 0 ? "issue.reviewed" : "workflow.started",
  target_kind: index % 2 === 0 ? "issue" : "workflow_run", target_id: index % 2 === 0 ? "iss-01" : "run-01",
  summary: "A masked record was updated",
}));
export const searchFixtures: SearchHit[] = ["issues", "findings", "runs", "audit"].map((index, item) => ({
  index, id: ["iss-01", "evt-01", "run-01", "aud-01"][item]!, title: "Masked finding review",
  snippet: item === 1 ? "ghp_****************[40]" : "Repository configuration reviewed", status: item === 0 ? "unresolved" : null, at: ago(item * 20),
}));
