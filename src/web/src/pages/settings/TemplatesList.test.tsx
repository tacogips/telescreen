import { cleanup, fireEvent, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import { mockApi, renderSettings, requestBody, settle } from "./testUtils";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

const templates = [
  { name: "system.workflow_notify", event_kind: "workflow_notify", builtin: true, description: "Built-in notification", active_version: 0, latest_version: 0, updated_at: null },
  { name: "workflow.review", event_kind: "workflow_notify", builtin: false, description: "Workflow review message", active_version: 1, latest_version: 2, updated_at: "2026-10-10T12:00:00Z" },
];
function handler(url: string, init: RequestInit | undefined): Response | undefined {
  if (url === "/api/notification-templates" && init?.method === "POST") return Response.json({ name: "team.alert", version: 1 }, { status: 201 });
  if (url === "/api/notification-templates") return Response.json({ items: templates });
  return undefined;
}

it("renders built-in and custom rows from the API", async () => {
  mockApi(["templates.read", "templates.write"], handler);
  renderSettings("/settings/templates");
  expect(await screen.findByText("system.workflow_notify")).toBeInTheDocument();
  expect(screen.getByText("workflow.review").closest("a")).toHaveAttribute("href", "/settings/templates/workflow.review");
  expect(screen.getByText("Built-in")).toBeInTheDocument();
  expect(screen.getByText("Custom")).toBeInTheDocument();
  expect(screen.getByText("Notification templates")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "New template" })).toBeInTheDocument();
});

it("hides New template without templates.write", async () => {
  const fetchMock = mockApi(["templates.read"], handler);
  renderSettings("/settings/templates");
  expect(await screen.findByText("workflow.review")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/auth/me", expect.anything()));
  await settle();
  expect(screen.queryByRole("button", { name: "New template" })).toBeNull();
});

it("creates a template with a name and event kind", async () => {
  const fetchMock = mockApi(["templates.read", "templates.write"], handler);
  renderSettings("/settings/templates");
  fireEvent.click(await screen.findByRole("button", { name: "New template" }));
  fireEvent.input(screen.getByLabelText("Name"), { target: { value: "team.alert" } });
  fireEvent.change(screen.getByLabelText("Event kind"), { target: { value: "sla_escalated" } });
  fireEvent.input(screen.getByLabelText("Subject"), { target: { value: "Escalated {{ issue.short_id }}" } });
  fireEvent.input(screen.getByLabelText("Body"), { target: { value: "A task needs attention." } });
  fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([input, init]) => String(input) === "/api/notification-templates" && init?.method === "POST");
    expect(requestBody(call?.[1])).toEqual({ name: "team.alert", event_kind: "sla_escalated", subject: "Escalated {{ issue.short_id }}", body: "A task needs attention." });
  });
});
