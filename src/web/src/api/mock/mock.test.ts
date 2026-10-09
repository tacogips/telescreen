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
