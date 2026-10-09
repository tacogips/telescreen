import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { MemoryRouter, Route } from "@solidjs/router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowEditor } from "./WorkflowEditor";

const version = { name: "incident", version: 1, source_format: "yaml", source_text: "apiVersion: telescreen.workflow/v1", created_at: "2026-10-08T00:00:00Z", active: false };
const workflow = { name: "incident", title: "Incident", description: "Manual trigger", active_version: null, latest_version: 1, updated_at: "2026-10-08T00:00:00Z" };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function okResponse(url: string, init?: RequestInit): Response {
  if (url.endsWith("/schema")) return Response.json({ schema: "/api/workflows/schema" });
  if (url.endsWith("/validate")) return Response.json({ valid: false, errors: [{ path: "$.steps[0]", code: "required" }] });
  if (url.endsWith("/activate")) return Response.json(workflow);
  if (url.endsWith("/versions/1")) return Response.json(version);
  if (url.endsWith("/api/workflows") && init?.method === "POST") return Response.json({ ...version, version: 2 });
  return Response.json(workflow);
}

function renderEditor(): void {
  render(() => <MemoryRouter><Route path="*" component={() => <WorkflowEditor name="incident" />} /></MemoryRouter>);
}

describe("WorkflowEditor", () => {
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
});
