import { For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import type { IssueStatus } from "../../../api/types";
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
  unresolved: ["acknowledged", "in_progress", "resolved", "ignored"],
  acknowledged: ["in_progress", "resolved", "ignored", "unresolved"],
  in_progress: ["resolved", "ignored", "unresolved"],
  regressed: ["acknowledged", "in_progress", "resolved", "ignored"],
  resolved: ["unresolved"],
  ignored: ["unresolved"],
};

function severity(value: string): "high" | "medium" | "low" {
  if (value === "high" || value === "medium") return value;
  return "low";
}

export function IssueDetailHeader(
  props: Readonly<{
    detail: DetailData;
    assignee: string;
    onStatus(status: IssueStatus): void;
    onAssignee(value: string): void;
    onStartWorkflow(name: string): void;
    onCopyMarkdown(): void;
  }>,
): JSX.Element {
  const issue = (): DetailData["issue"] => props.detail.issue;
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
            <StatusBadge status={issue().status} />
            <span>{issue().rule_class}</span>
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
        <Show
          when={issue().resolution === "removal"}
          fallback={
            <Show
              when={ALLOWED_TRANSITIONS[issue().status].includes("resolved")}
            >
              <SplitButton
                onClick={() => props.onStatus("resolved")}
                menuLabel="Resolve options"
                menu={
                  <div role="none">
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => props.onStatus("resolved")}
                    >
                      Mark resolved
                    </button>
                  </div>
                }
              >
                Resolve
              </SplitButton>
            </Show>
          }
        >
          <Tag variant="success">Resolved by removal</Tag>
        </Show>
        <For
          each={ALLOWED_TRANSITIONS[issue().status].filter(
            (status) => status !== "resolved" && status !== "unresolved",
          )}
        >
          {(status) => (
            <Button size="sm" onClick={() => props.onStatus(status)}>
              {status === "ignored"
                ? "Ignore"
                : status === "acknowledged"
                  ? "Acknowledge"
                  : "In progress"}
            </Button>
          )}
        </For>
        <Show
          when={issue().status === "resolved" || issue().status === "ignored"}
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
      <Show
        when={issue().status === "resolved" || issue().status === "ignored"}
      >
        <div class="issue-status-banner">Issue is {issue().status}.</div>
      </Show>
    </>
  );
}
