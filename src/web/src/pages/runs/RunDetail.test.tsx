import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route, useParams } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "./RunDetail";

const summary = { id: "run-01", definition_id: "wf-01", issue_id: "iss-01", status: "waiting", trigger: "manual", started_at: "2026-10-08T00:00:00Z", finished_at: null };
const task = { id: "task-01", step_run_id: "step-02", kind: "human", title: "Review finding", assignee_ref: "team:security", status: "open", outcome: null, outcomes: ["handled", "send_back"] };
const run = {
  run: summary,
  steps: [
    { id: "step-01", step_id: "detect", visit: 1, branch_token: "main", status: "succeeded", attempt: 1, input_json: "{}", output_json: "{\"outcome\":\"found\"}", error_code: null },
    { id: "step-02", step_id: "review", visit: 1, branch_token: "main", status: "waiting", attempt: 1, input_json: "{}", output_json: null, error_code: null },
    { id: "step-03", step_id: "map", visit: 1, branch_token: "main", status: "waiting", attempt: 1, input_json: "{}", output_json: "{\"map_job_id\":\"map-01\"}", error_code: null },
  ],
  transitions: [{ from_step_run_id: "step-02", to_step_id: "detect", kind: "send_back", at: "2026-10-08T00:01:30Z", reason: "Review the masked finding." }],
  human_tasks: [task],
};
const mapJob = { id: "map-01", origin: "workflow", status: "running", collector_config_id: null, counts: { ready: 880, dispatched: 0, succeeded: 120, failed: 0, cancelled: 0 }, partitions_done: 2, partitions_total: 10, findings_so_far: 3, paused_until: null, failed_shards: [], result_json: null };
const definition = { id: "wf-01", name: "incident", version: 1, active: true };
const workflowVersion = { ...definition, source_format: "json", source_text: "", compiled_json: JSON.stringify({ steps: [{ id: "detect", type: "detect" }, { id: "review", type: "human_task" }, { id: "map", type: "map" }] }) };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

function renderRun(runId = "run-01"): void { render(() => <MemoryRouter><Route path="*" component={() => <RunDetail runId={runId} />} /></MemoryRouter>); }
function responseFor(input: RequestInfo | URL, current = run): Response {
  const url = String(input);
  if (url.endsWith("/map-01")) return Response.json(mapJob);
  if (url === "/api/workflows") return Response.json({ items: [definition] });
  if (url.endsWith("/versions/1")) return Response.json(workflowVersion);
  return Response.json(current);
}

describe("RunDetail", () => {
  it("shows an error banner when the run request returns a 404", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/runs/run-404") return Response.json({ error: { code: "not_found", detail: "run not found" } }, { status: 404 });
      return responseFor(input);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderRun("run-404");
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    await vi.advanceTimersByTimeAsync(1);
    expect(screen.getByRole("alert").textContent).toContain("not_found");
    expect(screen.getByText("Workflow run · run-404")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeDisabled();
    const runFetches = (): number => fetchMock.mock.calls.filter(([input]) => String(input) === "/api/runs/run-404").length;
    expect(runFetches()).toBe(1);
    await vi.advanceTimersByTimeAsync(5000);
    expect(runFetches()).toBe(1);
  });

  it("updates the run alert after navigating from a 404 to a 500 without polling", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/runs/r1") return Response.json({ error: { code: "not_found", detail: "run not found" } }, { status: 404 });
      if (url === "/api/runs/r2") return Response.json({ error: { code: "internal", detail: "request failed" } }, { status: 500 });
      return responseFor(input);
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = createMemoryHistory();
    history.set({ value: "/runs/r1" });
    render(() => <MemoryRouter history={history}><Route path="/runs/:id" component={() => <RunDetail runId={useParams<{ id: string }>().id} />} /></MemoryRouter>);
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    await vi.advanceTimersByTimeAsync(1);
    expect(screen.getByRole("alert")).toHaveTextContent("not_found");
    history.set({ value: "/runs/r2" });
    await vi.waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("internal"));
    expect(screen.getByRole("alert")).not.toHaveTextContent("not_found");
    const runFetches = (): number => fetchMock.mock.calls.filter(([input]) => String(input) === "/api/runs/r2").length;
    expect(runFetches()).toBe(1);
    await vi.advanceTimersByTimeAsync(5000);
    expect(runFetches()).toBe(1);
  });

  it("keeps the run visible when workflow definitions return a 500", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/workflows") return Response.json({ error: { code: "internal_error", detail: "failed" } }, { status: 500 });
      return responseFor(input);
    }));
    renderRun();
    expect(await screen.findByText("Workflow run · run-01")).toBeInTheDocument();
    expect(await screen.findByText("detect")).toBeInTheDocument();
    expect(screen.getByText("wf-01")).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".step-list li .step-title")?.textContent).toContain("Step · visit 1"));
  });

  it("renders ordered steps and a marked send-back transition", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => responseFor(input)));
    renderRun();
    expect(await screen.findByText("detect")).toBeInTheDocument();
    expect(screen.getByText("review")).toBeInTheDocument();
    expect(screen.getByText("map")).toBeInTheDocument();
    expect(screen.getByText(/Send back/)).toBeInTheDocument();
    expect(screen.getByText("Reason: Review the masked finding.")).toBeInTheDocument();
    expect(screen.getByText("incident · run-01")).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".step-list li .step-title")?.textContent).toContain("detect · visit"));
    expect(document.querySelector(".step-list li .step-title")?.textContent).toContain("visit 1");
  });

  it("posts the selected outcome and comment when completing a human task", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input).endsWith("/complete") ? Response.json({ id: task.id, status: "completed" }) : responseFor(input));
    vi.stubGlobal("fetch", fetchMock); renderRun();
    fireEvent.change(await screen.findByLabelText("Outcome"), { target: { value: "handled" } });
    fireEvent.input(screen.getByLabelText("Comment"), { target: { value: "Removed from repository." } });
    fireEvent.click(screen.getByRole("button", { name: "Complete task" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/human-tasks/task-01/complete" && init?.method === "POST" && init.body === JSON.stringify({ outcome: "handled", comment: "Removed from repository." }))).toBe(true));
  });

  it("renders map progress from terminal and pending shard counts", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => responseFor(input)));
    renderRun();
    expect(await screen.findByText("120 / 1000")).toBeInTheDocument();
    expect(screen.getByText(/partitions 2 \/ 10/)).toBeInTheDocument();
  });

  it("shows the evidence prerequisite without exposing the API detail", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/complete") ? Response.json({ error: { code: "evidence_required", detail: "internal detail" } }, { status: 409 }) : responseFor(input));
    vi.stubGlobal("fetch", fetchMock); renderRun();
    fireEvent.change(await screen.findByLabelText("Outcome"), { target: { value: "handled" } });
    fireEvent.click(await screen.findByRole("button", { name: "Complete task" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/complete"))).toBe(true));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Upload evidence first."));
    expect(screen.queryByText("internal detail")).toBeNull();
  });

  it("preserves a human-task draft across a five-second poll", async () => {
    vi.useFakeTimers();
    const pollingRun = { ...run, human_tasks: [{ ...task, outcomes: ["handled", "escalate"] }] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => responseFor(input, pollingRun));
    vi.stubGlobal("fetch", fetchMock); renderRun();
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    await vi.advanceTimersByTimeAsync(1);
    fireEvent.change(screen.getByLabelText("Outcome"), { target: { value: "escalate" } });
    fireEvent.input(screen.getByLabelText("Comment"), { target: { value: "Escalating to security." } });
    await vi.advanceTimersByTimeAsync(5000);
    await Promise.resolve(); await Promise.resolve();
    expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/runs/run-01")).toHaveLength(2);
    expect(screen.getByLabelText("Outcome")).toHaveValue("escalate");
    expect(screen.getByLabelText("Comment")).toHaveValue("Escalating to security.");
  });

  it("does not poll a terminal run after five seconds", async () => {
    vi.useFakeTimers();
    const terminalRun = { ...run, run: { ...summary, status: "succeeded" } };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => responseFor(input, terminalRun));
    vi.stubGlobal("fetch", fetchMock); renderRun();
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    await vi.advanceTimersByTimeAsync(5000);
    await Promise.resolve(); await Promise.resolve();
    expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/runs/run-01")).toHaveLength(1);
  });
  it("renders per-target counts for a v2 notify output", async () => {
    const notifyRun = { ...run, run: { ...summary, status: "succeeded" }, steps: [{ id: "step-09", step_id: "alert", visit: 1, branch_token: "main", status: "succeeded", attempt: 1, input_json: "{}", output_json: JSON.stringify({ queued: true, notification_ids: ["ntf-01", "ntf-02"], targets: [{ index: 0, channel: "slack", queued: 2, blocked: 0, skipped: 1 }, { index: 1, channel: "email", queued: 0, blocked: 1, skipped: 0 }] }), error_code: null }, { id: "step-10", step_id: "legacy", visit: 1, branch_token: "main", status: "succeeded", attempt: 1, input_json: "{}", output_json: JSON.stringify({ queued: true, notification_ids: ["ntf-03"] }), error_code: null }], transitions: [], human_tasks: [] };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => responseFor(input, notifyRun)));
    renderRun();
    const heading = await screen.findByText("Notification targets");
    expect(screen.getAllByText("Notification targets")).toHaveLength(1);
    const table = heading.parentElement?.querySelector("table");
    expect(table).not.toBeNull();
    const rows = Array.from(table?.querySelectorAll("tbody tr") ?? []).map((row) => Array.from(row.querySelectorAll("td")).map((cell) => cell.textContent));
    expect(rows).toEqual([["0", "slack", "2", "0", "1"], ["1", "email", "0", "1", "0"]]);
  });
});
