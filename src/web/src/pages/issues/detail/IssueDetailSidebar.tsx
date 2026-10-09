import { For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import type { IssueStatus, RunHumanTask } from "../../../api/types";
import { RelativeTime } from "../../../components/RelativeTime";
import { Button, ProgressMarker, Timeline } from "../../../components/ui";
import type { DetailData } from "./types";

function progressFor(status: IssueStatus): 0 | 1 | 2 | 3 | 4 {
  if (status === "acknowledged") return 1;
  if (status === "in_progress") return 2;
  if (status === "resolved") return 4;
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
      </section>
      <section>
        <h3>Workflow runs</h3>
        <ProgressMarker step={progressFor(props.detail.issue.status)} />
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
                <strong>{item.kind.replaceAll("_", " ")}</strong>
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
