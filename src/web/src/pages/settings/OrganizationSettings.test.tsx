import { cleanup, fireEvent, screen, waitFor, within } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import { mockApi, renderSettings } from "./testUtils";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

it("shows integration tests only with permission and renders a safe test result", async () => {
  const fetchMock = mockApi(["integrations.read", "integrations.test"], (url, init) => {
    if (url === "/api/integrations") return Response.json({ items: [{ kind: "slack", configured: true, status: "ok", last_check_at: null, last_check_ok: true, error_code: null, docs_path: "README.md#slack" }] });
    if (url === "/api/integrations/slack/test" && init?.method === "POST") return Response.json({ status: "ok", detail: "private transport diagnostic" });
    return undefined;
  });
  renderSettings("/settings/integrations");
  fireEvent.click(await screen.findByRole("button", { name: "Test" }));
  expect(await screen.findByText("Connection test succeeded.")).toBeInTheDocument();
  expect(screen.queryByText("private transport diagnostic")).toBeNull();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/integrations/slack/test", expect.objectContaining({ method: "POST" })));
});

it("hides the integration Test action when integrations.test is absent", async () => {
  const fetchMock = mockApi(["integrations.read"], (url) => url === "/api/integrations" ? Response.json({ items: [{ kind: "email", configured: true, status: "ready" }] }) : undefined);
  renderSettings("/settings/integrations");
  expect(await screen.findByText("email")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Test" })).toBeNull();
  expect(fetchMock).not.toHaveBeenCalledWith("/api/integrations/email/test", expect.anything());
});

it("creates an admin token and shows the returned secret once", async () => {
  const fetchMock = mockApi(["tokens.read", "tokens.write", "users.read"], (url, init) => {
    if (url === "/api/tokens" && init?.method === "POST") return Response.json({ id: "token-2", token: "mock-token-value" });
    if (url === "/api/tokens") return Response.json({ items: [] });
    if (url === "/api/members") return Response.json({ items: [{ handle: "alex", display_name: "Alex Morgan" }] });
    return undefined;
  });
  renderSettings("/settings/tokens");
  const tokenLabel = await screen.findByLabelText("Token label");
  const scopeGroup = within(screen.getByRole("group", { name: "Scopes" }));
  expect(scopeGroup.getAllByRole("checkbox").map((checkbox) => checkbox.closest("label")?.textContent?.trim())).toEqual(["read", "write", "admin", "ingest"]);
  expect(scopeGroup.queryByLabelText("issues.read")).toBeNull();
  fireEvent.input(tokenLabel, { target: { value: "deploy" } });
  fireEvent.click(screen.getByRole("button", { name: "Create token" }));
  expect(await screen.findByText("mock-token-value")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/tokens", expect.objectContaining({ method: "POST", body: JSON.stringify({ label: "deploy", scopes: ["read"] }) })));
});

it("reorders ownership rules with the full new order and reports matches", async () => {
  const fetchMock = mockApi(["ownership.read", "ownership.write", "users.read"], (url, init) => {
    if (url === "/api/ownership-rules" && init?.method !== "POST") return Response.json({ items: [
      { id: "rule-1", source: "slack", pattern: "team/*", team: "engineering", enabled: true, position: 0 },
      { id: "rule-2", source: "github", pattern: "org/repo/**", team: "security", enabled: true, position: 1 },
    ] });
    if (url === "/api/teams") return Response.json({ items: [{ id: "team-01", slug: "engineering", name: "Engineering" }, { id: "team-02", slug: "security", name: "Security" }] });
    if (url === "/api/ownership-rules/reorder") return new Response(null, { status: 204 });
    if (url === "/api/ownership-rules/test") return Response.json({ rule_id: "rule-1", team: "engineering" });
    return undefined;
  });
  renderSettings("/settings/ownership");
  fireEvent.click(await screen.findByRole("button", { name: "Move team/* down" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/ownership-rules/reorder", expect.objectContaining({ body: JSON.stringify({ ids: ["rule-2", "rule-1"] }) })));
  fireEvent.input(screen.getByLabelText("Display locator"), { target: { value: "team/service" } });
  fireEvent.click(screen.getByRole("button", { name: "Test locator" }));
  expect(await screen.findByText("Matched team: engineering")).toBeInTheDocument();
});

it("creates an enabled ownership rule using the selected team slug", async () => {
  const fetchMock = mockApi(["ownership.read", "ownership.write", "users.read"], (url, init) => {
    if (url === "/api/ownership-rules" && init?.method === "POST") return Response.json({ id: "rule-new", position: 0, source: "slack", pattern: "org/service/**", team: "engineering", enabled: true });
    if (url === "/api/ownership-rules") return Response.json({ items: [] });
    if (url === "/api/teams") return Response.json({ items: [{ id: "team-01", slug: "engineering", name: "Engineering" }] });
    return undefined;
  });
  renderSettings("/settings/ownership");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/teams", expect.anything()));
  fireEvent.click(await screen.findByRole("button", { name: "Add rule" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Source"), { target: { value: "slack" } });
  fireEvent.input(screen.getByLabelText("Pattern"), { target: { value: "org/service/**" } });
  fireEvent.click(screen.getByRole("button", { name: "Save rule" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/ownership-rules", expect.objectContaining({ method: "POST", body: JSON.stringify({ source: "slack", pattern: "org/service/**", team: "engineering", enabled: true }) })));
});

it("does not echo a rejected ownership test value", async () => {
  mockApi(["ownership.read", "users.read"], (url) => {
    if (url === "/api/ownership-rules") return Response.json({ items: [] });
    if (url === "/api/teams") return Response.json({ items: [] });
    if (url === "/api/ownership-rules/test") return Response.json({ error: { code: "raw_value_detected", detail: "Rejected input." } }, { status: 422 });
    return undefined;
  });
  renderSettings("/settings/ownership");
  fireEvent.input(await screen.findByLabelText("Display locator"), { target: { value: "sample-sensitive-value" } });
  fireEvent.click(screen.getByRole("button", { name: "Test locator" }));
  expect(await screen.findByText("The value looks like a secret and was not saved")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("sample-sensitive-value")).toBeNull();
});
