import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
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
  expect(sessionStorage.getItem("telescreen.token")).toBe("private-token-value");
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
  fireEvent.click(screen.getByRole("button", { name: "Set members" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/teams/oncall/members", expect.objectContaining({ method: "PUT", body: JSON.stringify({ handles: ["alice", "bob"] }) })));
});

it("shows the API error when setting team members fails", async () => {
  const responses = initialSettingsResponses();
  responses.push(new Response(JSON.stringify({ error: { code: "internal_error", detail: "failed" } }), { status: 500 }));
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => responses.shift() ?? new Response());
  render(() => <SettingsPage />);
  await screen.findByText("On call");
  fireEvent.click(screen.getByRole("button", { name: "Set members" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("internal_error");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/teams/oncall/members", expect.objectContaining({ method: "PUT" })));
});
