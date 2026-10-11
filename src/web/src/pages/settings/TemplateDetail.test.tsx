import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import SettingsPage from "./index";
import { mockApi, renderSettings, requestBody, settle } from "./testUtils";

const values = new Map<string, string>();
const storage = {
  get length(): number { return values.size; },
  key: (index: number): string | null => Array.from(values.keys())[index] ?? null,
  getItem: (key: string): string | null => values.get(key) ?? null,
  setItem: (key: string, value: string): void => { values.set(key, value); },
  removeItem: (key: string): void => { values.delete(key); },
  clear: (): void => values.clear(),
};
vi.stubGlobal("localStorage", storage);

afterEach(() => { cleanup(); sessionStorage.clear(); storage.clear(); vi.restoreAllMocks(); });

const base = "/api/notification-templates/system.workflow_notify";
const detail = { name: "system.workflow_notify", event_kind: "workflow_notify", builtin: true, active_version: 1, versions: [{ version: 0, active: false, created_by: null, created_at: null }, { version: 1, active: true, created_by: "member:alex", created_at: "2026-10-10T12:00:00Z" }], variables: ["issue.short_id", "workflow.name"] };
const versionOne = { name: "system.workflow_notify", version: 1, event_kind: "workflow_notify", description: null, subject: "Update {{ issue.short_id }}", body: "{{ workflow.name }} sent an update.", active: true };
const preview = { email: { subject: "Update TELE-1", body: "Email body for the incident workflow.", dlp_verdict: "allow" }, slack: { subject: "Update TELE-1", body: "Slack body for the incident workflow.", dlp_verdict: "allow" } };
const write = ["templates.read", "templates.write"];

function handler(url: string, init: RequestInit | undefined): Response | undefined {
  if (url === base) return Response.json(detail);
  if (url === `${base}/versions/1`) return Response.json(versionOne);
  if (url === `${base}/versions/0`) return Response.json({ ...versionOne, version: 0, subject: "Workflow update", body: "A masked Telescreen notification is available.", active: false });
  if (url === `${base}/activate`) return Response.json({ name: detail.name, event_kind: "workflow_notify", builtin: true, description: null, active_version: 0, latest_version: 1, updated_at: null });
  if (url === "/api/notification-templates/validate") return Response.json({ valid: false, errors: [{ field: "body", code: "unknown_variable" }] });
  if (url === "/api/notification-templates/preview") return Response.json(preview);
  if (url === "/api/notification-templates" && init?.method === "POST") return Response.json({ name: detail.name, version: 2 }, { status: 201 });
  return undefined;
}
async function bodyField(): Promise<HTMLTextAreaElement> {
  const field = await screen.findByLabelText("Body") as HTMLTextAreaElement;
  await waitFor(() => expect(field.value).toBe(versionOne.body));
  return field;
}

it("keeps the dotted name and shows validation errors next to the field", async () => {
  const fetchMock = mockApi(write, handler);
  renderSettings("/settings/templates/system.workflow_notify");
  const body = await bodyField();
  expect(screen.getByText("Built-in default")).toBeInTheDocument();
  fireEvent.input(body, { target: { value: "{{ issue.raw }}" } });
  fireEvent.click(screen.getByRole("button", { name: "Validate" }));
  const error = await screen.findByText("Body: unknown_variable");
  expect(body.closest(".template-field")).toContainElement(error);
  const call = fetchMock.mock.calls.find(([input]) => String(input) === "/api/notification-templates/validate");
  expect(requestBody(call?.[1])).toEqual({ event_kind: "workflow_notify", subject: versionOne.subject, body: "{{ issue.raw }}" });
});

it("renders the Email and Slack preview tabs", async () => {
  mockApi(write, handler);
  renderSettings("/settings/templates/system.workflow_notify");
  await bodyField();
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  const tabs = await screen.findByRole("tablist", { name: "Preview channel" });
  expect(within(tabs).getAllByRole("tab")).toHaveLength(2);
  expect(screen.getByText("Email body for the incident workflow.")).toBeInTheDocument();
  expect(within(tabs).getAllByText("DLP allow")).toHaveLength(2);
  fireEvent.click(within(tabs).getByRole("tab", { name: /Slack/ }));
  expect(await screen.findByText("Slack body for the incident workflow.")).toBeInTheDocument();
});

it("activates the built-in default with version 0", async () => {
  const fetchMock = mockApi(write, handler);
  renderSettings("/settings/templates/system.workflow_notify");
  const body = await bodyField();
  fireEvent.input(body, { target: { value: "Unsaved {{ issue.short_id }}" } });
  fireEvent.click(await screen.findByRole("button", { name: "Activate Built-in default" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`${base}/activate`, expect.objectContaining({ method: "POST", body: JSON.stringify({ version: 0 }) })));
  expect(await screen.findByText("Built-in default is active.")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input) === base).length).toBeGreaterThanOrEqual(2));
  await settle();
  expect(body.value).toBe("Unsaved {{ issue.short_id }}");
});

it("loads the text of another version when it is opened", async () => {
  mockApi(write, handler);
  renderSettings("/settings/templates/system.workflow_notify");
  const body = await bodyField();
  fireEvent.click(screen.getByRole("button", { name: "Open Built-in default" }));
  await waitFor(() => expect(body.value).toBe("A masked Telescreen notification is available."));
  expect((screen.getByLabelText("Subject") as HTMLTextAreaElement).value).toBe("Workflow update");
});

it("saves a new inactive version and inserts allowed variables", async () => {
  const fetchMock = mockApi(write, handler);
  renderSettings("/settings/templates/system.workflow_notify");
  const body = await bodyField();
  fireEvent.input(body, { target: { value: "Run " } });
  body.setSelectionRange(4, 4);
  fireEvent.focus(body);
  fireEvent.click(screen.getByRole("button", { name: "workflow.name" }));
  expect(body.value).toBe("Run {{ workflow.name }}");
  fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));
  expect(await screen.findByText("Saved as version 2 (inactive)")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input) === base).length).toBeGreaterThanOrEqual(2));
  await settle();
  expect(screen.getByText("Saved as version 2 (inactive)")).toBeInTheDocument();
  expect(body.value).toBe("Run {{ workflow.name }}");
  expect(fetchMock.mock.calls.filter(([input]) => String(input) === `${base}/versions/1`)).toHaveLength(1);
  const call = fetchMock.mock.calls.find(([input, init]) => String(input) === "/api/notification-templates" && init?.method === "POST");
  expect(requestBody(call?.[1])).toEqual({ name: "system.workflow_notify", event_kind: "workflow_notify", subject: versionOne.subject, body: "Run {{ workflow.name }}" });
  const stored = Array.from({ length: localStorage.length }, (_, index) => localStorage.getItem(localStorage.key(index) ?? "") ?? "");
  expect(stored.some((value) => value.includes("Run {{"))).toBe(false);
});

it("hides save and activate without templates.write", async () => {
  const fetchMock = mockApi(["templates.read"], handler);
  renderSettings("/settings/templates/system.workflow_notify");
  await bodyField();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/auth/me", expect.anything()));
  await settle();
  expect(screen.queryByRole("button", { name: "Save as new version" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Activate Built-in default" })).toBeNull();
  expect(screen.getByRole("button", { name: "Validate" })).toBeInTheDocument();
});

it("loads the second template's text after navigating between template names", async () => {
  mockApi(write, (url, init) => {
    if (url === "/api/notification-templates/workflow.review") return Response.json({ ...detail, name: "workflow.review", builtin: false, active_version: 1, versions: [{ version: 1, active: true, created_by: null, created_at: null }] });
    if (url === "/api/notification-templates/workflow.review/versions/1") return Response.json({ ...versionOne, name: "workflow.review", body: "{{ workflow.name }} needs review." });
    return handler(url, init);
  });
  const history = createMemoryHistory();
  history.set({ value: "/settings/templates/system.workflow_notify" });
  render(() => <MemoryRouter history={history}><Route path="/settings/:id/:sub" component={SettingsPage} /></MemoryRouter>);
  await bodyField();
  history.set({ value: "/settings/templates/workflow.review" });
  await waitFor(() => expect((screen.getByLabelText("Body") as HTMLTextAreaElement).value).toBe("{{ workflow.name }} needs review."));
});
