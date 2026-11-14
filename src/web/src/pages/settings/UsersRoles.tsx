import { For, Show, createResource, createSignal, type JSX } from "solid-js";
import { ApiError, apiFetch, directoryApi, membersApi, usersApi } from "../../api/client";
import type { Member, UserSummary } from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RelativeTime } from "../../components/RelativeTime";
import { TopBar } from "../../components/shell/TopBar";
import { EmptyState, Panel, Placeholder, Select, Table, Tag, Tooltip, showToast } from "../../components/ui";
import { PermissionMatrix } from "./PermissionMatrix";

type RoleChoice = "admin" | "responder" | "viewer" | null;
const ROLE_OPTIONS: readonly Readonly<{ value: string; label: string }>[] = [
  { value: "admin", label: "admin" }, { value: "responder", label: "responder" }, { value: "viewer", label: "viewer" }, { value: "", label: "none" },
];
const IDP_TOOLTIP = "The next sign-in resets this role from the identity provider.";
const LAST_ADMIN = "At least one administrator must remain.";

function roleChoice(value: string): RoleChoice {
  return value === "admin" || value === "responder" || value === "viewer" ? value : null;
}
function failure(cause: unknown, detail: string): ApiError {
  return cause instanceof ApiError ? cause : new ApiError(0, "request_failed", detail);
}

/** Settings > Users and roles: role assignment and the read-only permission matrix. */
export function UsersRoles(): JSX.Element {
  const auth = useAuth();
  const [users, { mutate, refetch: refetchUsers }] = createResource(async () => (await usersApi.list()).items);
  const [matrix] = createResource(() => usersApi.roles());
  const [members, { refetch: refetchMembers }] = createResource(() => auth.can("directory.write") ? directoryApi.members().then((page) => page.items) : undefined);
  const [rowErrors, setRowErrors] = createSignal<ReadonlyMap<string, string>>(new Map());
  const [error, setError] = createSignal<ApiError | null>(null);
  const [busy, setBusy] = createSignal<string | null>(null);
  const [editing, setEditing] = createSignal<Readonly<{ handle: string; displayName: string; email: string; slackUserId: string }> | null>(null);
  const loadError = (): ApiError | undefined => users.error === undefined ? undefined : failure(users.error, "Users could not be loaded.");
  const setRowError = (handle: string, message: string | null): void => {
    const next = new Map(rowErrors()); if (message === null) next.delete(handle); else next.set(handle, message); setRowErrors(next);
  };
  const changeRole = async (user: UserSummary, select: HTMLSelectElement): Promise<void> => {
    setBusy(user.handle); setError(null); setRowError(user.handle, null);
    try {
      const updated = await usersApi.setRole(user.handle, roleChoice(select.value));
      mutate((current) => current?.map((item) => item.handle === updated.handle ? updated : item));
      showToast(`Role updated for @${user.handle}`, "success");
    } catch (cause) {
      select.value = user.role ?? "";
      if (cause instanceof ApiError && cause.status === 409 && cause.code === "last_admin") setRowError(user.handle, LAST_ADMIN);
      else setError(failure(cause, "The role could not be changed."));
    } finally { setBusy(null); }
  };
  const editMember = (user: UserSummary): void => {
    const member = members()?.find((item) => item.handle === user.handle);
    setEditing({ handle: user.handle, displayName: member?.display_name ?? user.display_name, email: member?.email ?? user.email ?? "", slackUserId: member?.slack_user_id ?? "" });
    setRowError(user.handle, null);
  };
  const saveMember = async (user: UserSummary): Promise<void> => {
    const draft = editing(); if (draft === null) return;
    const original = members()?.find((item) => item.handle === user.handle);
    const patch: Readonly<{ display_name?: string; email?: string | null; slack_user_id?: string | null }> = {
      ...(draft.displayName !== (original?.display_name ?? user.display_name) ? { display_name: draft.displayName } : {}),
      ...(draft.email !== (original?.email ?? user.email ?? "") ? { email: draft.email === "" ? null : draft.email } : {}),
      ...(draft.slackUserId !== (original?.slack_user_id ?? "") ? { slack_user_id: draft.slackUserId === "" ? null : draft.slackUserId } : {}),
    };
    setBusy(user.handle); setRowError(user.handle, null);
    try {
      if (Object.keys(patch).length > 0) await apiFetch<Member>(`/api/members/${encodeURIComponent(user.handle)}`, { method: "PATCH", json: patch });
      setEditing(null); await Promise.all([refetchMembers(), refetchUsers()]);
    } catch { setRowError(user.handle, "The member could not be updated."); }
    finally { setBusy(null); }
  };
  const setActive = async (user: UserSummary): Promise<void> => {
    setBusy(user.handle); setRowError(user.handle, null);
    try {
      if (user.active) await membersApi.deactivate(user.handle); else await membersApi.reactivate(user.handle);
      await Promise.all([refetchMembers(), refetchUsers()]);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409 && cause.code === "last_admin") setRowError(user.handle, LAST_ADMIN);
      else setRowError(user.handle, "The member status could not be changed.");
    } finally { setBusy(null); }
  };

  return <section class="page-content settings-page users-roles">
    <TopBar.Slot name="title">Users and roles</TopBar.Slot>
    <Show when={loadError() ?? error()} keyed>{(value) => <ErrorBanner error={value} />}</Show>
    <Panel title="Users">
      <Show when={!users.error}>
      <Show when={!users.loading} fallback={<Placeholder label="Loading users…" />}>
        <Show when={(users.latest?.length ?? 0) > 0} fallback={<Show when={!users.error}><EmptyState title="No users"><p>Add members under General.</p></EmptyState></Show>}>
          <Table><thead><tr><th>Handle</th><th>Display name</th><th>Role</th><th>Teams</th><th>Last sign-in</th><th>Member actions</th></tr></thead><tbody>
            <For each={users.latest}>{(user) => <tr>
              <td>@{user.handle}</td>
              <td>{user.display_name}<Show when={!user.active}><Tag variant="warning">Deactivated</Tag></Show></td>
              <td><div class="role-cell">
                <Select aria-label={`Role for ${user.handle}`} value={user.role ?? ""} disabled={!auth.can("users.write") || busy() === user.handle} onChange={(event) => void changeRole(user, event.currentTarget)}><For each={ROLE_OPTIONS}>{(option) => <option value={option.value}>{option.label}</option>}</For></Select>
                <Show when={user.idp_managed}><Tooltip label={IDP_TOOLTIP}><Tag variant="info">Managed by IdP</Tag></Tooltip></Show>
              </div></td>
              <td>{user.teams.length > 0 ? user.teams.join(", ") : "—"}</td>
              <td><Show when={user.last_sign_in_at} fallback="Never">{(at) => <RelativeTime iso={at()} />}</Show></td>
              <td><Show when={auth.can("directory.write") || auth.can("users.write")}><button class="button-secondary" aria-label={`Edit ${user.display_name}`} onClick={() => editMember(user)}>Edit</button></Show><Show when={auth.can("users.write")}><button class="button-secondary" disabled={busy() === user.handle} onClick={() => void setActive(user)}>{user.active ? "Deactivate" : "Reactivate"}</button></Show>
                <Show when={editing()?.handle === user.handle}><form class="settings-form member-edit-form" onSubmit={(event) => { event.preventDefault(); void saveMember(user); }}>
                  <label class="field">Display name<input aria-label="Display name" value={editing()?.displayName ?? ""} onInput={(event) => setEditing((current) => current === null ? null : { ...current, displayName: event.currentTarget.value })} /></label>
                  <label class="field">Email<input aria-label="Email" type="email" value={editing()?.email ?? ""} onInput={(event) => setEditing((current) => current === null ? null : { ...current, email: event.currentTarget.value })} /></label>
                  <label class="field">Slack user id<input aria-label="Slack user id" value={editing()?.slackUserId ?? ""} onInput={(event) => setEditing((current) => current === null ? null : { ...current, slackUserId: event.currentTarget.value })} /></label>
                  <button class="button-primary" type="submit" disabled={busy() === user.handle}>Save member</button><button class="button-secondary" type="button" onClick={() => setEditing(null)}>Cancel</button>
                </form></Show>
                <Show when={rowErrors().get(user.handle)}>{(message) => <p class="field-error" role="alert">{message()}</p>}</Show>
              </td>
            </tr>}</For>
          </tbody></Table>
        </Show>
      </Show>
      </Show>
    </Panel>
    <Panel title="Permission matrix">
      <Show when={!matrix.error} fallback={<ErrorBanner error={failure(matrix.error, "The permission matrix could not be loaded.")} />}>
        <Show when={matrix.latest} fallback={<Placeholder label="Loading permissions…" />}>{(value) => <PermissionMatrix matrix={value()} />}</Show>
      </Show>
    </Panel>
  </section>;
}
