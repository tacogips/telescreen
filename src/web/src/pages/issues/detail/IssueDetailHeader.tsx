import { createSignal, For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import type { ExceptionDuration, IssueStatus } from "../../../api/types";
import { StatusBadge } from "../../../components/StatusBadge";
import { TopBar } from "../../../components/shell/TopBar";
import {
  Button,
  PriorityBadge,
  Redacted,
  Select,
  SeverityBar,
  SplitButton,
  Tag,
} from "../../../components/ui";
import type { DetailData } from "./types";

const ALLOWED_TRANSITIONS: Readonly<
  Record<IssueStatus, readonly IssueStatus[]>
> = {
  unresolved: ["acknowledged", "in_progress"],
  acknowledged: ["in_progress", "unresolved"],
  in_progress: ["unresolved"],
  regressed: ["acknowledged", "in_progress"],
  resolved: ["unresolved"],
  ignored: ["unresolved"],
  allowed: ["unresolved"],
  pending_verification: ["unresolved"],
  escalating: ["acknowledged", "in_progress", "unresolved"],
  archived: ["unresolved"],
};

function severity(value: string): "critical" | "high" | "medium" {
  if (value === "critical" || value === "high") return value;
  return "medium";
}

export function IssueDetailHeader(
  props: Readonly<{
    detail: DetailData;
    assignee: string;
    onStatus(status: IssueStatus): void;
    onRemediation(action: "remove" | "restrict", includeLinked: boolean): void;
    onResolve(reason: "false_positive" | "manual_unverified", note: string): void;
    onRequestException(reason: string, duration: ExceptionDuration): void;
    onAssignee(value: string): void;
    onStartWorkflow(name: string): void;
    onCopyMarkdown(): void;
  }>,
): JSX.Element {
  const issue = (): DetailData["issue"] => props.detail.issue;
  const [dialog, setDialog] = createSignal<"remediation" | "false_positive" | "manual_unverified" | "exception" | null>(null);
  const [note, setNote] = createSignal("");
  const [action, setAction] = createSignal<"remove" | "restrict">("remove");
  const [includeLinked, setIncludeLinked] = createSignal(false);
  const [duration, setDuration] = createSignal<ExceptionDuration>("30d");
  const credential = (): boolean => ["credential", "token", "private_key", "password"].includes(issue().rule_class);
  const exceptionMessage = (): string => {
    const exception = issue().active_exception;
    if (exception === null) return "No expiry (legacy)";
    if (exception.status === "requested")
      return `Exception requested, waiting for ${exception.approver_ref ?? "approver"}`;
    if (exception.expires_at !== null)
      return `Ignored until ${exception.expires_at}, approved by ${exception.approver_ref ?? "approver"}`;
    return "No expiry (legacy)";
  };
  const submitDialog = (event: SubmitEvent): void => {
    event.preventDefault();
    const reason = note().trim();
    if (dialog() !== "remediation" && reason === "") return;
    if (dialog() === "remediation") props.onRemediation(action(), includeLinked());
    if (dialog() === "false_positive") props.onResolve("false_positive", reason);
    if (dialog() === "manual_unverified") props.onResolve("manual_unverified", reason);
    if (dialog() === "exception") props.onRequestException(reason, duration());
    setDialog(null);
    setNote("");
  };
  return (
    <>
      <TopBar.Slot name="title">
        <A href="/issues">Issues</A>
        <span aria-hidden="true"> / </span>
        <span>
          {issue().source} · {issue().short_id}
        </span>
      </TopBar.Slot>
      <div class="issue-detail-header">
        <div class="issue-detail-heading">
          <div class="issue-detail-title">
            <SeverityBar severity={severity(issue().severity)} />
            <h2 title={issue().display_title}>{issue().display_title}</h2>
          </div>
          <p>
            <StatusBadge status={issue().status} resolution={issue().resolution} />
            <span>{issue().rule_class}</span>
            <Show when={issue().resolution !== null}>
              <span>Resolution: {issue().resolution?.replaceAll("_", " ")}</span>
            </Show>
            <Redacted
              text={issue().display_locator}
              ruleClass={issue().rule_class}
            />
          </p>
          <div class="issue-detail-badges">
            <Tag variant="muted">{props.detail.evidence.length} evidence</Tag>
            <Tag variant="muted">
              {
                props.detail.runs.filter(
                  (run) => run.status === "running" || run.status === "waiting",
                ).length
              }{" "}
              active runs
            </Tag>
          </div>
        </div>
        <div class="issue-detail-metrics">
          <div>
            <span>Findings</span>
            <strong>{issue().times_seen}</strong>
          </div>
          <div>
            <span>Resources</span>
            <strong>
              {new Set(props.detail.events.map((event) => event.location)).size}
            </strong>
          </div>
        </div>
      </div>
      <div class="issue-action-bar" role="toolbar" aria-label="Issue actions">
        <Show when={issue().resolution === "removal"}>
          <Tag variant="success">Resolved by removal</Tag>
        </Show>
        <Show when={issue().status !== "resolved" && issue().status !== "ignored"}>
          <SplitButton
            onClick={() => setDialog("remediation")}
            menuLabel="Resolve options"
            menu={<div role="none">
              <button type="button" role="menuitem" onClick={() => setDialog("false_positive")}>Resolve as false positive...</button>
              <button type="button" role="menuitem" onClick={() => setDialog("manual_unverified")}>Resolve without verification...</button>
            </div>}
          >
            Mark remediated...
          </SplitButton>
          <Button size="sm" onClick={() => setDialog("exception")}>Request exception...</Button>
        </Show>
        <For
          each={ALLOWED_TRANSITIONS[issue().status].filter(
            (status) => status !== "unresolved",
          )}
        >
          {(status) => (
            <Button size="sm" onClick={() => props.onStatus(status)}>
              {status === "acknowledged"
                  ? "Acknowledge"
                  : "In progress"}
            </Button>
          )}
        </For>
        <Show
          when={ALLOWED_TRANSITIONS[issue().status].includes("unresolved")}
        >
          <Button size="sm" onClick={() => props.onStatus("unresolved")}>
            Reopen
          </Button>
        </Show>
        <label>
          Start workflow
          <Select
            aria-label="Start workflow"
            value=""
            onChange={(event) =>
              props.onStartWorkflow(event.currentTarget.value)
            }
          >
            <option value="">Start workflow</option>
            <For
              each={props.detail.workflows.filter(
                (workflow) => workflow.active,
              )}
            >
              {(workflow) => (
                <option value={workflow.name}>{workflow.name}</option>
              )}
            </For>
          </Select>
        </label>
        <SplitButton
          menuLabel="Issue options"
          menu={
            <div role="none">
              <button
                type="button"
                role="menuitem"
                onClick={props.onCopyMarkdown}
              >
                Copy issue as Markdown
              </button>
            </div>
          }
        >
          More
        </SplitButton>
        <span class="issue-action-spacer" />
        <PriorityBadge level={severity(issue().severity)} />
        <label>
          Assignee
          <Select
            aria-label="Assignee"
            value={props.assignee}
            onChange={(event) => props.onAssignee(event.currentTarget.value)}
          >
            <option value="">
              {issue().assignee_member_id ??
                issue().assignee_team_id ??
                "Unassigned"}
            </option>
            <For each={props.detail.members}>
              {(member) => (
                <option value={`member:${member.handle}`}>
                  {member.display_name}
                </option>
              )}
            </For>
            <For each={props.detail.teams}>
              {(team) => (
                <option value={`team:${team.slug}`}>{team.name}</option>
              )}
            </For>
          </Select>
        </label>
      </div>
      <Show when={issue().status === "pending_verification"}>
        <div class="issue-status-banner info">Verifying: waiting for a rescan of the resource</div>
      </Show>
      <Show when={issue().status === "allowed"}>
        <div class="issue-status-banner">Allowed by policy {issue().policy_name ?? "unknown"} v{issue().policy_version ?? "?"}</div>
      </Show>
      <Show when={issue().status === "ignored"}>
        <div class="issue-status-banner">{exceptionMessage()}</div>
      </Show>
      <Show when={issue().status === "resolved"}>
        <div class="issue-status-banner">{issue().resolution === "manual_unverified" ? "Resolved (unverified)" : "Issue is resolved."}</div>
      </Show>
      <Show when={dialog()}>
        <form class="issue-action-dialog" role="dialog" aria-modal="true" onSubmit={submitDialog}>
          <h3>{dialog() === "remediation" ? "Mark remediated" : dialog() === "exception" ? "Request exception" : "Resolve issue"}</h3>
          <Show when={dialog() === "remediation"}>
            <label>Action
              <Select aria-label="Remediation action" value={action()} onChange={(event) => setAction(event.currentTarget.value as "remove" | "restrict")}>
                <option value="remove">Removed</option><option value="restrict">Restricted</option>
              </Select>
            </label>
            <Show when={credential()}>
              <label><input type="checkbox" checked={includeLinked()} onChange={(event) => setIncludeLinked(event.currentTarget.checked)} /> Include linked occurrences</label>
            </Show>
          </Show>
          <Show when={dialog() === "exception"}>
            <label>Duration
              <Select aria-label="Exception duration" value={duration()} onChange={(event) => setDuration(event.currentTarget.value as ExceptionDuration)}>
                <option value="7d">7 days</option><option value="30d">30 days</option><option value="90d">90 days</option><option value="until_regressed">Until regressed</option><option value="forever">Forever (admin)</option>
              </Select>
            </label>
          </Show>
          <Show when={dialog() !== "remediation"}>
            <label>Reason<textarea aria-label="Reason" value={note()} onInput={(event) => setNote(event.currentTarget.value)} required /></label>
          </Show>
          <Button type="submit" size="sm" disabled={dialog() !== "remediation" && note().trim() === ""}>Submit</Button>
          <Button type="button" size="sm" onClick={() => setDialog(null)}>Cancel</Button>
        </form>
      </Show>
    </>
  );
}
