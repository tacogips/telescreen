import { A } from "@solidjs/router";
import { createResource, createSignal, For, Show } from "solid-js";
import { ApiError, exceptionsApi, humanTasksApi, issuesApi } from "../../api/client";
import type { ExceptionDuration, HumanTask, Issue, IssueException } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { TopBar } from "../../components/shell/TopBar";
import { EmptyState, Panel, PanelItem, Placeholder, RiskPill, showToast } from "../../components/ui";
import { StatusBadge } from "../../components/StatusBadge";

const durations: readonly ExceptionDuration[] = ["7d", "30d", "90d", "until_regressed"];
type TaskData = Readonly<{ tasks: HumanTask[]; approvals: IssueException[]; issues: Issue[] }>;

function failure(value: unknown): ApiError {
  return value instanceof ApiError ? value : new ApiError(0, "request_failed", "The request could not be completed.");
}

export default function TasksPage() {
  const [data, { refetch }] = createResource(async (): Promise<TaskData> => {
    const taskFilters = new URLSearchParams({ assignee: "me", status: "open" });
    const issueFilters = new URLSearchParams({ assignee: "me", sort: "risk" });
    const [tasks, approvals, issues] = await Promise.all([
      humanTasksApi.list(taskFilters), exceptionsApi.list({ approver: "me", status: "requested" }), issuesApi.list(issueFilters),
    ]);
    return { tasks: tasks.items, approvals: approvals.items, issues: issues.items };
  });
  const [error, setError] = createSignal<ApiError | null>(null);
  const [comments, setComments] = createSignal<Readonly<Record<string, string>>>({});
  const [durationsByTask, setDurationsByTask] = createSignal<Readonly<Record<string, ExceptionDuration>>>({});
  const [notes, setNotes] = createSignal<Readonly<Record<string, string>>>({});
  const ownerTasks = (): HumanTask[] => (data()?.tasks ?? []).filter((task) => task.kind === "owner_review");
  const otherTasks = (): HumanTask[] => (data()?.tasks ?? []).filter((task) => task.kind !== "owner_review");
  const visibleIssues = (): Issue[] => (data()?.issues ?? []).filter((issue) => ["unresolved", "acknowledged", "in_progress", "regressed", "pending_verification"].includes(issue.status));
  const verifyIssues = (): Issue[] => visibleIssues().filter((issue) => issue.status === "pending_verification");
  const assignedIssues = (): Issue[] => visibleIssues().filter((issue) => issue.status !== "pending_verification");

  async function complete(task: HumanTask, outcome: "remove" | "restrict" | "exception" | "not_mine"): Promise<void> {
    const comment = (comments()[task.id] ?? "").trim();
    if (outcome === "exception" && comment.length === 0) {
      setError(new ApiError(0, "comment_required", "Add a comment before requesting an exception."));
      return;
    }
    setError(null);
    try {
      await humanTasksApi.complete(task.id, outcome, comment || undefined, outcome === "exception" ? durationsByTask()[task.id] ?? "7d" : undefined);
      await refetch(); showToast("Task completed", "success");
    } catch (cause) { setError(failure(cause)); }
  }

  async function decide(exception: IssueException, action: "approve" | "reject"): Promise<void> {
    setError(null);
    try {
      if (action === "approve") await exceptionsApi.approve(exception.id, notes()[exception.id]?.trim() || undefined);
      else await exceptionsApi.reject(exception.id, notes()[exception.id]?.trim() || undefined);
      await refetch(); showToast(`Exception ${action}d`, "success");
    } catch (cause) { setError(failure(cause)); }
  }

  return <section class="page-content">
    <TopBar.Slot name="title">My tasks</TopBar.Slot>
    <Show when={error() ?? (data.error ? failure(data.error) : null)}>{(value) => <ErrorBanner error={value()} />}</Show>
    <Show when={!data.loading} fallback={<Placeholder label="Loading tasks…" />}>
      <Show when={(ownerTasks().length + otherTasks().length + (data()?.approvals.length ?? 0) + visibleIssues().length) > 0} fallback={<EmptyState title="Nothing needs your attention" />}>
        <Panel title="Owner reviews"><Show when={ownerTasks().length > 0} fallback={<p>No owner reviews are waiting.</p>}><For each={ownerTasks()}>{(task) => <PanelItem><div><strong>{task.title}</strong><p class="muted">Assigned to {task.assignee_ref}</p><label class="field">Comment<textarea rows="2" value={comments()[task.id] ?? ""} onInput={(event) => setComments({ ...comments(), [task.id]: event.currentTarget.value })} /></label><Show when={(comments()[task.id] ?? "").trim().length === 0}><small>Exception requires a comment.</small></Show><label class="field">Exception duration<select aria-label={`Exception duration for ${task.title}`} value={durationsByTask()[task.id] ?? "7d"} onChange={(event) => setDurationsByTask({ ...durationsByTask(), [task.id]: event.currentTarget.value as ExceptionDuration })}>{durations.map((duration) => <option value={duration}>{duration === "until_regressed" ? "Until regressed" : duration}</option>)}</select></label></div><div class="row-actions"><button class="button-secondary" onClick={() => void complete(task, "remove")}>Remove</button><button class="button-secondary" onClick={() => void complete(task, "restrict")}>Restrict</button><button class="button-secondary" onClick={() => void complete(task, "exception")}>Exception</button><button class="button-secondary" onClick={() => void complete(task, "not_mine")}>Not mine</button></div></PanelItem>}</For></Show></Panel>
        <Panel title="Other tasks"><Show when={otherTasks().length > 0} fallback={<p>No other tasks are waiting.</p>}><For each={otherTasks()}>{(task) => <PanelItem><div><strong>{task.title}</strong><p>{task.assignee_ref}</p></div><A href={`/runs/${encodeURIComponent(task.run_id)}`}>Open run</A></PanelItem>}</For></Show></Panel>
        <Panel title="Approvals"><Show when={(data()?.approvals.length ?? 0) > 0} fallback={<p>No exception approvals are waiting.</p>}><For each={data()?.approvals}>{(exception) => <PanelItem><div><strong>Issue {exception.issue_id}</strong><p>{exception.reason} · {exception.duration}</p><label class="field">Note<input value={notes()[exception.id] ?? ""} onInput={(event) => setNotes({ ...notes(), [exception.id]: event.currentTarget.value })} /></label></div><div class="row-actions"><button class="button-primary" onClick={() => void decide(exception, "approve")}>Approve</button><button class="button-secondary" onClick={() => void decide(exception, "reject")}>Reject</button></div></PanelItem>}</For></Show></Panel>
        <Show when={verifyIssues().length > 0}><Panel title="Verifying"><For each={verifyIssues()}>{(issue) => <PanelItem><A href={`/issues/${encodeURIComponent(issue.id)}`}>{issue.short_id} · {issue.display_title}</A><RiskPill score={issue.risk_score ?? 0} /><StatusBadge status={issue.status} /></PanelItem>}</For></Panel></Show>
        <Panel title="Assigned issues"><Show when={assignedIssues().length > 0} fallback={<p>No assigned issues need action.</p>}><For each={assignedIssues()}>{(issue) => <PanelItem><A href={`/issues/${encodeURIComponent(issue.id)}`}>{issue.short_id} · {issue.display_title}</A><RiskPill score={issue.risk_score ?? 0} /><StatusBadge status={issue.status} /></PanelItem>}</For></Show></Panel>
      </Show>
    </Show>
  </section>;
}
