import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import AuditPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows the sequence where audit-chain verification broke", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null }))).mockResolvedValueOnce(new Response(JSON.stringify({ ok: false, first_broken_seq: 42, checked: 50 })));
  render(() => <AuditPage />);
  fireEvent.click(screen.getByRole("button", { name: "Verify chain" }));
  expect(await screen.findByText("Broken at seq 42")).toBeInTheDocument();
});
