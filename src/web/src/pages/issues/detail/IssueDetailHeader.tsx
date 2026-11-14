import { createSignal, For, Show, type JSX } from "solid-js";
import { A } from "@solidjs/router";
import type { ExceptionDuration, IssuePriority, IssueStatus } from "../../../api/types";
import { Avatar } from "../../../components/Avatar";
import { StatusBadge } from "../../../components/StatusBadge";
import { TopBar } from "../../../components/shell/TopBar";
import { ArchiveMenu, type ArchiveRequest } from "../ArchiveMenu";
import {
  Button,
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
    onPriority(value: IssuePriority): void;
    onArchive(request: ArchiveRequest): void;
    onUnarchive(): void;
    onBookmark(bookmarked: boolean): void;
    onSubscribe(subscribed: boolean): void;
    onUnmerge(): void;
    onUnmergeChild(primaryId: string): void;
    onMerge(childId: string): void;
    onLinkExternal(url: string, title: string): void;
    onCreateExternal(repo: string, title: string): void;
    onDeleteExternal(linkId: string): void;
    onUpload(event: Event & { currentTarget: HTMLInputElement }): void;
  }>,
): JSX.Element {
  const issue = (): DetailData["issue"] => props.detail.issue;
  const [dialog, setDialog] = createSignal<"remediation" | "false_positive" | "manual_unverified" | "exception" | "merge" | "link" | "github" | null>(null);
  const [note, setNote] = createSignal("");
  const [action, setAction] = createSignal<"remove" | "restrict">("remove");
  const [includeLinked, setIncludeLinked] = createSignal(false);
  const [duration, setDuration] = createSignal<ExceptionDuration>("30d");
  const [moreOpen, setMoreOpen] = createSignal(false);
  const [externalUrl, setExternalUrl] = createSignal("");
  const [externalTitle, setExternalTitle] = createSignal("");
  const [githubRepo, setGithubRepo] = createSignal("");
  const [githubTitle, setGithubTitle] = createSignal(issue().display_title);
  const [mergeId, setMergeId] = createSignal("");
  const [permalinkCopied, setPermalinkCopied] = createSignal(false);
  const currentAssignee = (): string => {
    const display = issue().assignee;
    if (display !== undefined && display !== null) return display.display_name;
    const member = props.detail.members.find((entry) => entry.id === issue().assignee_member_id || entry.handle === issue().assignee_ref?.replace(/^member:/, ""));
    if (member !== undefined) return member.display_name;
    const team = props.detail.teams.find((entry) => entry.id === issue().assignee_team_id || entry.slug === issue().assignee_ref?.replace(/^team:/, ""));
    return team?.name ?? "Unassigned";
  };
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
    if (["false_positive", "manual_unverified", "exception"].includes(dialog() ?? "") && reason === "") return;
    if (dialog() === "remediation") props.onRemediation(action(), includeLinked());
    if (dialog() === "false_positive") props.onResolve("false_positive", reason);
    if (dialog() === "manual_unverified") props.onResolve("manual_unverified", reason);
    if (dialog() === "exception") props.onRequestException(reason, duration());
    if (dialog() === "link" && externalUrl().trim() !== "") props.onLinkExternal(externalUrl().trim(), externalTitle());
    if (dialog() === "github" && githubRepo() !== "" && githubTitle().trim() !== "") props.onCreateExternal(githubRepo(), githubTitle().trim());
    if (dialog() === "merge" && mergeId().trim() !== "") props.onMerge(mergeId().trim());
    setDialog(null);
    setNote("");
  };
  return (
    <>
      <TopBar.Slot name="title">
        <A href="/issues">Issues</A>
        <span aria-hidden="true"> / </span>
        <A href={`/issues/${encodeURIComponent(issue().id)}`}>
          {issue().source} · {issue().short_id}
        </A>
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
        <Show when={issue().status === "archived"}>
          <Button size="sm" onClick={props.onUnarchive}>Unarchive</Button>
        </Show>
        <Show when={issue().status !== "archived"}>
          <ArchiveMenu onArchive={props.onArchive} />
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
        <Button size="sm" aria-expanded={moreOpen()} onClick={() => setMoreOpen(!moreOpen())}>More</Button>
        <Show when={moreOpen()}>
          <div class="issue-more-menu" role="menu" aria-label="More issue actions">
            <button type="button" role="menuitem" onClick={() => void navigator.clipboard?.writeText(`${location.origin}/issues/${encodeURIComponent(issue().id)}`).then(() => setPermalinkCopied(true))}>Copy permalink</button>
            <button type="button" role="menuitem" onClick={props.onCopyMarkdown}>Copy masked Markdown</button>
            <button type="button" role="menuitem" onClick={() => setDialog("exception")}>Request exception</button>
            <button type="button" role="menuitem" onClick={() => document.querySelector<HTMLSelectElement>('select[aria-label="Start workflow"]')?.focus()}>Start workflow</button>
            <label role="menuitem">Upload evidence<input aria-label="Upload evidence" type="file" onChange={props.onUpload} /></label>
            <button type="button" role="menuitem" onClick={() => setDialog("link")}>Link external issue</button>
            <button type="button" role="menuitem" onClick={() => setDialog("github")}>Create GitHub issue</button>
            <button type="button" role="menuitem" onClick={() => setDialog("merge")}>Merge with…</button>
            <Show when={props.detail.mergedChildren.length > 0}><button type="button" role="menuitem" onClick={props.onUnmerge}>Unmerge</button></Show>
            <Show when={props.detail.mergedInto}><button type="button" role="menuitem" onClick={() => props.onUnmergeChild(props.detail.mergedInto!.id)}>Unmerge from primary</button></Show>
            <Show when={permalinkCopied()}><span role="status">Permalink copied</span></Show>
          </div>
        </Show>
        <span class="issue-action-spacer" />
        <label>Priority
          <Select aria-label="Priority" value={issue().priority ?? "medium"} onChange={(event) => props.onPriority(event.currentTarget.value as IssuePriority)}>
            <option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
          </Select>
        </label>
        <Button size="sm" aria-pressed={issue().bookmarked === true} onClick={() => props.onBookmark(issue().bookmarked !== true)}>{issue().bookmarked ? "Bookmarked" : "Bookmark"}</Button>
        <Button size="sm" aria-pressed={props.detail.subscription.subscribed} onClick={() => props.onSubscribe(!props.detail.subscription.subscribed)}>{props.detail.subscription.subscribed ? "Following" : "Subscribe"}</Button>
        <label>
          Assignee
          <div class="assignee-current">
            <Show when={issue().assignee}>
              {(person) => Avatar({ name: person().display_name, ref: person().ref })}
            </Show>
            <span>{currentAssignee()}</span>
          </div>
          <div class="assignee-suggestions" aria-label="Suggested assignees">
            <For each={props.detail.suggestions}>
              {(suggestion) => <button type="button" onClick={() => props.onAssignee(suggestion.ref)}>{Avatar({ name: suggestion.display_name, ref: suggestion.ref, size: 20 })} {suggestion.display_name} <Tag variant="muted">{suggestion.reason.replaceAll("_", " ")}</Tag></button>}
            </For>
          </div>
          <Select
            aria-label="Assignee"
            value={props.assignee}
            onChange={(event) => props.onAssignee(event.currentTarget.value)}
          >
            <option value="">
              {currentAssignee()}
            </option>
            <For each={props.detail.suggestions}>
              {(suggestion) => <option value={suggestion.ref}>★ {suggestion.display_name} · {suggestion.reason.replaceAll("_", " ")}</option>}
            </For>
            <For each={props.detail.members}>
              {(member) => (
                <option value={`member:${member.handle}`}>{member.display_name}</option>
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
          <h3>{dialog() === "remediation" ? "Mark remediated" : dialog() === "exception" ? "Request exception" : dialog() === "merge" ? "Merge issue" : dialog() === "link" ? "Link external issue" : dialog() === "github" ? "Create GitHub issue" : "Resolve issue"}</h3>
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
          <Show when={dialog() === "link"}>
            <label>Issue URL<input aria-label="External issue URL" type="url" value={externalUrl()} onInput={(event) => setExternalUrl(event.currentTarget.value)} required /></label>
            <label>Title<input aria-label="External issue title" value={externalTitle()} onInput={(event) => setExternalTitle(event.currentTarget.value)} /></label>
          </Show>
          <Show when={dialog() === "github"}>
            <label>Repository<Select aria-label="GitHub repository" value={githubRepo()} onChange={(event) => setGithubRepo(event.currentTarget.value)}>
              <option value="">Select repository</option><For each={props.detail.githubRepos}>{(repo) => <option value={repo}>{repo}</option>}</For>
            </Select></label>
            <label>Title<input aria-label="GitHub issue title" value={githubTitle()} onInput={(event) => setGithubTitle(event.currentTarget.value)} /></label>
            <Show when={props.detail.githubRepos.length === 0}><p>Configure GitHub integration to create an issue.</p></Show>
          </Show>
          <Show when={dialog() === "merge"}>
            <label>Issue ID to merge<input aria-label="Issue to merge" value={mergeId()} onInput={(event) => setMergeId(event.currentTarget.value)} /></label>
          </Show>
          <Show when={dialog() !== "remediation"}>
            <label>Reason<textarea aria-label="Reason" value={note()} onInput={(event) => setNote(event.currentTarget.value)} required /></label>
          </Show>
          <Button type="submit" size="sm" disabled={(["false_positive", "manual_unverified", "exception"].includes(dialog() ?? "") && note().trim() === "") || (dialog() === "github" && (githubRepo() === "" || githubTitle().trim() === "")) || (dialog() === "link" && externalUrl().trim() === "") || (dialog() === "merge" && mergeId().trim() === "")}>Submit</Button>
          <Button type="button" size="sm" onClick={() => setDialog(null)}>Cancel</Button>
        </form>
      </Show>
    </>
  );
}
