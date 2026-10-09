import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { createMemoryHistory, MemoryRouter, Route, useParams } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowEditor } from "./WorkflowEditor";

const version = { id: "wf-01", name: "incident", version: 1, source_format: "yaml", source_text: "apiVersion: telescreen.workflow/v1", compiled_json: "{}", active: false };
const versionRef = { id: "wf-01", name: "incident", version: 1, active: false };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function okResponse(url: string, init?: RequestInit): Response {
  if (url.endsWith("/schema")) return new Response("{\"type\":\"object\"}", { status: 200 });
  if (url.endsWith("/validate")) return Response.json({ valid: false, errors: [{ path: "$.steps[0]", code: "required" }] });
  if (url.endsWith("/activate")) return Response.json({ ...versionRef, active: true });
  if (url.endsWith("/versions/1")) return Response.json(version);
  if (url.endsWith("/versions/2")) return Response.json({ ...version, version: 2, active: false });
  if (url.endsWith("/api/workflows") && init?.method === "POST") return Response.json({ ...versionRef, version: 2 });
  if (url.endsWith("/runs") && init?.method === "POST") return Response.json({ run_id: "run-01" }, { status: 201 });
  if (url.endsWith("/api/workflows/incident")) return Response.json({ items: [versionRef] });
  if (url.endsWith("/api/workflows")) return Response.json({ items: [versionRef] });
  return Response.json({ items: [versionRef] });
}

function renderEditor(name = "incident"): void {
  render(() => <MemoryRouter><Route path="*" component={() => <WorkflowEditor name={name} />} /></MemoryRouter>);
}

describe("WorkflowEditor", () => {
  it("keeps the editor usable when the workflow versions request returns a 404", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/workflows/missing") return Response.json({ error: { code: "not_found", detail: "workflow not found" } }, { status: 404 });
      return okResponse(String(input), init);
    }));
    renderEditor("missing");
    expect(await screen.findByRole("alert")).toHaveTextContent("not_found");
    expect(screen.getByLabelText("Workflow source")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start run" })).toBeDisabled();
  });

  it("renders validation paths returned by the API", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => okResponse(String(input), init)));
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: "Validate" }));
    await waitFor(() => expect(document.querySelector(".validation-errors")?.textContent).toContain("$.steps[0]: required"));
  });

  it("saves the source format and exact source text", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => okResponse(String(input), init));
    vi.stubGlobal("fetch", fetchMock);
    renderEditor();
    const text = "apiVersion: telescreen.workflow/v1\nkind: Workflow";
    fireEvent.input(await screen.findByLabelText("Workflow source"), { target: { value: text } });
    fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/workflows" && init?.method === "POST" && init.body === JSON.stringify({ source_format: "yaml", source_text: text }))).toBe(true));
  });

  it("activates the selected version at the named workflow URL", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => okResponse(String(input), init));
    vi.stubGlobal("fetch", fetchMock);
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: "Activate" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/workflows/incident/activate" && init?.method === "POST" && init.body === JSON.stringify({ version: 1 }))).toBe(true));
  });

  it("does not echo rejected sensitive source text", async () => {
    const sensitiveSource = "private-source-sample";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/workflows" && init?.method === "POST") return Response.json({ error: { code: "raw_value_detected", detail: sensitiveSource } }, { status: 422 });
      return okResponse(String(input), init);
    }));
    renderEditor();
    fireEvent.input(await screen.findByLabelText("Workflow source"), { target: { value: sensitiveSource } });
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/workflows" && init?.method === "POST")).toBe(true));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Source contains sensitive values."));
    expect(screen.queryByText(sensitiveSource)).toBeNull();
  });

  it("renders validation paths returned by a create 422", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/workflows" && init?.method === "POST") return Response.json({ valid: false, errors: [{ path: "$.steps[0].id", code: "required" }] }, { status: 422 });
      return okResponse(String(input), init);
    }));
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: "Save as new version" }));
    await waitFor(() => expect(document.querySelector(".validation-errors li")?.textContent).toBe("$.steps[0].id: required"));
  });

  it("shows the graph fallback for invalid workflow text", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => okResponse(String(input), init)));
    renderEditor();
    fireEvent.input(await screen.findByLabelText("Workflow source"), { target: { value: "steps: [" } });
    expect(screen.getByText("Fix validation errors to preview")).toBeInTheDocument();
  });

  it("starts a run and navigates to its detail", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/versions/1")) return Response.json({ ...version, active: true });
      if (url.endsWith("/runs") && init?.method === "POST") return Response.json({ run_id: "run-01" }, { status: 201 });
      if (url.endsWith("/api/workflows/incident")) return Response.json({ items: [{ ...versionRef, active: true }] });
      return okResponse(url, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    const history = createMemoryHistory();
    history.set({ value: "/workflows/incident" });
    render(() => <MemoryRouter history={history}>
      <Route path="/workflows/:name" component={() => <WorkflowEditor name="incident" />} />
      <Route path="/runs/:id" component={() => <p>Run page {useParams<{ id: string }>().id}</p>} />
    </MemoryRouter>);
    const startButton = await screen.findByRole("button", { name: "Start run" });
    await waitFor(() => expect(startButton).not.toBeDisabled());
    fireEvent.click(startButton);
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/workflows/incident/runs" && init?.method === "POST")).toBe(true));
    expect(await screen.findByText('Run page run-01')).toBeInTheDocument();
  });
});
