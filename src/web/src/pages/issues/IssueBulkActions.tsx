import { createSignal, For, Show, type JSX } from "solid-js";
import type { Issue, IssuePriority, IssueStatus, Member, Team, WorkflowVersionRef } from "../../api/types";
import { Button } from "../../components/ui";
import { ArchiveMenu, type ArchiveRequest } from "./ArchiveMenu";

export function IssueBulkActions(props: Readonly<{
  selectedCount: number;
  matchingCount: number;
  allMatching: boolean;
  query: string;
  period: string;
  issues: readonly Issue[];
  statuses: readonly IssueStatus[];
  members: readonly Member[];
  teams: readonly Team[];
  workflows: readonly WorkflowVersionRef[];
  onStatus(status: IssueStatus): void;
  onResolve(reason: "false_positive" | "manual_unverified", note: string): void;
  onArchive(request: ArchiveRequest): void;
  onAssign(value: string): void;
  onPriority(value: IssuePriority): void;
  onReview(): void;
  onMerge(primaryId: string): void;
  onSelectAllMatching(): void;
  onRemediate(): void;
  onStartWorkflow(name: string): void;
  onClear(): void;
}>): JSX.Element {
  const [resolveOpen, setResolveOpen] = createSignal(false);
  const [resolveReason, setResolveReason] = createSignal<"false_positive" | "manual_unverified">("manual_unverified");
  const [resolveNote, setResolveNote] = createSignal("");
  const [primaryId, setPrimaryId] = createSignal(props.issues[0]?.id ?? "");
  const statusLabel = (status: IssueStatus): string => status === "acknowledged" ? "Acknowledge" : status === "in_progress" ? "Mark in progress" : "Reopen";
  return <div class="bulk-action-bar" role="toolbar" aria-label="Bulk issue actions">
    <span>{props.allMatching ? `${props.matchingCount} matching issues selected` : `${props.selectedCount} selected`}</span>
    <Show when={!props.allMatching && props.selectedCount > 0}><Button size="xs" onClick={props.onSelectAllMatching}>Select all {props.matchingCount} matching</Button></Show>
    <For each={props.statuses}>{(status) => <Button size="xs" onClick={() => props.onStatus(status)}>{statusLabel(status)}</Button>}</For>
    <Button size="xs" aria-label="Resolve selected issues" onClick={() => setResolveOpen(true)}>Resolve</Button>
    <Show when={resolveOpen()}><div class="resolve-dialog" role="dialog" aria-label="Resolve selected issues"><label>Reason<select aria-label="Resolve reason" value={resolveReason()} onChange={(event) => setResolveReason(event.currentTarget.value as "false_positive" | "manual_unverified")}><option value="manual_unverified">Manually resolved</option><option value="false_positive">False positive</option></select></label><label>Note<textarea aria-label="Resolution note" value={resolveNote()} onInput={(event) => setResolveNote(event.currentTarget.value)} /></label><Button size="xs" onClick={() => { props.onResolve(resolveReason(), resolveNote()); setResolveOpen(false); }}>Confirm resolve</Button><Button size="xs" onClick={() => setResolveOpen(false)}>Cancel</Button></div></Show>
    <ArchiveMenu onArchive={props.onArchive} />
    <label>Assign to<select aria-label="Assign to" value="" onChange={(event) => props.onAssign(event.currentTarget.value)}><option value="">Choose member or team</option><option value="__unassigned__">Unassigned</option><For each={props.members}>{(member) => <option value={`member:${member.handle}`}>{member.display_name}</option>}</For><For each={props.teams}>{(team) => <option value={`team:${team.slug}`}>{team.name}</option>}</For></select></label>
    <label>Priority<select aria-label="Set priority" value="" onChange={(event) => props.onPriority(event.currentTarget.value as IssuePriority)}><option value="">Set priority</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
    <Button size="xs" onClick={props.onReview}>Mark reviewed</Button>
    <label>Merge into<select aria-label="Merge primary issue" value={primaryId()} onChange={(event) => setPrimaryId(event.currentTarget.value)}><For each={props.issues}>{(issue) => <option value={issue.id}>{issue.short_id}</option>}</For></select></label>
    <Button size="xs" onClick={() => props.onMerge(primaryId())}>Merge selected</Button>
    <Button size="xs" onClick={props.onRemediate}>Mark remediated</Button>
    <label>Start workflow<select aria-label="Start workflow" value="" onChange={(event) => props.onStartWorkflow(event.currentTarget.value)}><option value="">Choose workflow</option><For each={props.workflows}>{(workflow) => <option value={workflow.name}>{workflow.name}</option>}</For></select></label>
    <Button size="xs" onClick={props.onClear}>Clear selection</Button>
    <span class="bulk-scope" data-query={props.query} data-period={props.period} />
  </div>;
}
