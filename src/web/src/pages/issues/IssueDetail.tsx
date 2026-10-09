import { createResource, createSignal, Show, type JSX } from "solid-js";
import {
  ApiError,
  directoryApi,
  evidenceApi,
  issuesApi,
  runsApi,
  workflowsApi,
} from "../../api/client";
import type { IssueEvent, IssueStatus } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { showToast } from "../../components/ui";
import { EvidenceRejectedError, uploadEvidence } from "./uploadEvidence";
import { IssueDetailHeader } from "./detail/IssueDetailHeader";
import { IssueDetailSections } from "./detail/IssueDetailSections";
import { IssueDetailSidebar } from "./detail/IssueDetailSidebar";
import type { DetailData } from "./detail/types";
import "./issue-detail.css";

function errorValue(value: unknown): ApiError {
  return value instanceof ApiError
    ? value
    : new ApiError(0, "request_failed", "The request could not be completed.");
}

export function IssueDetail(props: Readonly<{ id: string }>): JSX.Element {
  const [data, { refetch }] = createResource(
    () => props.id,
    async (id): Promise<DetailData> => {
      const [
        issue,
        eventPage,
        historyPage,
        runPage,
        evidencePage,
        membersPage,
        teamsPage,
        workflowPage,
      ] = await Promise.all([
        issuesApi.get(id),
        issuesApi.events(id),
        issuesApi.history(id),
        runsApi.list(new URLSearchParams({ issue_id: id })),
        evidenceApi.list(new URLSearchParams({ issue_id: id })),
        directoryApi.members(),
        directoryApi.teams(),
        workflowsApi.list(),
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
        events: eventPage.items,
        history: historyPage.items,
        runs: runPage.items,
        evidence: evidencePage.items,
        members: membersPage.items,
        teams: teamsPage.items,
        workflows: workflowPage.items,
        activeRun,
      };
    },
  );
  const [error, setError] = createSignal<ApiError | null>(null);
  const [assignee, setAssignee] = createSignal("");
  const [comment, setComment] = createSignal("");
  const [comments, setComments] = createSignal<string[]>([]);
  const [uploadMessage, setUploadMessage] = createSignal("");
  const [selectedEventId, setSelectedEventId] = createSignal<string | null>(
    null,
  );
  const [sidebarOpen, setSidebarOpen] = createSignal(true);
  const [activeTab, setActiveTab] = createSignal("Findings");

  const selectedEvent = (): IssueEvent | undefined => {
    const events = data()?.events ?? [];
    return events.find((event) => event.id === selectedEventId()) ?? events[0];
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
    if (issue !== undefined)
      void run(() => issuesApi.update(issue.id, { status }));
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
      setComments((prior) => [...prior, body]);
      setComment("");
      showToast("Comment posted", "success");
    });
  };
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
              onAssignee={updateAssignee}
              onStartWorkflow={startWorkflow}
              onCopyMarkdown={() => void copyMarkdown()}
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
                selectedEventId={selectedEventId()}
                activeTab={activeTab()}
                onSelectEvent={setSelectedEventId}
                onActiveTab={setActiveTab}
                onUpload={upload}
              />
              <Show when={sidebarOpen()}>
                <IssueDetailSidebar
                  detail={detail()}
                  currentStep={currentStep()}
                  comment={comment()}
                  comments={comments()}
                  onCommentChange={setComment}
                  onComment={submitComment}
                  onClose={() => setSidebarOpen(false)}
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
