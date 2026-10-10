import { For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import type { IssueStatus, RunHumanTask } from "../../../api/types";
import { StatusBadge } from "../../../components/StatusBadge";
import { RelativeTime } from "../../../components/RelativeTime";
import { Button, ExposureBadge, ProgressMarker, RiskPill, Timeline } from "../../../components/ui";
import { historyLabel, type DetailData } from "./types";

function progressFor(status: IssueStatus): 0 | 1 | 2 | 3 | 4 {
  if (status === "acknowledged") return 1;
  if (status === "in_progress") return 2;
  if (status === "resolved") return 4;
  if (status === "pending_verification") return 3;
  return 0;
}

export function IssueDetailSidebar(
  props: Readonly<{
    detail: DetailData;
    currentStep: string;
    comment: string;
    comments: readonly string[];
    onCommentChange(value: string): void;
    onComment(event: SubmitEvent): void;
    onClose(): void;
    onExceptionDecision(action: "approve" | "reject"): void;
  }>,
): JSX.Element {
  const activeTask = (): RunHumanTask | undefined =>
    props.detail.activeRun?.human_tasks.find((task) => task.status === "open");
  const workflowName = (definitionId: string): string =>
    props.detail.workflows.find((workflow) => workflow.id === definitionId)
      ?.name ?? definitionId;
  return (
    <aside id="runs" class="issue-detail-sidebar">
      <Button size="xs" onClick={props.onClose}>
        Close sidebar
      </Button>
      <section>
        <h3>First / last seen</h3>
        <p>
          First <RelativeTime iso={props.detail.issue.first_seen} />
        </p>
        <p>
          Last <RelativeTime iso={props.detail.issue.last_seen} />
        </p>
        <p>Coverage: {props.detail.issue.sweep_covered ? "Scanned" : "Not covered"}</p>
        <p>Last checked <RelativeTime iso={props.detail.issue.last_checked_at} /></p>
      </section>
      <section>
        <h3>Exposure</h3>
        <p><ExposureBadge visibility={props.detail.issue.exposure.visibility} externalPrincipalCount={props.detail.issue.exposure.external_principal_count} /></p>
        <p>External principals: {props.detail.issue.exposure.external_principal_count}</p>
        <p>Shared drive: {props.detail.issue.exposure.shared_drive ? "Yes" : "No"}</p>
        <Show when={props.detail.issue.exposure.channel_kind}><p>Channel: {props.detail.issue.exposure.channel_kind}</p></Show>
        <Show when={props.detail.issue.exposure.repo_visibility}><p>Repository: {props.detail.issue.exposure.repo_visibility}</p></Show>
        <Show when={props.detail.issue.exposure.is_fork !== null}><p>Fork: {props.detail.issue.exposure.is_fork ? "Yes" : "No"}</p></Show>
        <Show when={props.detail.issue.exposure_changed_at}><p>Changed <RelativeTime iso={props.detail.issue.exposure_changed_at ?? ""} /></p></Show>
      </section>
      <section>
        <h3>Risk</h3>
        <p>{props.detail.issue.risk_score === undefined ? "Risk unavailable" : <RiskPill score={props.detail.issue.risk_score} />}</p>
        <p>Effective severity: {props.detail.issue.severity}</p>
        <Show when={props.detail.issue.base_severity && props.detail.issue.base_severity !== props.detail.issue.severity}>
          <p>Base severity: {props.detail.issue.base_severity}</p>
        </Show>
        <p>Asset tier: {props.detail.issue.asset_tier ?? "Unknown"}</p>
        <p>Age: <RelativeTime iso={props.detail.issue.first_seen} /></p>
      </section>
      <section>
        <h3>Owner</h3>
        <p>{props.detail.issue.owner.present ? `Mapped to ${props.detail.issue.owner.member_ref ?? "member"}` : "Owner not mapped"}</p>
      </section>
      <section>
        <h3>Policy</h3>
        <p>{props.detail.issue.policy_name ? `${props.detail.issue.policy_name} v${props.detail.issue.policy_version ?? "?"}` : "No matching policy"}</p>
        <StatusBadge status={props.detail.issue.status} resolution={props.detail.issue.resolution} />
      </section>
      <Show when={props.detail.issue.active_exception}>
        {(exception) => (
          <section>
            <h3>Exception</h3>
            <p>Status: {exception().status}</p>
            <p>Duration: {exception().duration}</p>
            <p>{exception().expires_at ? `Expires ${exception().expires_at}` : "No expiry"}</p>
            <Show when={exception().status === "requested"}>
              <Button size="xs" onClick={() => props.onExceptionDecision("approve")}>Approve</Button>
              <Button size="xs" onClick={() => props.onExceptionDecision("reject")}>Reject</Button>
            </Show>
          </section>
        )}
      </Show>
      <section>
        <h3>Workflow runs</h3>
        <Show when={props.detail.issue.status !== "allowed"}>
          <ProgressMarker step={progressFor(props.detail.issue.status)} />
        </Show>
        <ul>
          <For each={props.detail.runs}>
            {(run) => (
              <li>
                <A href={`/runs/${encodeURIComponent(run.id)}`}>
                  {workflowName(run.definition_id)} · {run.status}
                </A>
              </li>
            )}
          </For>
        </ul>
        <Show when={props.detail.activeRun}>
          <p>Current step: {props.currentStep}</p>
        </Show>
      </section>
      <section>
        <h3>Escalation</h3>
        <Show when={activeTask()} fallback={<p>No open human task.</p>}>
          {(task) => (
            <p>
              {task().title} · {task().assignee_ref} · {task().status}
            </p>
          )}
        </Show>
      </section>
      <section>
        <h3>Activity</h3>
        <form onSubmit={props.onComment}>
          <label>
            Comment
            <textarea
              aria-label="Add a comment"
              placeholder="Add a comment…"
              value={props.comment}
              onInput={(event) =>
                props.onCommentChange(event.currentTarget.value)
              }
            />
          </label>
          <Button type="submit" size="sm">
            Add a comment
          </Button>
        </form>
        <Timeline>
          <For each={props.detail.history}>
            {(item) => (
              <li>
                <strong>{historyLabel(item.kind)}</strong>
                <RelativeTime iso={item.at} />
              </li>
            )}
          </For>
          <For each={props.comments}>
            {(item) => (
              <li>
                <strong>Comment posted</strong>
                <p>{item}</p>
              </li>
            )}
          </For>
        </Timeline>
      </section>
      <section>
        <h3>People</h3>
        <p>
          {props.detail.members.find(
            (member) => member.id === props.detail.issue.assignee_member_id,
          )?.display_name ??
            props.detail.teams.find(
              (team) => team.id === props.detail.issue.assignee_team_id,
            )?.name ??
            "Unassigned"}
        </p>
      </section>
    </aside>
  );
}
