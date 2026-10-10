import { afterEach, describe, expect, it, vi } from "vitest";
import type { IssueStatus } from "../types";

afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.resetModules(); });

async function mockClient(): Promise<typeof import("../client")> {
  vi.resetModules();
  vi.stubEnv("VITE_TELESCREEN_MOCK", "1");
  return import("../client");
}

describe("mock API mode", () => {
  it("serves fixtures without calling fetch", async () => {
    const fetchSpy = vi.fn(); vi.stubGlobal("fetch", fetchSpy);
    const client = await mockClient();
    const data = await client.issuesApi.list();
    expect(data.items.length).toBeGreaterThan(0);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("returns a typed not-found error for unknown routes", async () => {
    const client = await mockClient();
    await expect(client.apiFetch("/api/unknown")).rejects.toMatchObject({ status: 404, code: "not_found" });
  });

  it("implements v1.1 remediation, approvals, policy versions, filters and risk order", async () => {
    const client = await mockClient();
    await client.issuesApi.remediation("iss-01", { action: "remove", include_linked: true });
    expect((await client.issuesApi.get("iss-01")).status).toBe("pending_verification");
    await client.exceptionsApi.approve("exc-01");
    expect((await client.issuesApi.get("iss-02")).status).toBe("ignored");
    const requested = await client.issuesApi.requestException("iss-03", { reason: "review", duration: "7d" });
    expect(requested.status).toBe("active");
    expect((await client.issuesApi.get("iss-03")).status).toBe("ignored");
    const created = await client.policiesApi.create({ source_format: "yaml", source_text: "name: Example policy" });
    expect(created.version).toBe(2);
    const assigned = await client.issuesApi.list({ assignee: "me" });
    expect(assigned.items.every((issue) => issue.assignee_member_id === "mem-01")).toBe(true);
    const ranked = await client.issuesApi.list({ sort: "risk" });
    expect(ranked.items[0]?.risk_score).toBeGreaterThanOrEqual(ranked.items[1]?.risk_score ?? 0);
    const scores = (await client.issuesApi.list()).items.map((issue) => issue.risk_score ?? 0);
    expect(Math.min(...scores)).toBe(5);
    expect(Math.max(...scores)).toBe(95);
    const exposed = await client.issuesApi.list({ exposure: "public" });
    expect(exposed.items.every((issue) => issue.exposure_visibility === "public")).toBe(true);
  });

  it("returns 404 for unknown v1.1 API paths", async () => {
    const client = await mockClient();
    await expect(client.apiFetch("/api/policies/unknown/undeclared")).rejects.toMatchObject({ status: 404, code: "not_found" });
  });

  it("persists issue status updates during the session", async () => {
    const client = await mockClient();
    await client.issuesApi.update("iss-01", { status: "acknowledged" });
    expect((await client.issuesApi.get("iss-01")).status).toBe("acknowledged" satisfies IssueStatus);
  });

  it("serves all page read endpoints", async () => {
    const client = await mockClient();
    const paths = [
      "/api/health", "/api/issues", "/api/issues/stats?bucket=day&days=14", "/api/issues/iss-01",
      "/api/issues/iss-01/events", "/api/issues/iss-01/history", "/api/workflows", "/api/workflows/schema",
      "/api/workflows/Credential%20remediation/versions/1", "/api/workflows/Credential%20remediation", "/api/runs", "/api/runs/run-02",
      "/api/human-tasks", "/api/evidence?issue_id=iss-01", "/api/evidence/evi-01", "/api/evidence/evi-01/content",
      "/api/escalation-policies", "/api/members", "/api/teams", "/api/collectors", "/api/map-jobs",
      "/api/map-jobs/map-01", "/api/police/workers", "/api/audit/search", "/api/audit/verify",
      "/api/search?q=masked&index=issues", "/api/police/v1/openapi.json",
    ];
    const statuses: number[] = [];
    for (const path of paths) statuses.push((await client.rawFetch(path)).status);
    expect(statuses.map((status, index) => status === 200 ? null : `${paths[index]} returned ${status}`).filter((value) => value !== null)).toEqual([]);
  });

  it("includes the send-back path and task outcomes in the waiting run", async () => {
    const client = await mockClient();
    const detail = await client.runsApi.get("run-02");
    expect(detail.transitions.some((item) => item.kind === "send_back")).toBe(true);
    expect(detail.human_tasks[0]?.outcomes).toContain("send_back");
  });
});
