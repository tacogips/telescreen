import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route } from "@solidjs/router";
import { afterEach, expect, it, vi } from "vitest";
import PoliciesPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderPolicy(path: string): void {
  const history = createMemoryHistory();
  history.set({ value: path });
  render(() => <MemoryRouter history={history}><Route path="/policies" component={PoliciesPage} /><Route path="/policies/:name" component={PoliciesPage} /></MemoryRouter>);
}

const policy = { name: "External policy", active_version: 1, effect: "violate", enabled: true, owner_team: "team:response" };
const version = { name: "External policy", version: 1, source_format: "yaml", source_text: "name: External policy\neffect: violate", active: true };

it("lists policies and validates editor input with server paths", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/policies") return new Response(JSON.stringify({ items: [policy], next_cursor: null }));
    if (init?.method === "POST") return new Response(JSON.stringify({ valid: false, errors: [{ path: "/conditions/0/exposure", code: "invalid_value" }] }));
    return new Response(JSON.stringify({ valid: true, errors: [] }));
  });
  renderPolicy("/policies");
  expect(await screen.findByText("External policy")).toBeInTheDocument();
  cleanup();
  renderPolicy("/policies/new");
  fireEvent.input(await screen.findByLabelText("Policy source"), { target: { value: "name: Draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Validate" }));
  expect(await screen.findByText("/conditions/0/exposure")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/policies/validate", expect.objectContaining({ method: "POST" })));
});

it("activates a selected version", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/api/policies") return new Response(JSON.stringify({ items: [policy], next_cursor: null }));
    if (url === "/api/policies/External%20policy") return new Response(JSON.stringify({ name: policy.name, versions: [version, { ...version, version: 2, active: false }] }));
    if (url === "/api/policies/External%20policy/versions/2") return new Response(JSON.stringify({ ...version, version: 2, active: false }));
    if (url === "/api/policies/External%20policy/activate") return new Response(JSON.stringify({ ...policy, active_version: 2 }));
    return new Response(JSON.stringify({ items: [policy], next_cursor: null }));
  });
  renderPolicy("/policies/External%20policy");
  fireEvent.click(await screen.findByRole("button", { name: "Activate" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/policies/External%20policy/activate", expect.objectContaining({ method: "POST", body: JSON.stringify({ version: 2 }) })));
});

it("saves a policy as a new version", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/policies" && init?.method === "POST") return new Response(JSON.stringify({ ...version, version: 1 }));
    if (url === "/api/policies") return new Response(JSON.stringify({ items: [policy], next_cursor: null }));
    if (url === "/api/policies/External%20policy") return new Response(JSON.stringify({ name: policy.name, versions: [version] }));
    return new Response(JSON.stringify({ ...version, version: 1 }));
  });
  renderPolicy("/policies/new");
  fireEvent.input(await screen.findByLabelText("Policy source"), { target: { value: "name: External policy\neffect: violate" } });
  fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/policies", expect.objectContaining({ method: "POST", body: JSON.stringify({ source_format: "yaml", source_text: "name: External policy\neffect: violate" }) })));
});
