import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, WorkflowValidationError, apiFetch, auditApi, collectorsApi, directoryApi, evidenceApi, exceptionsApi, getToken, humanTasksApi, issuesApi, policiesApi, rawFetch, setToken, workflowsApi } from "./client";
import { getSession } from "../auth/session";

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

describe("API wire contract", () => {
  it("sends the v1.1 UI endpoints with their designed paths and bodies", async () => {
    const calls: Readonly<{ path: string; method: string; body: string | undefined }>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ path: String(input), method: init?.method ?? "GET", body: typeof init?.body === "string" ? init.body : undefined });
      return Response.json({ items: [], next_cursor: null });
    }));
    await issuesApi.list({ exposure: "public", sort: "risk", assignee: "me" });
    await issuesApi.remediation("iss-01", { action: "remove", include_linked: true, note: "reviewed" });
    await issuesApi.resolve("iss-01", { reason: "false_positive", note: "reviewed" });
    await issuesApi.occurrences("iss-01");
    await issuesApi.requestException("iss-01", { reason: "reviewed", duration: "7d" });
    await exceptionsApi.list({ status: "requested", issue_id: "iss-01", approver: "me" });
    await exceptionsApi.approve("exc-01", "approved");
    await exceptionsApi.reject("exc-01");
    await exceptionsApi.revoke("exc-01");
    await policiesApi.list();
    await policiesApi.get("Example policy");
    await policiesApi.version("Example policy", 2);
    await policiesApi.validate({ source_format: "yaml", source_text: "name: Example" });
    await policiesApi.create({ source_format: "yaml", source_text: "name: Example" });
    await policiesApi.activate("Example policy", 2);
    await policiesApi.disable("Example policy");
    await collectorsApi.coverage("col-01");
    await collectorsApi.coverageResources("col-01", { state: "partial", limit: 5 });
    await directoryApi.identities("alex");
    await directoryApi.addIdentity("alex", { kind: "slack", value: "masked" });
    await directoryApi.removeIdentity("alex", "identity-1");
    await directoryApi.createTeam({ slug: "response", name: "Response", lead: "alex" });
    await directoryApi.setTeamMembers("response", ["alex"], "alex");
    await humanTasksApi.complete("task-01", "exception", "reviewed", "30d");
    expect(calls.map(({ path, method }) => `${method} ${path}`)).toEqual([
      "GET /api/issues?exposure=public&sort=risk&assignee=me",
      "POST /api/issues/iss-01/remediation", "POST /api/issues/iss-01/resolve", "GET /api/issues/iss-01/occurrences",
      "POST /api/issues/iss-01/exceptions", "GET /api/exceptions?status=requested&issue_id=iss-01&approver=me",
      "POST /api/exceptions/exc-01/approve", "POST /api/exceptions/exc-01/reject", "POST /api/exceptions/exc-01/revoke",
      "GET /api/policies", "GET /api/policies/Example%20policy", "GET /api/policies/Example%20policy/versions/2",
      "POST /api/policies/validate", "POST /api/policies", "POST /api/policies/Example%20policy/activate", "POST /api/policies/Example%20policy/disable", "GET /api/collectors/col-01/coverage",
      "GET /api/collectors/col-01/coverage/resources?state=partial&limit=5", "GET /api/members/alex/identities",
      "POST /api/members/alex/identities", "DELETE /api/members/alex/identities/identity-1", "POST /api/teams", "PUT /api/teams/response/members", "POST /api/human-tasks/task-01/complete",
    ]);
    expect(calls[1]?.body).toBe(JSON.stringify({ action: "remove", include_linked: true, note: "reviewed" }));
    expect(calls[23]?.body).toBe(JSON.stringify({ outcome: "exception", comment: "reviewed", exception_duration: "30d" }));
    expect(calls[22]?.body).toBe(JSON.stringify({ handles: ["alex"], lead: "alex" }));
  });
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

  it("clears a session and redirects to login after a production 401", async () => {
    setToken("expired-session"); const assign = vi.fn(); vi.stubGlobal("location", { pathname: "/runs", search: "?state=active", assign }); vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    await expect(apiFetch("/api/runs")).rejects.toMatchObject({ status: 401 }); expect(getSession()).toBeNull(); expect(assign).toHaveBeenCalledWith("/login?returnTo=%2Fruns%3Fstate%3Dactive");
  });
});
