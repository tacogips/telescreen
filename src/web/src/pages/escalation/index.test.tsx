import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import EscalationPage from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("saves a new policy's tiers", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null }))).mockResolvedValueOnce(new Response(JSON.stringify({ name: "Primary", tiers: [{ after_seconds: 600, recipients: ["team:triage"] }], created_at: "now", updated_at: "now" }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  render(() => <EscalationPage />);
  fireEvent.input(screen.getByLabelText("Policy name"), { target: { value: "Primary" } });
  fireEvent.input(screen.getByLabelText("Escalation tiers JSON"), { target: { value: '[{"after_seconds":600,"recipients":["team:triage"]}]' } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/escalation-policies", expect.objectContaining({ method: "POST", body: expect.stringContaining('"after_seconds":600') })));
});

it("shows a validation path for malformed tier JSON", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null })));
  render(() => <EscalationPage />);
  fireEvent.input(screen.getByLabelText("Policy name"), { target: { value: "Primary" } });
  fireEvent.input(screen.getByLabelText("Escalation tiers JSON"), { target: { value: "[" } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  expect(await screen.findByText(/invalid_json/)).toBeInTheDocument();
});
