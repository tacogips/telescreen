/** Reactive access to the current account and its permission set. */
import { createResource } from "solid-js";
import { authApi } from "../api/client";
import { getSession } from "./session";
import type { AuthMe } from "../api/types";
export function useAuth(): Readonly<{ user: () => AuthMe | undefined; loading: () => boolean; can: (permission: string) => boolean }> {
  const [user] = createResource(() => import.meta.env.VITE_TELESCREEN_MOCK === "1" || getSession() !== null, (enabled) => enabled ? authApi.me().catch(() => undefined) : undefined);
  return { user, loading: () => user.loading, can: (permission) => user()?.permissions.includes(permission) ?? false };
}
