import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi, type MockInstance } from "vitest";
import { setSession } from "../../auth/session";
import ProfilePage from "./ProfilePage";

afterEach(() => { cleanup(); sessionStorage.clear(); document.documentElement.removeAttribute("data-theme"); vi.restoreAllMocks(); });

function setup(): MockInstance<typeof fetch> {
  setSession({ kind: "token", access_token: "session" });
  const history = createMemoryHistory(); history.set({ value: "/settings/profile" });
  const settings = ["assigned", "mentions", "subscribed", "alerts", "workflow_tasks", "weekly_report"].map((category) => ({ category, stored: category === "alerts" || category === "workflow_tasks" ? null : "email", effective: "email", preference: "email", available: { email: true, slack: category !== "mentions" } }));
  const views = [{ id: "view-1", name: "Open issues", query: "is:unresolved", sort: "last_seen", visibility: "personal", starred: true, is_default: true }];
  let tokens = [{ id: "token-1", label: "Reports", scopes: ["read"], preview: "ts_••••1234", created_at: "2026-10-01T00:00:00Z" }];
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/auth/me") return Response.json({ kind: "token", member: { handle: "viewer", display_name: "View User" }, role: "viewer", permissions: ["profile.self", "views.read"] });
    if (url === "/api/auth/me/notification-settings" && init?.method === "PUT") return Response.json({ items: settings });
    if (url === "/api/auth/me/notification-settings") return Response.json({ items: settings });
    if (url === "/api/auth/me/tokens" && init?.method === "POST") { tokens = [...tokens, { id: "token-2", label: "new", scopes: ["read"], preview: "masked", created_at: "2026-10-13T00:00:00Z" }]; return Response.json({ id: "token-2", token: "one-time-secret" }, { status: 201 }); }
    if (url === "/api/auth/me/tokens") return Response.json({ items: tokens });
    if (url === "/api/views") return Response.json({ items: views });
    return new Response(null, { status: 204 });
  });
  render(() => <MemoryRouter history={history}><Route path="/settings/profile" component={ProfilePage} /></MemoryRouter>);
  return fetchMock;
}

it("saves As configured for alert rules and disables unavailable Slack", async () => {
  const fetchMock = setup();
  await screen.findByLabelText("Alert rules channel");
  const mentions = screen.getByLabelText("Mentions channel") as HTMLSelectElement;
  expect([...mentions.options].find((option) => option.value === "slack")).toBeDisabled();

  const alerts = screen.getByLabelText("Alert rules channel");
  expect(alerts).toHaveValue("as_configured");
  fireEvent.change(alerts, { target: { value: "email" } });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/auth/me/notification-settings", expect.objectContaining({ method: "PUT" })));
  let updates = fetchMock.mock.calls.filter(([input, init]) => String(input) === "/api/auth/me/notification-settings" && init?.method === "PUT");
  expect(JSON.parse(String(updates.at(-1)?.[1]?.body))).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ category: "alerts", stored: "email" })]) });

  fireEvent.change(alerts, { target: { value: "as_configured" } });
  await waitFor(() => {
    updates = fetchMock.mock.calls.filter(([input, init]) => String(input) === "/api/auth/me/notification-settings" && init?.method === "PUT");
    expect(updates).toHaveLength(2);
  });
  expect(alerts).toHaveValue("as_configured");
  expect(JSON.parse(String(updates.at(-1)?.[1]?.body))).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ category: "alerts", stored: null })]) });
});

it("saves Slack for an available notification category", async () => {
  const fetchMock = setup();
  const subscribed = await screen.findByLabelText("Subscribed issues channel");
  fireEvent.change(subscribed, { target: { value: "slack" } });
  await waitFor(() => {
    const updates = fetchMock.mock.calls.filter(([input, init]) => String(input) === "/api/auth/me/notification-settings" && init?.method === "PUT");
    expect(updates).toHaveLength(1);
    expect(JSON.parse(String(updates.at(-1)?.[1]?.body))).toMatchObject({ items: expect.arrayContaining([expect.objectContaining({ category: "subscribed", stored: "slack" })]) });
  });
});

it("limits viewer scopes to read and clears the one-time token on dialog close", async () => {
  const fetchMock = setup();
  await screen.findByLabelText("Token label");
  expect(screen.getByLabelText("read")).toBeChecked();
  expect(screen.queryByLabelText("write")).toBeNull();
  fireEvent.input(screen.getByLabelText("Token label"), { target: { value: "Daily report" } });
  fireEvent.click(screen.getByRole("button", { name: "Create token" }));
  expect(await screen.findByText("one-time-secret")).toBeInTheDocument();
  const createCall = fetchMock.mock.calls.find(([input, init]) => String(input) === "/api/auth/me/tokens" && init?.method === "POST");
  expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({ label: "Daily report", scopes: ["read"] });
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByText("one-time-secret")).toBeNull();
});

it("sets a theme and provides the default view", async () => {
  const fetchMock = setup();
  await screen.findByLabelText("Theme");
  await waitFor(() => expect(screen.getByLabelText("Default view")).toHaveValue("view-1"));
  fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "dark" } });
  expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  expect(fetchMock).toHaveBeenCalledWith("/api/views", expect.objectContaining({ headers: expect.any(Headers) }));
});
