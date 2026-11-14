import { createResource, createSignal, onCleanup, onMount, Show, type JSX } from "solid-js";
import {
  ApiError,
  apiFetch,
  directoryApi,
  evidenceApi,
  exceptionsApi,
  issuesApi,
  runsApi,
  workflowsApi,
} from "../../api/client";
import type { ExceptionDuration, ExternalIssueLink, IssueEvent, IssuePriority, IssueStatus } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { showToast } from "../../components/ui";
import { EvidenceRejectedError, uploadEvidence } from "./uploadEvidence";
import { registerShortcuts } from "../../components/shell/shortcuts";
import { IssueDetailHeader } from "./detail/IssueDetailHeader";
import { IssueDetailSections } from "./detail/IssueDetailSections";
import { IssueDetailSidebar } from "./detail/IssueDetailSidebar";
import type {
  DetailData,
  DetailStats,
  DetailStatsBucket,
  DetailTagItem,
  DetailTags,
} from "./detail/types";
import type { ArchiveRequest } from "./ArchiveMenu";
import "./issue-detail.css";

function errorValue(value: unknown): ApiError {
  return value instanceof ApiError
    ? value
    : new ApiError(0, "request_failed", "The request could not be completed.");
}

function normalizeStats(value: unknown): DetailStats {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { buckets: [] };
  }
  const buckets: readonly unknown[] =
    "buckets" in value && Array.isArray(value.buckets)
      ? value.buckets
      : "items" in value && Array.isArray(value.items)
        ? value.items
        : [];
  const normalized: DetailStatsBucket[] = [];
  for (const entry of buckets) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      continue;
    }
    if (!("count" in entry) || typeof entry.count !== "number") continue;
    const start =
      "start" in entry && typeof entry.start === "string"
        ? entry.start
        : "at" in entry && typeof entry.at === "string"
          ? entry.at
          : null;
    if (start !== null) normalized.push({ start, count: entry.count });
  }
  return { buckets: normalized };
}

function normalizeTags(value: unknown): DetailTags {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { total: 0, items: [] };
  }
  const total = "total" in value && typeof value.total === "number" ? value.total : 0;
  const items: DetailTagItem[] = [];
  if ("tags" in value && Array.isArray(value.tags)) {
    const tags: readonly unknown[] = value.tags;
    for (const tag of tags) {
      if (
        typeof tag !== "object" || tag === null || Array.isArray(tag) ||
        !("key" in tag) || typeof tag.key !== "string" ||
        !("values" in tag) || !Array.isArray(tag.values)
      ) continue;
      const values: readonly unknown[] = tag.values;
      for (const entry of values) {
        if (
          typeof entry !== "object" || entry === null || Array.isArray(entry) ||
          !("value" in entry) || typeof entry.value !== "string" ||
          !("count" in entry) || typeof entry.count !== "number"
        ) continue;
        const percent =
          "percent" in entry && typeof entry.percent === "number"
            ? entry.percent
            : total === 0 ? 0 : entry.count / total * 100;
        items.push({ key: tag.key, value: entry.value, count: entry.count, percent });
      }
    }
  } else if ("items" in value && Array.isArray(value.items)) {
    const entries: readonly unknown[] = value.items;
    for (const entry of entries) {
      if (
        typeof entry !== "object" || entry === null || Array.isArray(entry) ||
        !("key" in entry) || typeof entry.key !== "string" ||
        !("value" in entry) || typeof entry.value !== "string" ||
        !("count" in entry) || typeof entry.count !== "number"
      ) continue;
      items.push({
        key: entry.key,
        value: entry.value,
        count: entry.count,
        percent: total === 0 ? 0 : entry.count / total * 100,
      });
    }
  }
  return { total, items };
}

export function IssueDetail(props: Readonly<{ id: string }>): JSX.Element {
  const [period, setPeriod] = createSignal("14d");
  const [selection, setSelection] = createSignal<DetailData["selection"]>(null);
  const [data, { refetch }] = createResource(
    () => props.id,
    async (id): Promise<DetailData> => {
      const issue = await issuesApi.get(id);
      const isCredential = [
        "credential",
        "token",
        "private_key",
        "password",
      ].includes(issue.rule_class);
      const [
        eventPage,
        historyPage,
        runPage,
        evidencePage,
        membersPage,
        teamsPage,
        workflowPage,
        occurrences,
        selection,
        stats,
        tags,
        activity,
        externalIssues,
        githubRepos,
      ] = await Promise.all([
        issuesApi.events(id),
        issuesApi.history(id),
        runsApi.list(new URLSearchParams({ issue_id: id })),
        evidenceApi.list(new URLSearchParams({ issue_id: id })),
        directoryApi.members(),
        directoryApi.teams(),
        workflowsApi.list(),
        isCredential
          ? issuesApi.occurrences(id)
          : Promise.resolve({ items: [], key_versions: [] }),
        issuesApi.eventAt(id, "latest"),
        issuesApi.issueStats(id, period()),
        issuesApi.tags(id),
        issuesApi.activity(id),
        issuesApi.externalIssues(id),
        issuesApi.githubRepos(),
      ]);
      const active = runPage.items
        .filter(
          (item) => item.status === "running" || item.status === "waiting",
        )
        .at(-1);
      const activeRun =
        active === undefined ? null : await runsApi.get(active.id);
      return {
        issue,
        occurrences,
        events: eventPage.items,
        history: historyPage.items,
        runs: runPage.items,
        evidence: evidencePage.items,
        members: membersPage.items,
        teams: teamsPage.items,
        workflows: workflowPage.items,
        activeRun,
        selection,
        stats: normalizeStats(stats),
        tags: normalizeTags(tags),
        activity: activity.items.map((item) => {
          const comment = "comment" in item && typeof item.comment === "object" && item.comment !== null
            ? item.comment
            : null;
          const commentRecord = comment !== null && "id" in comment && "body" in comment
            ? {
                id: String(comment.id),
                body: String(comment.body),
                edited_at: "edited_at" in comment && typeof comment.edited_at === "string" ? comment.edited_at : null,
                deleted: "deleted" in comment && comment.deleted === true,
                can_edit: "can_edit" in comment && comment.can_edit === true,
              }
            : null;
          return {
            id: item.id,
            kind: item.kind,
            actor: item.actor,
            body: item.body,
            at: item.at,
            comment: commentRecord,
          };
        }),
        externalIssues: externalIssues.items,
        githubRepos: githubRepos.items,
        subscription: "subscription" in issue && typeof issue.subscription === "object" && issue.subscription !== null
          ? {
              subscribed: "subscribed" in issue.subscription && issue.subscription.subscribed === true,
              reason: "reason" in issue.subscription && typeof issue.subscription.reason === "string" ? issue.subscription.reason : null,
            }
          : { subscribed: false, reason: null },
        mergedInto: "merged_into" in issue && typeof issue.merged_into === "object" && issue.merged_into !== null && "id" in issue.merged_into && "short_id" in issue.merged_into
          ? { id: String(issue.merged_into.id), short_id: String(issue.merged_into.short_id) }
          : null,
        mergedChildren: "merged_children" in issue && Array.isArray(issue.merged_children)
          ? issue.merged_children.filter((child): child is Readonly<{ id: string; short_id: string; source: string; display_locator: string; status: string }> => typeof child === "object" && child !== null && "id" in child && "short_id" in child && "source" in child && "display_locator" in child && "status" in child).map((child) => ({ id: child.id, short_id: child.short_id, source: child.source, display_locator: child.display_locator, status: child.status }))
          : [],
        suggestions: "suggested_assignees" in issue && Array.isArray(issue.suggested_assignees)
          ? issue.suggested_assignees.filter((entry): entry is Readonly<{ ref: string; display_name: string; reason: string }> => typeof entry === "object" && entry !== null && "ref" in entry && "display_name" in entry && "reason" in entry).map((entry) => ({ ref: entry.ref, display_name: entry.display_name, reason: entry.reason }))
          : [],
      };
    },
  );
  const [error, setError] = createSignal<ApiError | null>(null);
  const [assignee, setAssignee] = createSignal("");
  const [comment, setComment] = createSignal("");
  const [uploadMessage, setUploadMessage] = createSignal("");
  const [sidebarOpen, setSidebarOpen] = createSignal(true);
  const [activeTab, setActiveTab] = createSignal("Findings");

  const selectedEvent = (): IssueEvent | undefined => {
    return selection()?.event ?? data()?.selection?.event ?? data()?.events[0];
  };
  const currentStep = (): string => {
    const step = data()
      ?.activeRun?.steps.slice()
      .reverse()
      .find(
        (item) =>
          item.status !== "succeeded" &&
          item.status !== "failed" &&
          item.status !== "cancelled",
      );
    return step?.step_id ?? "No active step";
  };
  const run = async (action: () => Promise<unknown>): Promise<void> => {
    setError(null);
    setUploadMessage("");
    try {
      await action();
      await refetch();
    } catch (caught) {
      setError(errorValue(caught));
    }
  };
  const updateStatus = (status: IssueStatus): void => {
    const issue = data()?.issue;
    if (status === "resolved" || status === "ignored") return;
    if (issue !== undefined)
      void run(() => issuesApi.update(issue.id, { status }));
  };
  const remediate = (action: "remove" | "restrict", includeLinked: boolean): void => {
    const issue = data()?.issue;
    if (issue !== undefined)
      void run(() => issuesApi.remediation(issue.id, { action, include_linked: includeLinked }));
  };
  const resolve = (reason: "false_positive" | "manual_unverified", note: string): void => {
    const issue = data()?.issue;
    if (issue !== undefined) void run(() => issuesApi.resolve(issue.id, { reason, note }));
  };
  const requestException = (reason: string, duration: ExceptionDuration): void => {
    const issue = data()?.issue;
    if (issue !== undefined) void run(() => issuesApi.requestException(issue.id, { reason, duration }));
  };
  const decideException = (action: "approve" | "reject"): void => {
    const exception = data()?.issue.active_exception;
    if (exception === null || exception === undefined) return;
    void run(() =>
      action === "approve"
        ? exceptionsApi.approve(exception.id)
        : exceptionsApi.reject(exception.id),
    );
  };
  const updateAssignee = (value: string): void => {
    const issue = data()?.issue;
    if (issue === undefined) return;
    setAssignee(value);
    void run(() => issuesApi.update(issue.id, { assignee_ref: value || null }));
  };
  const startWorkflow = (name: string): void => {
    const issue = data()?.issue;
    if (issue !== undefined && name !== "")
      void run(() => workflowsApi.startRun(name, issue.id));
  };
  const submitComment = (event: SubmitEvent): void => {
    event.preventDefault();
    const issue = data()?.issue;
    const body = comment().trim();
    if (issue === undefined || body === "") return;
    void run(async () => {
      await issuesApi.comment(issue.id, body);
      setComment("");
      showToast("Comment posted", "success");
    });
  };
  const navigateEvent = async (selector: string): Promise<void> => {
    try {
      const result = await issuesApi.eventAt(props.id, selector);
      setSelection(result);
    } catch (caught) {
      setError(errorValue(caught));
    }
  };
  const changePeriod = (value: string): void => {
    setPeriod(value);
    void refetch();
  };
  const changePriority = (value: IssuePriority): void => {
    void run(() => issuesApi.setPriority(props.id, value));
  };
  const setFollowed = (subscribed: boolean): void => {
    void run(() => issuesApi.setSubscription(props.id, subscribed));
  };
  const setBookmarked = (bookmarked: boolean): void => {
    void run(() => bookmarked ? issuesApi.bookmark(props.id) : issuesApi.unbookmark(props.id));
  };
  const archive = (request: ArchiveRequest): void => {
    void run(() => issuesApi.archive(props.id, request));
  };
  const unarchive = (): void => { void run(() => issuesApi.unarchive(props.id)); };
  const unmerge = (ids: readonly string[] = []): void => { void run(() => issuesApi.unmerge(props.id, ids)); };
  const unmergeChild = (primaryId: string, childId: string): void => { void run(() => issuesApi.unmerge(primaryId, [childId])); };
  const merge = (childId: string): void => { void run(() => issuesApi.merge({ primary_id: props.id, child_ids: [childId] })); };
  const linkExternal = (url: string, title: string): void => {
    const endpoint = `/api/issues/${encodeURIComponent(props.id)}/external-issues`;
    void run(() => title.trim() === ""
      ? apiFetch<ExternalIssueLink>(endpoint, { method: "POST", json: { url } })
      : apiFetch<ExternalIssueLink>(endpoint, { method: "POST", json: { url, title: title.trim() } }));
  };
  const createExternal = (repo: string, title: string): void => { void run(() => issuesApi.createGithubIssue(props.id, repo, title)); };
  const deleteExternal = (linkId: string): void => { void run(() => issuesApi.unlinkExternal(props.id, linkId)); };
  onMount(() => {
    const stop = registerShortcuts("detail", [
      { key: "r", handler: () => updateStatus("resolved") },
      { key: "a", handler: () => document.querySelector<HTMLSelectElement>('select[aria-label="Assignee"]')?.focus() },
      { key: "e", handler: () => archive({ kind: "for_duration", duration_minutes: 1440 }) },
      { key: "b", handler: () => setBookmarked(!(data()?.issue.bookmarked ?? false)) },
    ]);
    onCleanup(stop);
  });
  const upload = (event: Event & { currentTarget: HTMLInputElement }): void => {
    const file = event.currentTarget.files?.[0];
    const issue = data()?.issue;
    if (file === undefined || issue === undefined) return;
    void run(async () => {
      try {
        await uploadEvidence(file, issue.id, file.name);
        setUploadMessage("Evidence uploaded.");
      } catch (caught) {
        if (caught instanceof EvidenceRejectedError) {
          const rules =
            caught.ruleClasses.length > 0
              ? caught.ruleClasses.join(", ")
              : caught.detail;
          setUploadMessage(`Evidence rejected: ${rules}`);
          return;
        }
        throw caught;
      }
    });
  };
  const copyMarkdown = async (): Promise<void> => {
    const issue = data()?.issue;
    if (issue === undefined || navigator.clipboard === undefined) return;
    const markdown = `## ${issue.short_id}: ${issue.display_title}\n\nStatus: ${issue.status}\nRule: ${issue.rule_class}\nLocation: ${issue.display_locator}`;
    try {
      await navigator.clipboard.writeText(markdown);
      showToast("Masked issue summary copied", "success");
    } catch {
      showToast("Could not copy issue summary", "warning");
    }
  };

  return (
    <section class="page-content issue-detail-page">
      <Show
        when={data()}
        fallback={
          <p>{data.loading ? "Loading issue…" : "Issue unavailable."}</p>
        }
      >
        {(detail) => (
          <>
            <IssueDetailHeader
              detail={detail()}
              assignee={assignee()}
              onStatus={updateStatus}
              onRemediation={remediate}
              onResolve={resolve}
              onRequestException={requestException}
              onAssignee={updateAssignee}
              onStartWorkflow={startWorkflow}
              onCopyMarkdown={() => void copyMarkdown()}
              onPriority={changePriority}
              onArchive={archive}
              onUnarchive={unarchive}
              onBookmark={setBookmarked}
              onSubscribe={setFollowed}
              onUnmerge={() => unmerge()}
              onUnmergeChild={(primaryId) => unmergeChild(primaryId, props.id)}
              onMerge={merge}
              onLinkExternal={linkExternal}
              onCreateExternal={createExternal}
              onDeleteExternal={deleteExternal}
              onUpload={upload}
            />
            <Show when={error()} keyed>
              {(value) => <ErrorBanner error={value} />}
            </Show>
            <Show when={uploadMessage()}>
              <p role="status">{uploadMessage()}</p>
            </Show>
            <div
              classList={{
                "issue-detail-layout": true,
                "sidebar-closed": !sidebarOpen(),
              }}
            >
              <IssueDetailSections
                detail={detail()}
                selectedEvent={selectedEvent()}
                selection={selection() ?? detail().selection}
                activeTab={activeTab()}
                onNavigate={navigateEvent}
                period={period()}
                onPeriod={changePeriod}
                onUnmerge={unmerge}
                onUnmergeChild={unmergeChild}
                onDeleteExternal={deleteExternal}
                onActiveTab={setActiveTab}
                onUpload={upload}
              />
              <Show when={sidebarOpen()}>
                <IssueDetailSidebar
                  detail={detail()}
                  currentStep={currentStep()}
                  comment={comment()}
                  onCommentChange={setComment}
                  onComment={submitComment}
                  onEditComment={(id, body) => { void run(() => issuesApi.editComment(props.id, id, body)); }}
                  onDeleteComment={(id) => { void run(() => issuesApi.deleteComment(props.id, id)); showToast("Comment removed", "success"); }}
                  onClose={() => setSidebarOpen(false)}
                  onExceptionDecision={decideException}
                />
              </Show>
              <Show when={!sidebarOpen()}>
                <button
                  type="button"
                  class="ui-button xs"
                  onClick={() => setSidebarOpen(true)}
                >
                  Open sidebar
                </button>
              </Show>
            </div>
          </>
        )}
      </Show>
    </section>
  );
}
