import { Check } from "lucide-solid";
import { For, type JSX } from "solid-js";
import type { RoleMatrix } from "../../api/types";
import { Table } from "../../components/ui";

type MatrixColumn = Readonly<{ label: string; permissions: ReadonlySet<string> }>;
type PermissionGroup = Readonly<{ group: string; permissions: readonly string[] }>;

/** Group permissions by the route-group prefix before the first dot. */
export function groupPermissions(matrix: RoleMatrix): readonly PermissionGroup[] {
  const all = new Set<string>([...matrix.roles.flatMap((item) => item.permissions), ...matrix.scopes.flatMap((item) => item.permissions)]);
  const groups = new Map<string, string[]>();
  for (const permission of [...all].sort()) {
    const dot = permission.indexOf(".");
    const group = dot < 0 ? permission : permission.slice(0, dot);
    groups.set(group, [...(groups.get(group) ?? []), permission]);
  }
  return [...groups.entries()].map(([group, permissions]) => ({ group, permissions }));
}

/** Read-only permission matrix from GET /api/roles: roles first, then v1 token scopes. */
export function PermissionMatrix(props: Readonly<{ matrix: RoleMatrix }>): JSX.Element {
  const columns = (): readonly MatrixColumn[] => [
    ...props.matrix.roles.map((item) => ({ label: item.role, permissions: new Set(item.permissions) })),
    ...props.matrix.scopes.map((item) => ({ label: `scope: ${item.scope}`, permissions: new Set(item.permissions) })),
  ];
  return <Table><thead><tr><th>Permission</th><For each={columns()}>{(column) => <th scope="col">{column.label}</th>}</For></tr></thead>
    <tbody><For each={groupPermissions(props.matrix)}>{(group) => <>
      <tr class="permission-group"><th scope="rowgroup" colSpan={columns().length + 1}>{group.group}</th></tr>
      <For each={group.permissions}>{(permission) => <tr><th scope="row"><code>{permission}</code></th><For each={columns()}>{(column) => <td class="permission-cell">{column.permissions.has(permission) ? <Check size={16} aria-label={`${column.label} has ${permission}`} /> : ""}</td>}</For></tr>}</For>
    </>}</For></tbody>
  </Table>;
}
