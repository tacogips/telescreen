import { createMemo, For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import type { IssueStatus, RunHumanTask } from "../../../api/types";
import { StatusBadge } from "../../../components/StatusBadge";
import { Avatar } from "../../../components/Avatar";
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
    onCommentChange(value: string): void;
    onComment(event: SubmitEvent): void;
    onEditComment(id: string, body: string): void;
    onDeleteComment(id: string): void;
    onClose(): void;
    onExceptionDecision(action: "approve" | "reject"): void;
  }>,
): JSX.Element {
  const activeTask = (): RunHumanTask | undefined =>
    props.detail.activeRun?.human_tasks.find((task) => task.status === "open");
  const workflowName = (definitionId: string): string =>
    props.detail.workflows.find((workflow) => workflow.id === definitionId)
      ?.name ?? definitionId;
  const owner = (): Readonly<{ id: string; name: string }> | undefined => {
    const ref = props.detail.issue.owner.member_ref;
    if (ref === null) return undefined;
    const identity = ref.replace(/^member:/, "");
    const member = props.detail.members.find((entry) => entry.id === identity || entry.handle === identity);
    return member === undefined ? undefined : { id: member.id, name: member.display_name };
  };
  const mentionQuery = createMemo(() => /(?:^|\s)@([a-z0-9_.-]*)$/i.exec(props.comment)?.[1] ?? null);
  const suggestions = createMemo(() => {
    const query = mentionQuery();
    if (query === null) return [];
    const entries = [
      ...props.detail.members.filter((member) => member.active).map((member) => ({ handle: member.handle, name: member.display_name, ref: `member:${member.handle}` })),
      ...props.detail.suggestions.filter((item) => item.ref.startsWith("member:")).map((item) => ({ handle: item.ref.slice(7), name: item.display_name, ref: item.ref })),
    ];
    return [...new Map(entries.filter((entry) => entry.handle.toLowerCase().startsWith(query.toLowerCase())).map((entry) => [entry.handle, entry])).values()].slice(0, 6);
  });
  const insertMention = (handle: string): void => {
    props.onCommentChange(props.comment.replace(/(?:^|\s)@[a-z0-9_.-]*$/i, (match) => `${match.startsWith(" ") ? " " : ""}@${handle} `));
  };
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
        <Show when={props.detail.issue.exposure} keyed fallback={<p>Exposure unknown: the source reported no sharing data.</p>}>{(exposure) => <>
          <p><ExposureBadge visibility={exposure.visibility} externalPrincipalCount={exposure.external_principal_count} /></p>
          <p>External principals: {exposure.external_principal_count ?? 0}</p>
          <p>Shared drive: {exposure.shared_drive ? "Yes" : "No"}</p>
          <Show when={exposure.channel_kind}><p>Channel: {exposure.channel_kind}</p></Show>
          <Show when={exposure.repo_visibility}><p>Repository: {exposure.repo_visibility}</p></Show>
          <Show when={exposure.is_fork !== null && exposure.is_fork !== undefined}><p>Fork: {exposure.is_fork ? "Yes" : "No"}</p></Show>
        </>}</Show>
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
        <p>{owner() === undefined ? "Owner not mapped" : <>{Avatar({ name: owner()!.name, ref: owner()!.id, size: 20 })} Mapped to {owner()!.name}</>}</p>
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
        <Show when={props.detail.subscription.reason !== null}>
          <p>Following reason: {props.detail.subscription.reason}</p>
        </Show>
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
          <Show when={suggestions().length > 0}>
            <ul role="listbox" aria-label="Mention suggestions">
              <For each={suggestions()}>{(item) => <li><button type="button" role="option" onClick={() => insertMention(item.handle)}>{Avatar({ name: item.name, ref: item.ref, size: 20 })} {item.name} (@{item.handle})</button></li>}</For>
            </ul>
          </Show>
        </form>
        <Timeline>
          <For each={props.detail.activity}>
            {(item) => (
              <li>
                {Avatar({ name: item.actor.display_name, ref: item.actor.ref, size: 24 })}
                <strong>{item.actor.display_name}</strong>
                <span>{historyLabel(item.kind)}</span>
                <Show when={item.comment}>
                  {(comment) => <>
                    <Show when={comment().deleted} fallback={<p>{comment().body}</p>}><p>Comment deleted</p></Show>
                    <Show when={comment().edited_at}><small>Edited</small></Show>
                    <Show when={comment().can_edit && !comment().deleted}>
                      <Button size="xs" onClick={() => { const body = window.prompt("Edit comment", comment().body); if (body !== null) props.onEditComment(comment().id, body); }}>Edit</Button>
                      <Button size="xs" onClick={() => props.onDeleteComment(comment().id)}>Delete</Button>
                    </Show>
                  </>}
                </Show>
                <RelativeTime iso={item.at} />
              </li>
            )}
          </For>
        </Timeline>
        <Show when={props.detail.activity.length === 0}><p>No activity has been recorded.</p></Show>
      </section>
      <section>
        <h3>People</h3>
        <p>{props.detail.issue.assignee?.display_name ?? props.detail.members.find((member) => member.id === props.detail.issue.assignee_member_id)?.display_name ?? props.detail.teams.find((team) => team.id === props.detail.issue.assignee_team_id)?.name ?? "Unassigned"}</p>
      </section>
    </aside>
  );
}
