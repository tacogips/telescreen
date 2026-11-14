import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import AuditPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows the broken sequence and the chain verification banner", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [], total: 0 }))).mockResolvedValueOnce(new Response(JSON.stringify({ ok: false, first_broken_seq: 42, checked: 50 })));
  render(() => <AuditPage />);
  fireEvent.click(screen.getByRole("button", { name: "Verify chain" }));
  expect(await screen.findByText("Chain broken at #42")).toBeInTheDocument();
});

it("shows the API error when chain verification fails", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], total: 0 })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "internal_error", detail: "failed" } }), { status: 500 }));
  render(() => <AuditPage />);
  await screen.findByText("No matching audit records");
  fireEvent.click(screen.getByRole("button", { name: "Verify chain" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("internal_error");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/audit/verify", expect.anything()));
});

it("paginates a 120-record result with 50-record offsets", async () => {
  const records = Array.from({ length: 50 }, (_, index) => ({ id: `audit-${index}`, seq: index + 1, at: "2026-10-01T00:00:00Z", actor_kind: "member", actor_ref: "reviewer", action: "issue.resolve", target_kind: "issue", target_id: "iss-01", summary: "Masked summary" }));
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: records, total: 120 }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: records, total: 120 })));
  render(() => <AuditPage />);
  await screen.findByText("120 records");
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(String(fetchMock.mock.calls[1]?.[0])).toContain("offset=50");
  expect(String(fetchMock.mock.calls[1]?.[0])).toContain("limit=50");
});
