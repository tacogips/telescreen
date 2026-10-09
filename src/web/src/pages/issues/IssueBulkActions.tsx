import { For, type JSX } from "solid-js";
import type {
  IssueStatus,
  Member,
  Team,
  WorkflowVersionRef,
} from "../../api/types";
import { Button, SplitButton } from "../../components/ui";

export function IssueBulkActions(
  props: Readonly<{
    selectedCount: number;
    statuses: readonly IssueStatus[];
    members: readonly Member[];
    teams: readonly Team[];
    workflows: readonly WorkflowVersionRef[];
    onStatus(status: IssueStatus): void;
    onAssign(value: string): void;
    onStartWorkflow(name: string): void;
    onClear(): void;
  }>,
): JSX.Element {
  const statusLabel = (status: IssueStatus): string => {
    if (status === "resolved") return "Resolve";
    if (status === "ignored") return "Ignore";
    if (status === "acknowledged") return "Acknowledge";
    if (status === "in_progress") return "Mark in progress";
    return "Reopen";
  };
  return (
    <div class="bulk-action-bar" role="toolbar" aria-label="Bulk issue actions">
      <span>{props.selectedCount} selected</span>
      <For each={props.statuses}>
        {(status) => (
          <Button size="xs" onClick={() => props.onStatus(status)}>
            {statusLabel(status)}
          </Button>
        )}
      </For>
      <label>
        Assign to
        <select
          aria-label="Assign to"
          value=""
          onChange={(event) => props.onAssign(event.currentTarget.value)}
        >
          <option value="">Choose member or team</option>
          <For each={props.members}>
            {(member) => (
              <option value={`member:${member.handle}`}>
                {member.display_name}
              </option>
            )}
          </For>
          <For each={props.teams}>
            {(team) => <option value={`team:${team.slug}`}>{team.name}</option>}
          </For>
        </select>
      </label>
      <label>
        Start workflow
        <select
          aria-label="Start workflow"
          value=""
          onChange={(event) => props.onStartWorkflow(event.currentTarget.value)}
        >
          <option value="">Choose workflow</option>
          <For each={props.workflows}>
            {(workflow) => (
              <option value={workflow.name}>{workflow.name}</option>
            )}
          </For>
        </select>
      </label>
      <SplitButton
        menuLabel="More bulk actions"
        menu={
          <div role="none">
            <For
              each={props.statuses.filter(
                (status) => status === "in_progress" || status === "unresolved",
              )}
            >
              {(status) => (
                <button
                  role="menuitem"
                  type="button"
                  onClick={() => props.onStatus(status)}
                >
                  {status === "unresolved" ? "Reopen" : "Mark in progress"}
                </button>
              )}
            </For>
          </div>
        }
      >
        More
      </SplitButton>
      <Button size="xs" onClick={props.onClear}>
        Clear selection
      </Button>
    </div>
  );
}
