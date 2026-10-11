import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import { getSession } from "../../auth/session";
import SettingsPage from "./index";

afterEach(() => { cleanup(); sessionStorage.clear(); document.documentElement.removeAttribute("data-theme"); vi.restoreAllMocks(); });

function initialSettingsResponses(): Response[] {
  return [
    new Response(JSON.stringify({ items: [], next_cursor: null })),
    new Response(JSON.stringify({ items: [{ id: "team-1", slug: "oncall", name: "On call", email: null, slack_channel: "#ops" }], next_cursor: null })),
    new Response(JSON.stringify({ items: [{ id: "collector-1", name: "Drive", kind: "gdrive", trigger_mode: "schedule", cron: null, sweep_cron: null, selector_json: "{}", credential_ref: "env:DRIVE_TOKEN", enabled: true }], next_cursor: null })),
    new Response(JSON.stringify({ status: "ok" })),
  ];
}

it("stores the token in sessionStorage without showing it again", async () => {
  const responses = initialSettingsResponses();
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => responses.shift() ?? new Response());
  render(() => <SettingsPage />);
  fireEvent.input(await screen.findByLabelText("Bearer token"), { target: { value: "private-token-value" } });
  fireEvent.click(screen.getByRole("button", { name: "Save token" }));
  expect(getSession()?.access_token).toBe("private-token-value");
  expect(await screen.findByText("Token set")).toBeInTheDocument();
  expect(screen.queryByText("private-token-value")).toBeNull();
  expect(await screen.findByText("env:DRIVE_TOKEN")).toBeInTheDocument();
});

it("applies the selected theme and sends team member handles", async () => {
  const responses = initialSettingsResponses();
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => responses.shift() ?? new Response(null, { status: 204 }));
  render(() => <SettingsPage />);
  await screen.findByText("On call");
  fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "dark" } });
  expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  fireEvent.input(screen.getByLabelText("Member handles"), { target: { value: "alice, bob" } });
  fireEvent.click(screen.getByRole("button", { name: "Save team changes" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/teams/oncall/members", expect.objectContaining({ method: "PUT", body: JSON.stringify({ handles: ["alice", "bob"] }) })));
});

it("shows the API error when setting team members fails", async () => {
  const responses = initialSettingsResponses();
  responses.push(new Response(JSON.stringify({ error: { code: "internal_error", detail: "failed" } }), { status: 500 }));
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => responses.shift() ?? new Response());
  render(() => <SettingsPage />);
  await screen.findByText("On call");
  fireEvent.input(screen.getByLabelText("Member handles"), { target: { value: "alice" } });
  fireEvent.click(screen.getByRole("button", { name: "Save team changes" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("internal_error");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/teams/oncall/members", expect.objectContaining({ method: "PUT" })));
});

it("adds an identity without displaying the submitted value and saves the team lead", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/members" && init?.method !== "POST") return new Response(JSON.stringify({ items: [{ id: "mem-1", handle: "alex", display_name: "Alex", email: null, slack_user_id: null, active: true }], next_cursor: null }));
    if (url === "/api/teams") return new Response(JSON.stringify({ items: [{ id: "team-1", slug: "response", name: "Response", email: null, slack_channel: null, lead: null }], next_cursor: null }));
    if (url === "/api/collectors") return new Response(JSON.stringify({ items: [], next_cursor: null }));
    if (url === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
    if (url === "/api/members/alex/identities" && init?.method === "POST") return new Response(JSON.stringify({ id: "alex:identity-1", kind: "email", origin: "manual", key_prefix: "hmac:v1:abc", created_at: "2026-10-10T00:00:00Z" }));
    if (url === "/api/members/alex/identities") return new Response(JSON.stringify({ items: [{ id: "alex:identity-1", kind: "email", origin: "manual", key_prefix: "hmac:v1:abc", created_at: "2026-10-10T00:00:00Z" }], next_cursor: null }));
    return new Response(null, { status: 204 });
  });
  render(() => <SettingsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Identities" }));
  fireEvent.input(await screen.findByLabelText("Value"), { target: { value: "identity@example.invalid" } });
  fireEvent.click(screen.getByRole("button", { name: "Add identity" }));
  expect(await screen.findByText(/hmac:v1:abc/)).toBeInTheDocument();
  expect(screen.queryByDisplayValue("identity@example.invalid")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.change(screen.getByLabelText("Team lead for Response"), { target: { value: "alex" } });
  fireEvent.click(screen.getByRole("button", { name: "Save team changes" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/teams/response/lead", expect.objectContaining({ method: "PUT", body: JSON.stringify({ lead: "alex" }) })));
  expect(fetchMock).not.toHaveBeenCalledWith("/api/teams/response/members", expect.anything());
});
