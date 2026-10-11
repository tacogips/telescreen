import { cleanup, fireEvent, screen, waitFor, within } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import { mockApi, renderSettings, settle } from "./testUtils";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

const users = [
  { handle: "alex", display_name: "Alex Morgan", email: null, role: "admin", idp_managed: false, active: true, last_sign_in_at: null, teams: ["security"] },
  { handle: "riley", display_name: "Riley Chen", email: null, role: "responder", idp_managed: true, active: true, last_sign_in_at: "2026-10-10T12:00:00Z", teams: ["security", "dev"] },
  { handle: "sam", display_name: "Sam Lee", email: null, role: "viewer", idp_managed: false, active: true, last_sign_in_at: null, teams: [] },
];
const matrix = { roles: [{ role: "admin", permissions: ["users.read", "users.write", "templates.read"] }, { role: "responder", permissions: ["templates.read"] }, { role: "viewer", permissions: ["templates.read"] }], scopes: [{ scope: "read", permissions: ["templates.read"] }, { scope: "admin", permissions: ["users.write"] }] };
const write = ["users.read", "users.write"];

function handler(url: string, init: RequestInit | undefined): Response | undefined {
  if (url === "/api/users") return Response.json({ items: users });
  if (url === "/api/roles") return Response.json(matrix);
  if (url === "/api/users/sam/role" && init?.method === "PUT") return Response.json({ ...users[2], role: "responder" });
  if (url === "/api/users/alex/role" && init?.method === "PUT") return Response.json({ error: { code: "last_admin", detail: "At least one administrator must remain." } }, { status: 409 });
  return undefined;
}
async function enabledSelect(handle: string): Promise<HTMLSelectElement> {
  const select = await screen.findByLabelText(`Role for ${handle}`) as HTMLSelectElement;
  await waitFor(() => expect(select).toBeEnabled());
  return select;
}

it("changes a role through setRole", async () => {
  const fetchMock = mockApi(write, handler);
  renderSettings("/settings/users");
  fireEvent.change(await enabledSelect("sam"), { target: { value: "responder" } });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/users/sam/role", expect.objectContaining({ method: "PUT", body: JSON.stringify({ role: "responder" }) })));
  expect(screen.getByText("Users and roles")).toBeInTheDocument();
});

it("shows the inline last_admin error and restores the role", async () => {
  mockApi(write, handler);
  renderSettings("/settings/users");
  const select = await enabledSelect("alex");
  fireEvent.change(select, { target: { value: "viewer" } });
  expect(await screen.findByText("At least one administrator must remain.")).toBeInTheDocument();
  expect(select.value).toBe("admin");
});

it("renders the permission matrix with role and scope columns and the IdP marker", async () => {
  mockApi(write, handler);
  renderSettings("/settings/users");
  const headers = (await screen.findAllByRole("columnheader")).map((cell) => cell.textContent);
  expect(headers).toEqual(expect.arrayContaining(["admin", "responder", "viewer", "scope: read", "scope: admin"]));
  expect(screen.getByRole("rowheader", { name: "users.write" })).toBeInTheDocument();
  expect(screen.getByText("templates")).toBeInTheDocument();
  expect(screen.getByLabelText("admin has users.write")).toBeInTheDocument();
  expect(screen.queryByLabelText("viewer has users.write")).toBeNull();
  const marker = screen.getByText("Managed by IdP");
  expect(marker.closest("[title]")).toHaveAttribute("title", "The next sign-in resets this role from the identity provider.");
  expect(within(marker.closest("tr") as HTMLElement).getByText("@riley")).toBeInTheDocument();
});

it("disables the role select without users.write", async () => {
  const fetchMock = mockApi(["users.read"], handler);
  renderSettings("/settings/users");
  const select = await screen.findByLabelText("Role for sam");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/auth/me", expect.anything()));
  await settle();
  expect(select).toBeDisabled();
});
