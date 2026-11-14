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
    expect(Math.min(...scores)).toBe(0);
    expect(Math.max(...scores)).toBe(95);
    const exposed = await client.issuesApi.list({ exposure: "public" });
    expect(exposed.items.every((issue) => issue.exposure_visibility === "public")).toBe(true);
  });

  it("serves v1.3 seed counts and stateful mutation endpoints", async () => {
    const client = await mockClient();
    const all = await client.issuesApi.listQuery({ query: "", period: "all" });
    expect(all.items.length).toBeGreaterThanOrEqual(40);
    const open = await client.issuesApi.listQuery({ query: "is:unresolved", period: "all" });
    for (const status of ["unresolved", "acknowledged", "in_progress", "regressed", "escalating"]) expect(open.items.some((issue) => issue.status === status)).toBe(true);
    const notSlack = await client.issuesApi.listQuery({ query: "!source:slack", period: "all" });
    expect(notSlack.items.every((issue) => issue.source !== "slack")).toBe(true);
    const unassigned = await client.issuesApi.listQuery({ query: "assigned:none", period: "all" });
    expect(unassigned.items.every((issue) => issue.assignee_member_id === null && issue.assignee_team_id === null)).toBe(true);
    const exactIssue = await client.issuesApi.listQuery({ query: "TELE-3", period: "all" });
    expect(exactIssue.items.map((issue) => issue.short_id)).toEqual(["TELE-3"]);
    const countsAll = await client.issuesApi.counts({ query: "", period: "all" });
    expect(countsAll.tabs.find((item) => item.tab === "unresolved")?.count).toBe(open.items.length);
    expect(countsAll.tabs.find((item) => item.tab === "regressed")?.count).toBe(all.items.filter((issue) => issue.status === "regressed").length);
    const unresolvedCounts = await client.issuesApi.counts({ query: "is:unresolved", period: "all" });
    const archivedCount = all.items.filter((issue) => issue.status === "archived").length;
    const resolvedCount = all.items.filter((issue) => issue.status === "resolved").length;
    expect(unresolvedCounts.tabs.find((item) => item.tab === "archived")?.count).toBe(archivedCount);
    expect(archivedCount).toBeGreaterThan(0);
    expect(unresolvedCounts.tabs.find((item) => item.tab === "resolved")?.count).toBe(resolvedCount);
    expect(resolvedCount).toBeGreaterThan(0);
    expect(unresolvedCounts.tabs.find((item) => item.tab === "unresolved")?.count).toBe(open.items.length);
    const nonSlackUnresolvedCounts = await client.issuesApi.counts({ query: "is:unresolved !source:slack", period: "all" });
    expect(nonSlackUnresolvedCounts.tabs.find((item) => item.tab === "archived")?.count).toBe(all.items.filter((issue) => issue.status === "archived" && issue.source !== "slack").length);
    expect((await client.issuesApi.eventAt("v13-iss-01", "oldest")).previous_id).toBeNull();
    expect((await client.issuesApi.eventAt("iss-01", "evt-01")).event.id).toBe("evt-01");
    const priority = await client.issuesApi.setPriority("v13-iss-04", "high");
    expect(priority.priority).toBe("high");
    const counts = await client.issuesApi.counts({ query: "is:regressed", period: "all" });
    expect(counts.tabs.find((item) => item.tab === "regressed")?.count).toBeGreaterThan(0);
    const view = await client.viewsApi.create({ name: "Regressions", query: "is:regressed", sort: "last_seen", visibility: "personal" });
    await client.viewsApi.setDefault(view.id);
    expect((await client.viewsApi.list()).items.find((item) => item.id === view.id)?.is_default).toBe(true);
    const priorityResult = await client.issuesApi.bulk({ query: "is:unresolved", period: "all", action: "priority", params: { priority: "high" } });
    expect(priorityResult.matched).toBeGreaterThan(0);
    expect(priorityResult.processed).toBe(priorityResult.matched);
    expect(priorityResult.succeeded.length).toBe(priorityResult.matched);
    expect((await client.issuesApi.get(priorityResult.succeeded[0]!)).priority).toBe("high");
    const statusResult = await client.issuesApi.bulk({ ids: ["v13-iss-04"], action: "status", status: "acknowledged" });
    expect(statusResult.succeeded).toContain("v13-iss-04");
    expect((await client.issuesApi.get("v13-iss-04")).status).toBe("acknowledged");
    expect((await client.issuesApi.bulk({ ids: ["v13-iss-04"], action: "mark_reviewed" })).succeeded).toContain("v13-iss-04");
    const merged = await client.issuesApi.merge({ primary_id: "v13-iss-04", child_ids: ["v13-iss-05", "v13-iss-06"] });
    expect(merged.merged_count).toBe(2);
    const selectivelyUnmerged = await client.issuesApi.unmerge("v13-iss-04", ["v13-iss-05"]);
    expect(selectivelyUnmerged.merged_count).toBe(1);
    expect((await client.issuesApi.get("v13-iss-05")).merged_into_id).toBeNull();
    expect((await client.issuesApi.get("v13-iss-06")).merged_into_id).toBe("v13-iss-04");
    expect((await client.issuesApi.unmerge("v13-iss-04")).merged_count).toBe(0);
    expect((await client.issuesApi.editComment("v13-iss-01", "comment-01", "Updated masked comment.")).id).toBe("comment-01");
    await client.issuesApi.deleteComment("v13-iss-01", "comment-01");
    expect(await client.issuesApi.setSubscription("v13-iss-01", false)).toEqual({ subscribed: false });
    expect((await client.issuesApi.githubRepos()).items).toContain("example-org/security-tools");
    expect((await client.issuesApi.createGithubIssue("v13-iss-01", "example-org/security-tools", "Masked issue")).kind).toBe("github");
    const mutedUntil = "2026-10-13T12:00:00.000Z";
    expect((await client.alertsApi.mute("alert-01", mutedUntil)).muted_until).toBe(mutedUntil);
    expect((await client.ownershipApi.test("github", "example.test/team-1/repository")).team_ref).toBe("team:engineering");
    expect((await client.membersApi.update("alex", { display_name: "Alex Example" })).display_name).toBe("Alex Example");
    await client.membersApi.deactivate("alex");
    await client.membersApi.reactivate("alex");
  });

  it("INT-W2-04 and archive parsing: scopes bulk and requires an explicit archive kind", async () => {
    const client = await mockClient();
    const selected = await client.issuesApi.listQuery({ query: "", period: "custom", start: "2026-10-01", end: "2026-10-12" });
    const result = await client.issuesApi.bulk({ query: "", period: "custom", start: "2026-10-01", end: "2026-10-12", action: "mark_reviewed" });
    expect(result.succeeded.sort()).toEqual(selected.items.map((issue) => issue.id).sort());

    const before = await client.issuesApi.get("v13-iss-01");
    await expect(client.apiFetch("/api/issues/bulk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: ["v13-iss-01"], action: "archive", params: { archive: {} } }) })).rejects.toMatchObject({ status: 422, code: "validation_error" });
    expect((await client.issuesApi.get("v13-iss-01")).status).toBe(before.status);
    const archived = await client.issuesApi.bulk({ ids: ["v13-iss-01"], action: "archive", params: { archive: { kind: "until_escalating" } } });
    expect(archived.succeeded).toContain("v13-iss-01");
    const issue = await client.issuesApi.get("v13-iss-01");
    expect(issue.archive?.kind).toBe("until_escalating");
    expect(Date.parse(issue.archive?.until ?? "") - Date.now()).toBeGreaterThan(89 * 86_400_000);
    expect(Date.parse(issue.archive?.until ?? "") - Date.now()).toBeLessThanOrEqual(90 * 86_400_000);
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

  it("serves v1.2 auth, template, user and token contracts with mutations", async () => {
    const client = await mockClient();
    expect((await client.authApi.config()).oidc?.issuer).toBe("https://idp.example.test/realms/telescreen");
    expect((await client.authApi.me()).role).toBe("admin");
    expect((await client.templatesApi.list()).items.length).toBe(11);
    const created = await client.templatesApi.create({ name: "workflow.custom", event_kind: "workflow_notify", subject: "Review", body: "Masked update" });
    expect(created.version).toBe(1);
    expect((await client.templatesApi.get("workflow.custom")).active_version).toBeNull();
    await client.templatesApi.activate("workflow.custom", 1);
    expect((await client.templatesApi.get("workflow.custom")).active_version).toBe(1);
    await expect(client.usersApi.setRole("alex", "responder")).rejects.toMatchObject({ status: 409, code: "last_admin" });
    expect((await client.usersApi.list()).items).toHaveLength(4);
    expect((await client.tokensApi.list()).items).toHaveLength(1);
  });
});
