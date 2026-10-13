import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi, type MockInstance } from "vitest";
import { setSession } from "../../auth/session";
import type { DataFieldSlot } from "../../api/types";
import { MetadataForm } from "./MetadataForm";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

const ownerField: DataFieldSlot = {
  key: "owner", label: "Owner", field_type: "reference", required: true, role: "owner",
  options: [], value_json: '"member:alex"',
};

function setup(): Readonly<{ fetchMock: MockInstance<typeof fetch>; resolveMembers(response: Response): void }> {
  setSession({ kind: "token", access_token: "metadata-test-session" });
  let completeMembers: ((response: Response) => void) | undefined;
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/members") return new Promise<Response>((resolve) => { completeMembers = resolve; });
    if (url === "/api/teams") return Response.json({ items: [], next_cursor: null });
    if (url === "/api/data/fields/validate-values") return Response.json({ valid: true, errors: [] });
    if (url === "/api/data/assets/asset-a/fields" && init?.method === "PUT") return Response.json({});
    return Response.json({ error: { code: "not_found", detail: "Not found." } }, { status: 404 });
  });
  return { fetchMock, resolveMembers: (response) => {
    if (completeMembers === undefined) throw new Error("Member request has not started.");
    completeMembers(response);
  } };
}

it("preselects a reference after the directory loads and saves its value unchanged", async () => {
  const { fetchMock, resolveMembers } = setup();
  const saved: ReadonlyArray<Readonly<{ key: string; value_json: string | null }>>[] = [];
  render(() => <MetadataForm fields={[{ ...ownerField, value_json: '"member:alex"' }]} onSave={async (values) => { saved.push(values); await fetch("/api/data/assets/asset-a/fields", { method: "PUT", body: JSON.stringify({ values }) }); }} />);
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/members")).toBe(true));
  resolveMembers(Response.json({ items: [{ id: "member-1", handle: "alex", display_name: "Alex", email: null, slack_user_id: null, active: true }], next_cursor: null }));
  const owner = await screen.findByLabelText("Owner") as HTMLSelectElement;
  await waitFor(() => expect(owner.selectedOptions[0]?.value).toBe("member:alex"));
  fireEvent.click(screen.getByRole("button", { name: "Save values" }));
  await waitFor(() => expect(saved).toHaveLength(1));
  const request = fetchMock.mock.calls.find(([input, init]) => String(input) === "/api/data/assets/asset-a/fields" && init?.method === "PUT");
  expect(JSON.parse(String(request?.[1]?.body))).toEqual({ values: [{ key: "owner", value_json: '"member:alex"' }] });
});

it("keeps an unknown stored reference selectable and unchanged", async () => {
  const { fetchMock, resolveMembers } = setup();
  const saved: ReadonlyArray<Readonly<{ key: string; value_json: string | null }>>[] = [];
  render(() => <MetadataForm fields={[{ ...ownerField, value_json: '"member:retired"' }]} onSave={async (values) => { saved.push(values); }} />);
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/members")).toBe(true));
  resolveMembers(Response.json({ items: [], next_cursor: null }));
  const owner = await screen.findByLabelText("Owner") as HTMLSelectElement;
  await waitFor(() => expect(owner.selectedOptions[0]?.textContent).toBe("Current value (not in directory)"));
  fireEvent.click(screen.getByRole("button", { name: "Save values" }));
  await waitFor(() => expect(saved).toEqual([[{ key: "owner", value_json: '"member:retired"' }]]));
});
