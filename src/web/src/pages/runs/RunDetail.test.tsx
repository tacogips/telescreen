import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { MemoryRouter, Route } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "./RunDetail";

const task = { id: "task-1", run_id: "run-1", step_run_id: "step-2", kind: "human", title: "Review finding", assignee_ref: "team:security", tier_index: 0, due_at: "2026-10-09T00:00:00Z", status: "pending", outcome: null, outcomes: ["handled", "send_back"] };
const run = {
  id: "run-1", workflow: "incident", workflow_version: 2, issue_id: "issue-1", status: "waiting", current_step: "review", started_at: "2026-10-08T00:00:00Z", finished_at: null, context_json: "{}",
  steps: [
    { id: "step-1", run_id: "run-1", step_id: "detect", kind: "detect", status: "succeeded", visit: 1, started_at: "2026-10-08T00:00:00Z", finished_at: "2026-10-08T00:01:00Z", input_json: null, output_json: "{\"outcome\":\"found\"}" },
    { id: "step-2", run_id: "run-1", step_id: "review", kind: "human_task", status: "waiting", visit: 1, started_at: "2026-10-08T00:01:00Z", finished_at: null, input_json: null, output_json: null },
    { id: "step-3", run_id: "run-1", step_id: "sweep", kind: "map", status: "waiting", visit: 1, started_at: "2026-10-08T00:02:00Z", finished_at: null, input_json: "{\"map_job_id\":\"map-1\"}", output_json: null },
  ],
  transitions: [{ id: "transition-1", from_step: "review", to_step: "detect", kind: "send_back", at: "2026-10-08T00:01:30Z", reason: "Review the masked finding." }],
  human_tasks: [task],
};
const mapJob = { id: "map-1", origin: "workflow", status: "running", collector_config_id: null, counts: { ready: 880, dispatched: 0, succeeded: 120, failed: 0, cancelled: 0 }, partitions_done: 2, partitions_total: 10, findings_so_far: 3, paused_until: null, failed_shards: [], result_json: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

function renderRun(): void {
  render(() => <MemoryRouter><Route path="*" component={() => <RunDetail runId="run-1" />} /></MemoryRouter>);
}

describe("RunDetail", () => {
  it("renders three ordered steps and a marked send-back transition", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/map-1") ? Response.json(mapJob) : Response.json(run)));
    renderRun();
    expect(await screen.findByText("detect")).toBeInTheDocument();
    expect(screen.getByText("review")).toBeInTheDocument();
    expect(screen.getByText("sweep")).toBeInTheDocument();
    expect(screen.getByText("Send back")).toBeInTheDocument();
    expect(screen.getByText("Reason: Review the masked finding.")).toBeInTheDocument();
  });

  it("posts the selected outcome and comment when completing a human task", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/complete")) return Response.json({ ...task, status: "completed", outcome: "handled" });
      return Response.json(run);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderRun();
    fireEvent.change(await screen.findByLabelText("Outcome"), { target: { value: "handled" } });
    fireEvent.input(screen.getByLabelText("Comment"), { target: { value: "Removed from repository." } });
    fireEvent.click(screen.getByRole("button", { name: "Complete task" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/human-tasks/task-1/complete" && init?.method === "POST" && init.body === JSON.stringify({ outcome: "handled", comment: "Removed from repository." }))).toBe(true));
  });

  it("renders map progress from terminal and pending shard counts", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/map-1") ? Response.json(mapJob) : Response.json(run)));
    renderRun();
    expect(await screen.findByText("120 / 1000")).toBeInTheDocument();
    expect(screen.getByText(/partitions 2 \/ 10/)).toBeInTheDocument();
  });

  it("shows the evidence prerequisite without exposing the API detail", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/complete")) return Response.json({ error: { code: "evidence_required", detail: "internal detail" } }, { status: 409 });
      return Response.json(run);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderRun();
    fireEvent.change(await screen.findByLabelText("Outcome"), { target: { value: "handled" } });
    fireEvent.click(await screen.findByRole("button", { name: "Complete task" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/complete"))).toBe(true));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Upload evidence first."));
    expect(screen.queryByText("internal detail")).toBeNull();
  });

  it("preserves a human-task draft across a five-second poll", async () => {
    vi.useFakeTimers();
    const pollingRun = { ...run, human_tasks: [{ ...task, outcomes: ["handled", "escalate"] }] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/map-1") ? Response.json(mapJob) : Response.json({ ...pollingRun, human_tasks: pollingRun.human_tasks.map((item) => ({ ...item })) }));
    vi.stubGlobal("fetch", fetchMock);
    renderRun();
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    await vi.advanceTimersByTimeAsync(1);
    fireEvent.change(screen.getByLabelText("Outcome"), { target: { value: "escalate" } });
    fireEvent.input(screen.getByLabelText("Comment"), { target: { value: "Escalating to security." } });

    await vi.advanceTimersByTimeAsync(5000);
    await Promise.resolve(); await Promise.resolve();

    expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/runs/run-1")).toHaveLength(2);
    expect(screen.getByLabelText("Outcome")).toHaveValue("escalate");
    expect(screen.getByLabelText("Comment")).toHaveValue("Escalating to security.");
    expect(screen.queryByText("Loading run...")).toBeNull();
  });

  it("does not poll a terminal run after five seconds", async () => {
    vi.useFakeTimers();
    const terminalRun = { ...run, status: "succeeded" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/map-1") ? Response.json(mapJob) : Response.json(terminalRun));
    vi.stubGlobal("fetch", fetchMock);
    renderRun();
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    await vi.advanceTimersByTimeAsync(5000);
    await Promise.resolve(); await Promise.resolve();

    expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/runs/run-1")).toHaveLength(1);
  });
});
