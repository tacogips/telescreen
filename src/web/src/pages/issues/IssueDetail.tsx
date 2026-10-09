import { createResource, createSignal, For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import { ApiError, evidenceApi, issuesApi, runsApi } from "../../api/client";
import type { Evidence, Issue, IssueEvent, IssueHistory, WorkflowRun } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { StatusBadge } from "../../components/StatusBadge";
import { EvidenceRejectedError, uploadEvidence } from "./uploadEvidence";

const ALLOWED_TRANSITIONS: Readonly<Record<Issue["status"], readonly Issue["status"][]>> = {
  unresolved: ["acknowledged", "in_progress", "resolved", "ignored"],
  acknowledged: ["in_progress", "resolved", "ignored", "unresolved"],
  in_progress: ["resolved", "ignored", "unresolved"],
  regressed: ["acknowledged", "in_progress", "resolved", "ignored"],
  resolved: ["unresolved"],
  ignored: ["unresolved"],
};

type DetailData = Readonly<{ issue: Issue; events: IssueEvent[]; history: IssueHistory[]; runs: WorkflowRun[]; evidence: Evidence[] }>;

export function IssueDetail(props: Readonly<{ id: string }>): JSX.Element {
  const [data, { refetch }] = createResource(() => props.id, async (id): Promise<DetailData> => {
    const [issue, events, history, runs, evidence] = await Promise.all([
      issuesApi.get(id), issuesApi.events(id), issuesApi.history(id),
      runsApi.list(new URLSearchParams({ issue_id: id })),
      evidenceApi.list(new URLSearchParams({ issue_id: id })),
    ]);
    return { issue, events: events.items, history: history.items, runs: runs.items, evidence: evidence.items };
  });
  const [error, setError] = createSignal<ApiError | null>(null);
  const [assignee, setAssignee] = createSignal("");
  const [comment, setComment] = createSignal("");
  const [uploadMessage, setUploadMessage] = createSignal("");

  const run = async (action: () => Promise<unknown>): Promise<void> => {
    setError(null);
    setUploadMessage("");
    try { await action(); await refetch(); }
    catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "request_failed", "The request could not be completed.")); }
  };
  const submitAssignee = (event: SubmitEvent): void => {
    event.preventDefault();
    const issue = data()?.issue;
    if (issue !== undefined) void run(() => issuesApi.update(issue.id, { assignee_ref: assignee().trim() || null }));
  };
  const submitComment = (event: SubmitEvent): void => {
    event.preventDefault();
    const issue = data()?.issue;
    const body = comment().trim();
    if (issue !== undefined && body) void run(async () => { await issuesApi.comment(issue.id, body); setComment(""); });
  };
  const upload = (event: Event & { currentTarget: HTMLInputElement }): void => {
    const file = event.currentTarget.files?.[0];
    const issue = data()?.issue;
    if (file === undefined || issue === undefined) return;
    void run(async () => {
      try { await uploadEvidence(file, issue.id, file.name); setUploadMessage("Evidence uploaded."); }
      catch (caught) {
        if (caught instanceof EvidenceRejectedError) {
          const rejection = caught.ruleClasses.length > 0 ? caught.ruleClasses.join(", ") : caught.detail;
          setUploadMessage(`Evidence rejected: ${rejection}`);
          return;
        }
        throw caught;
      }
    });
  };

  return <section class="page-content">
    <Show when={data()} fallback={<p>{data.loading ? "Loading issue…" : "Issue unavailable."}</p>}>
      {(detail) => <>
        <A href="/issues">Back to issues</A>
        <div class="page-heading"><div><p class="eyebrow">{detail().issue.short_id} · {detail().issue.rule_class}</p><h1>{detail().issue.display_title}</h1><p>{detail().issue.display_locator}</p></div><StatusBadge status={detail().issue.status} /></div>
        <Show when={error()} keyed>{(value) => <ErrorBanner error={value} />}</Show>
        <Show when={uploadMessage()}><p role="status">{uploadMessage()}</p></Show>
        <div class="summary-grid"><div><strong>Severity</strong><span>{detail().issue.severity}</span></div><div><strong>First seen</strong><RelativeTime iso={detail().issue.first_seen} /></div><div><strong>Last seen</strong><RelativeTime iso={detail().issue.last_seen} /></div><div><strong>Events</strong><span>{detail().issue.times_seen}</span></div></div>
        <section><h2>Status</h2><div class="button-row"><For each={ALLOWED_TRANSITIONS[detail().issue.status]}>{(status) => <button class="button-secondary" type="button" onClick={() => void run(() => issuesApi.update(detail().issue.id, { status }))}>{status === "unresolved" ? "Reopen" : status.replaceAll("_", " ")}</button>}</For></div></section>
        <section><h2>Assignment</h2><form class="inline-form" onSubmit={submitAssignee}><label>Assignee reference<input aria-label="Assignee reference" value={assignee()} onInput={(event) => setAssignee(event.currentTarget.value)} placeholder={detail().issue.assignee_member_id ?? detail().issue.assignee_team_id ?? "Unassigned"} /></label><button class="button-secondary" type="submit">Save assignee</button></form></section>
        <section><h2>Events</h2><For each={detail().events}>{(event) => <article class="event-card"><p>{event.masked_snippet}</p><dl><dt>Location</dt><dd>{event.location}</dd><dt>Rule</dt><dd>{detail().issue.rule_class}</dd><dt>Detector</dt><dd>{event.detector}</dd><dt>Confidence</dt><dd>{event.confidence ?? "Unknown"}</dd><dt>Observed</dt><dd><RelativeTime iso={event.at} /></dd></dl></article>}</For><Show when={detail().events.length === 0}><p>No events recorded.</p></Show></section>
        <section><h2>History</h2><ol class="timeline"><For each={detail().history}>{(item) => <li><RelativeTime iso={item.at} /> <strong>{item.action}</strong><span>{item.summary}</span></li>}</For></ol></section>
        <section><h2>Workflow runs</h2><ul><For each={detail().runs}>{(item) => <li><A href={`/runs/${encodeURIComponent(item.id)}`}>{item.workflow} · {item.current_step ?? "No current step"} · {item.status}</A></li>}</For></ul></section>
        <section><h2>Evidence</h2><ul><For each={detail().evidence}>{(item) => <li>{item.display_filename} · {item.verdict} · {item.size_bytes} bytes</li>}</For></ul><label>Upload evidence<input aria-label="Upload evidence" type="file" onChange={upload} /></label></section>
        <section><h2>Comments</h2><form onSubmit={submitComment}><label>Comment<textarea aria-label="Comment" value={comment()} onInput={(event) => setComment(event.currentTarget.value)} /></label><button class="button-primary" type="submit">Add comment</button></form></section>
      </>}
    </Show>
  </section>;
}
