import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi } from "vitest";
import TasksPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderTasks(): void {
  render(() => <MemoryRouter><Route path="*" component={TasksPage} /></MemoryRouter>);
}

function pageResponses(): Record<string, unknown> {
  return {
    "/api/human-tasks?assignee=me&status=open": { items: [{ id: "task-1", run_id: "run-1", step_run_id: "step-1", kind: "owner_review", title: "Review owner", assignee_ref: "member:alex", due_at: null, status: "open", outcome: null }], next_cursor: null },
    "/api/exceptions?approver=me&status=requested": { items: [], next_cursor: null },
    "/api/issues?assignee=me&sort=risk": { items: [], next_cursor: null },
  };
}

it("requires a comment for owner review exceptions and submits the selected duration", async () => {
  const responses = pageResponses();
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === "POST") return new Response(JSON.stringify({ id: "task-1", status: "completed" }));
    return new Response(JSON.stringify(responses[url] ?? { items: [], next_cursor: null }));
  });
  renderTasks();
  fireEvent.click(await screen.findByRole("button", { name: "Exception" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("comment_required");
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  fireEvent.input(screen.getByLabelText("Comment"), { target: { value: "Owner confirmed review" } });
  fireEvent.change(screen.getByLabelText("Exception duration for Review owner"), { target: { value: "30d" } });
  fireEvent.click(screen.getByRole("button", { name: "Exception" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/human-tasks/task-1/complete", expect.objectContaining({ method: "POST", body: JSON.stringify({ outcome: "exception", comment: "Owner confirmed review", exception_duration: "30d" }) })));
});

it("completes owner removal and shows the empty state", async () => {
  const responses = pageResponses();
  let completed = false;
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "POST") { completed = true; return new Response(JSON.stringify({ id: "task-1", status: "completed" })); }
    const value = completed && String(input).includes("/human-tasks") ? { items: [], next_cursor: null } : responses[String(input)] ?? { items: [], next_cursor: null };
    return new Response(JSON.stringify(value));
  });
  renderTasks();
  fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/human-tasks/task-1/complete", expect.objectContaining({ method: "POST", body: JSON.stringify({ outcome: "remove" }) })));
  expect(await screen.findByText("Nothing needs your attention")).toBeInTheDocument();
});

it("approves a requested exception through the approval endpoint", async () => {
  const responses = pageResponses();
  responses["/api/exceptions?approver=me&status=requested"] = {
    items: [{ id: "exc-1", issue_id: "issue-1", status: "requested", reason: "Owner review", duration: "30d", requested_by: "member:alex", decided_by: null, approver_ref: "member:alex", expires_at: null, created_at: "2026-10-10T00:00:00Z", decided_at: null }],
    next_cursor: null,
  };
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/exceptions/exc-1/approve" && init?.method === "POST") return new Response(JSON.stringify({ id: "exc-1", status: "active" }));
    return new Response(JSON.stringify(responses[String(input)] ?? { items: [], next_cursor: null }));
  });
  renderTasks();
  fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/exceptions/exc-1/approve", expect.objectContaining({ method: "POST", body: "{}" })));
});

it("requests assigned issues by risk and separates pending verification", async () => {
  const responses = pageResponses();
  responses["/api/issues?assignee=me&sort=risk"] = {
    items: [
      { id: "issue-1", short_id: "TELE-1", display_title: "High risk issue", status: "unresolved", risk_score: 90 },
      { id: "issue-2", short_id: "TELE-2", display_title: "Verifying issue", status: "pending_verification", risk_score: 80 },
    ],
    next_cursor: null,
  };
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => new Response(JSON.stringify(responses[String(input)] ?? { items: [], next_cursor: null })));
  renderTasks();
  expect(await screen.findByRole("link", { name: "TELE-1 · High risk issue" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Verifying" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "TELE-2 · Verifying issue" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/issues?assignee=me&sort=risk", expect.anything());
});
