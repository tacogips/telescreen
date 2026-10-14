import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { within } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import { mockApi, renderSettings } from "./testUtils";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

const field = (id: string, key: string, label: string, position: number, value_count = 0) => ({ id, key, label, description: null, field_type: "text", options: [], required: false, position, role: null, archived: false, value_count, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" });

it("creates a select field with options and the compatible purpose role", async () => {
  const fetchMock = mockApi(["data_fields.admin"], (url, init) => {
    if (url === "/api/data/fields" && init?.method !== "POST") return Response.json({ items: [] });
    if (url === "/api/data/fields" && init?.method === "POST") return Response.json({ id: "field-new" });
    return undefined;
  });
  renderSettings("/settings/data-fields");
  fireEvent.click(await screen.findByRole("button", { name: "New field" }));
  fireEvent.input(screen.getByLabelText("Field key"), { target: { value: "purpose" } });
  fireEvent.input(screen.getByLabelText("Field label"), { target: { value: "Purpose" } });
  fireEvent.change(screen.getByLabelText("Field type"), { target: { value: "single_select" } });
  fireEvent.input(screen.getByLabelText("Options"), { target: { value: "ops|Operations\nresearch|Research" } });
  fireEvent.change(screen.getByLabelText("Field role"), { target: { value: "purpose" } });
  fireEvent.click(screen.getByRole("button", { name: "Save field" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/data/fields" && init?.method === "POST" && String(init.body).includes('"options":[{"value":"ops","label":"Operations"}'))).toBe(true));
});

it("reorders with the full active id list and disables changes when values exist", async () => {
  const active = [field("f1", "purpose", "Purpose", 0), field("f2", "owner", "Owner", 1, 3)];
  const fetchMock = mockApi(["data_fields.admin"], (url, init) => {
    if (url === "/api/data/fields" && init?.method !== "POST") return Response.json({ items: active });
    if (url === "/api/data/fields/reorder") return Response.json({ items: active });
    return undefined;
  });
  renderSettings("/settings/data-fields");
  await screen.findByText("Owner");
  fireEvent.click(screen.getByRole("button", { name: "Move Owner up" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/data/fields/reorder", expect.objectContaining({ method: "POST", body: JSON.stringify({ ids: ["f2", "f1"] }) })));
  fireEvent.click(screen.getByRole("button", { name: "Owner" }));
  expect(screen.getByLabelText("Field type")).toBeDisabled();
  const ownerRow = screen.getByText("owner").closest("tr");
  expect(ownerRow).not.toBeNull();
  expect(within(ownerRow as HTMLElement).getByRole("button", { name: "Delete" })).toBeDisabled();
});
