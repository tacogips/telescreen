import { cleanup, fireEvent, render, screen, waitFor, within } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi, type MockInstance } from "vitest";
import { setSession } from "../../auth/session";
import AlertsPage from "./index";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

const rules = [
  { id: "alert-01", name: "New high priority", enabled: true, trigger: "issue_created", query: "priority:high", action_interval_minutes: 60, muted_until: null, last_fired_at: "2026-10-11T09:00:00Z", fires_24h: 2 },
  { id: "alert-02", name: "Regressions", enabled: true, trigger: "issue_regressed", query: "is:regressed", action_interval_minutes: 30, muted_until: null, status: "muted" },
  { id: "alert-03", name: "Values increase", enabled: false, trigger: "values", query: "values:>3", action_interval_minutes: 120, muted_until: null },
];
let permissions: readonly string[] = [];
let validation: Readonly<{ valid: boolean; errors: readonly Readonly<{ field: string; code: string }>[] }> = { valid: true, errors: [] };
let created = false;
let createEnabled = true;
function requestBody(init: RequestInit | undefined): unknown { return typeof init?.body === "string" ? JSON.parse(init.body) as unknown : undefined; }
function setup(scopes: readonly string[], path: string): MockInstance<typeof fetch> {
  permissions = scopes; validation = { valid: true, errors: [] }; created = false; createEnabled = true;
  setSession({ kind: "token", access_token: "session" });
  const history = createMemoryHistory(); history.set({ value: path });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/auth/me") return Response.json({ kind: "token", member: { handle: "alex", display_name: "Alex Morgan" }, role: "responder", permissions });
    if (url === "/api/notification-templates") return Response.json({ items: [{ name: "alert.default", event_kind: "alert_fired" }] });
    if (url === "/api/workflows") return Response.json({ items: [{ id: "workflow-01", name: "triage", version: 1, active: true }] });
    if (url === "/api/alert-rules" && init?.method !== "POST") return Response.json({ items: rules });
    if (url === "/api/alert-rules/alert-01") return Response.json(rules[0]);
    if (url === "/api/alert-rules/alert-01/history") return Response.json({ items: Array.from({ length: 12 }, (_, index) => ({
      id: `fire-${index + 1}`, rule_id: "alert-01", issue_id: `TELE-${index + 1}`,
      outcome: index === 0 ? "delivered" : index === 1 ? "suppressed_interval" : index === 2 ? "muted" : "delivered",
      at: `2026-10-11T09:${String(index).padStart(2, "0")}:00Z`,
    })), next_cursor: null });
    if (url === "/api/alert-rules/alert-01/mute") return Response.json({ ...rules[0], status: "muted" });
    if (url === "/api/alert-rules/validate") return Response.json(validation);
    if (url === "/api/alert-rules" && init?.method === "POST") { created = true; return Response.json({ id: "alert-new", enabled: createEnabled }, { status: 201 }); }
    if (url === "/api/alert-rules/alert-new/enable" || url === "/api/alert-rules/alert-new/disable") return Response.json({ id: "alert-new", enabled: url.endsWith("/enable") });
    return Response.json({ error: { code: "not_found", detail: "Not found." } }, { status: 404 });
  });
  render(() => <MemoryRouter history={history}><Route path="/alerts" component={AlertsPage} /><Route path="/alerts/:id" component={AlertsPage} /></MemoryRouter>);
  return vi.spyOn(globalThis, "fetch");
}

it("shows three rules and hides create from a viewer", async () => {
  setup(["alerts.read"], "/alerts");
  expect(await screen.findByText("New high priority")).toBeInTheDocument();
  expect(screen.getByText("Regressions")).toBeInTheDocument();
  expect(screen.getByText("Values increase")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New alert rule" })).toBeNull();
});

it("gives a responder mute actions without edit access", async () => {
  const fetchMock = setup(["alerts.read", "alerts.mute"], "/alerts");
  const row = await screen.findByText("New high priority");
  const tableRow = row.closest("tr") as HTMLElement;
  expect(within(tableRow).getByRole("button", { name: "Mute 1h" })).toBeInTheDocument();
  expect(within(tableRow).queryByText("Edit")).toBeNull();
  fireEvent.click(within(tableRow).getByRole("button", { name: "Mute 1h" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/alert-rules/alert-01/mute" && typeof init?.body === "string" && init.body.includes('"minutes":60'))).toBe(true));
});

it("shows a tab named History with fire outcomes", async () => {
  setup(["alerts.read"], "/alerts/alert-01");
  expect(await screen.findByRole("tab", { name: "History" })).toBeInTheDocument();
  expect((await screen.findAllByText("delivered")).length).toBeGreaterThan(0);
  expect(screen.getByText("suppressed_interval")).toBeInTheDocument();
  expect(screen.getByText("muted")).toBeInTheDocument();
});

it("renders every fire returned on the history page", async () => {
  setup(["alerts.read"], "/alerts/alert-01");
  await screen.findByRole("tab", { name: "History" });
  const history = screen.getByRole("region", { name: "Alert history" });
  await waitFor(() => expect(within(history).getAllByRole("link")).toHaveLength(12));
  expect(within(history).getByRole("link", { name: "TELE-12" })).toBeInTheDocument();
});

it("validates before create and shows static field error codes without echoing values", async () => {
  const fetchMock = setup(["alerts.read", "alerts.write"], "/alerts/new");
  validation = { valid: false, errors: [{ field: "/trigger/threshold", code: "threshold_required" }] };
  await screen.findByRole("textbox", { name: "Rule name" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Create alert rule" })).toBeEnabled());
  fireEvent.input(screen.getByLabelText("Rule name"), { target: { value: "High exposure" } });
  fireEvent.change(screen.getByLabelText("Trigger"), { target: { value: "frequency" } });
  fireEvent.click(screen.getByRole("button", { name: "Create alert rule" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/alert-rules/validate", expect.objectContaining({ method: "POST" })));
  expect(await screen.findByText("Enter a threshold.")).toBeInTheDocument();
  expect(created).toBe(false);
  const validationCall = fetchMock.mock.calls.find(([input]) => String(input) === "/api/alert-rules/validate");
  expect(requestBody(validationCall?.[1])).toMatchObject({ trigger: { kind: "frequency" }, name: "High exposure" });
});

it("shows p08 validation errors and a safe fallback without creating or echoing values", async () => {
  const fetchMock = setup(["alerts.read", "alerts.write"], "/alerts/new");
  const rawRecipient = "alex-secret-value";
  validation = { valid: false, errors: [
    { field: "/actions/start_workflow", code: "duplicate_trigger" },
    { field: "/actions/targets/0/recipients/0", code: "invalid_recipient" },
    { field: "/actions/targets/0/channels/0", code: "invalid_value" },
    { field: "/unrecognized", code: "invalid_value" },
  ] };
  await screen.findByRole("textbox", { name: "Rule name" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Create alert rule" })).toBeEnabled());
  fireEvent.input(screen.getByLabelText("Rule name"), { target: { value: "critical-rule" } });
  fireEvent.input(screen.getByLabelText("Recipient 1"), { target: { value: rawRecipient } });
  fireEvent.change(screen.getByLabelText("Start workflow"), { target: { value: "triage" } });
  fireEvent.click(screen.getByRole("button", { name: "Create alert rule" }));

  expect(await screen.findByText("Choose a workflow with a different trigger.")).toBeInTheDocument();
  expect(screen.getByText("Enter a valid recipient reference.")).toBeInTheDocument();
  expect(screen.getByText("Choose a valid value.")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Some fields need attention.");
  const renderedErrors = [...document.querySelectorAll(".field-error")].map((element) => element.textContent ?? "").join(" ");
  expect(renderedErrors).not.toContain(rawRecipient);
  expect(created).toBe(false);
  expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/alert-rules/validate")).toBe(true);
});

it("disables a new rule when the server creates it enabled", async () => {
  const fetchMock = setup(["alerts.read", "alerts.write"], "/alerts/new");
  createEnabled = true;
  await screen.findByRole("textbox", { name: "Rule name" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Create alert rule" })).toBeEnabled());
  fireEvent.input(screen.getByLabelText("Rule name"), { target: { value: "critical-rule" } });
  fireEvent.click(screen.getByLabelText("Enabled"));
  expect(screen.getByLabelText("Enabled")).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Create alert rule" }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/alert-rules/alert-new/disable")).toHaveLength(1));
  expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/alert-rules/alert-new/enable")).toBe(false);
});

it("enables a new rule when the server creates it disabled", async () => {
  const fetchMock = setup(["alerts.read", "alerts.write"], "/alerts/new");
  createEnabled = false;
  await screen.findByRole("textbox", { name: "Rule name" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Create alert rule" })).toBeEnabled());
  fireEvent.input(screen.getByLabelText("Rule name"), { target: { value: "critical-rule" } });
  const enabled = screen.getByLabelText("Enabled");
  fireEvent.click(enabled);
  fireEvent.click(enabled);
  expect(enabled).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Create alert rule" }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/alert-rules/alert-new/enable")).toHaveLength(1));
  expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/alert-rules/alert-new/disable")).toBe(false);
});
