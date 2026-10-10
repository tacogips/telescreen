import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import EscalationPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("lists backend-shaped policies and renders their tier fields", async () => {
  const tiersJson = JSON.stringify({ name: "Primary", tiers: [{ after: "PT1H", assign_to: "team:triage", notify: ["member:oncall"], channels: ["email"] }] });
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: "esc-01", name: "Primary", tiers_json: tiersJson }], next_cursor: null })));
  render(() => <EscalationPage />);
  expect(await screen.findByText("Tier 1 · PT1H · team:triage")).toBeInTheDocument();
  expect(screen.getByText("team:triage, member:oncall")).toBeInTheDocument();
  expect(screen.queryByText("Invalid policy")).toBeNull();
});

it("renders a configured minimum risk and accepts it in the JSON editor", async () => {
  const tiersJson = JSON.stringify({ name: "Risk policy", tiers: [{ after: "PT1H", assign_to: "team:triage", notify: [], channels: ["email"], min_risk: 70 }] });
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: "esc-risk", name: "Risk policy", tiers_json: tiersJson }], next_cursor: null })));
  render(() => <EscalationPage />);
  expect(await screen.findByText("Tier 1 · PT1H · team:triage · min risk 70")).toBeInTheDocument();
});

it("creates a policy with a backend-shaped JSON document", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null }))).mockResolvedValueOnce(new Response(JSON.stringify({ id: "esc-01", name: "Primary", tiers_json: "{}" }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  render(() => <EscalationPage />);
  fireEvent.click(screen.getByRole("button", { name: "New policy" }));
  fireEvent.input(screen.getByLabelText("Policy name"), { target: { value: "Primary" } });
  fireEvent.input(screen.getByLabelText("JSON source"), { target: { value: '{"name":"Draft","tiers":[{"after":"PT10M","assign_to":"team:triage","notify":["member:lead"],"channels":["email","slack"]}]}' } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/escalation-policies", expect.objectContaining({ method: "POST", body: expect.stringContaining('"source_format":"json"') })));
  const postCall = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/escalation-policies" && init?.method === "POST");
  expect(postCall).toBeDefined();
  const postBody = JSON.parse(String(postCall?.[1]?.body)) as { name: string; tiers_json: string; source_format: string };
  expect(postBody).toEqual({ name: "Primary", tiers_json: '{"name":"Primary","tiers":[{"after":"PT10M","assign_to":"team:triage","notify":["member:lead"],"channels":["email","slack"]}]}', source_format: "json" });
});

it("shows a local validation path only for malformed JSON", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  render(() => <EscalationPage />);
  fireEvent.click(screen.getByRole("button", { name: "New policy" }));
  fireEvent.input(screen.getByLabelText("Policy name"), { target: { value: "Primary" } });
  fireEvent.input(screen.getByLabelText("JSON source"), { target: { value: "{" } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  expect(await screen.findByText(/invalid_json/)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("lists local validation paths for an invalid document without sending a request", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  render(() => <EscalationPage />);
  fireEvent.click(screen.getByRole("button", { name: "New policy" }));
  fireEvent.input(screen.getByLabelText("Policy name"), { target: { value: "Primary" } });
  fireEvent.input(screen.getByLabelText("JSON source"), { target: { value: '{"name":"Draft","tiers":[{"after":"soon","assign_to":"triage","notify":[],"channels":["fax"]}]}' } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  expect(await screen.findByText("/tiers/0/after")).toBeInTheDocument();
  expect(screen.getByText("/tiers/0/after").closest("li")).toHaveTextContent("invalid_duration");
  expect(screen.getByText("/tiers/0/assign_to").closest("li")).toHaveTextContent("invalid_value");
  expect(screen.getByText("/tiers/0/channels/0").closest("li")).toHaveTextContent("invalid_value");
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("shows the real backend rejection banner and keeps the editor open", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "workflow_invalid", detail: "workflow definition is invalid" } }), { status: 500 }));
  render(() => <EscalationPage />);
  await screen.findByText("No escalation policies");
  fireEvent.click(screen.getByRole("button", { name: "New policy" }));
  fireEvent.input(screen.getByLabelText("Policy name"), { target: { value: "Primary" } });
  fireEvent.input(screen.getByLabelText("JSON source"), { target: { value: '{"name":"Draft","tiers":[{"after":"PT10M","assign_to":"team:triage","notify":[],"channels":["email"]}]}' } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/escalation-policies");
  expect(fetchMock.mock.calls[1]?.[1]?.method).toBe("POST");
  expect(await screen.findByRole("alert")).toHaveTextContent("workflow_invalid");
  expect(screen.getByRole("button", { name: "Save policy" })).toBeInTheDocument();
  expect(screen.queryByText("Escalation policy saved")).toBeNull();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("updates an existing backend-shaped policy through PUT", async () => {
  const loadedJson = '{"name":"Primary","tiers":[{"after":"PT10M","assign_to":"team:triage","notify":[],"channels":["email"]}]}';
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: "esc-01", name: "Primary", tiers_json: loadedJson }], next_cursor: null }))).mockResolvedValueOnce(new Response(JSON.stringify({ id: "esc-01", name: "Primary", tiers_json: loadedJson }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  render(() => <EscalationPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.input(screen.getByLabelText("JSON source"), { target: { value: '{"name":"Different name","tiers":[{"after":"PT15M","assign_to":"team:oncall","notify":["member:lead"],"channels":["slack"]}]}' } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/escalation-policies/Primary", expect.objectContaining({ method: "PUT", body: expect.stringContaining('"source_format":"json"') })));
  const putCall = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/escalation-policies/Primary" && init?.method === "PUT");
  expect(putCall).toBeDefined();
  const putBody = JSON.parse(String(putCall?.[1]?.body)) as { tiers_json: string; source_format: string };
  expect(putBody).toEqual({ tiers_json: '{"name":"Primary","tiers":[{"after":"PT15M","assign_to":"team:oncall","notify":["member:lead"],"channels":["slack"]}]}', source_format: "json" });
});

it("renders malformed stored documents defensively", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: "esc-01", name: "Broken policy", tiers_json: "{" }], next_cursor: null })));
  render(() => <EscalationPage />);
  expect(await screen.findByText("Invalid policy")).toBeInTheDocument();
});
