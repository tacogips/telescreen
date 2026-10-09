import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, WorkflowValidationError, apiFetch, auditApi, collectorsApi, evidenceApi, getToken, issuesApi, rawFetch, setToken, workflowsApi } from "./client";

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

describe("API wire contract", () => {
  it("uses global issue stats and its query parameters", async () => {
    const fetchSpy = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({ buckets: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    await issuesApi.stats({ bucket: "day", days: 14 });
    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe("/api/issues/stats?bucket=day&days=14");
  });

  it("returns the run_id from the workflow start endpoint", async () => {
    const fetchSpy = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ run_id: "run-01" }, { status: 201 }));
    vi.stubGlobal("fetch", fetchSpy);
    expect(await workflowsApi.startRun("incident", "iss-01")).toEqual({ run_id: "run-01" });
    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe("/api/workflows/incident/runs");
    expect(fetchSpy.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({ issue_id: "iss-01" }));
  });

  it("decodes workflow validation issues", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ valid: false, errors: [{ path: "$.steps[0]", code: "x" }] }), { status: 422 })));
    await expect(workflowsApi.create("json", "{}")).rejects.toMatchObject({ name: "WorkflowValidationError", errors: [{ path: "$.steps[0]", code: "x" }] } satisfies Partial<WorkflowValidationError>);
  });

  it("decodes only string evidence rule classes", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "evidence_rejected", detail: "evidence rejected", rule_classes: ["credential", 3] } }), { status: 422 })));
    await expect(evidenceApi.upload(new Blob(["safe"]), "iss-01", "notes.txt")).rejects.toMatchObject({ name: "ApiError", ruleClasses: ["credential"] } satisfies Partial<ApiError>);
  });

  it("normalizes endpoint item lists and keeps audit totals", async () => {
    const fetchSpy = vi.fn(async (input: RequestInfo | URL) => String(input).startsWith("/api/audit")
      ? new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 })
      : new Response(JSON.stringify({ items: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    expect((await collectorsApi.list()).next_cursor).toBeNull();
    expect(await auditApi.search()).toEqual({ items: [], total: 0 });
  });

  it("rawFetch sends bearer authorization and leaves non-2xx decoding to the caller", async () => {
    setToken("test-token");
    const fetchSpy = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response("missing", { status: 404 }));
    vi.stubGlobal("fetch", fetchSpy);
    const response = await rawFetch("/api/missing");
    expect(response.status).toBe(404);
    expect(new Headers(fetchSpy.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer test-token");
    expect(getToken()).toBe("test-token");
  });

  it("retains stable API error decoding", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "not_found", detail: "Missing" } }), { status: 404 })));
    await expect(apiFetch("/api/missing")).rejects.toMatchObject({ code: "not_found", status: 404 });
  });
});
