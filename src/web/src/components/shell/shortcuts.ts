export type ShortcutBinding = Readonly<{ key: string; handler: (event: KeyboardEvent) => void; modifier?: boolean }>;
type Scope = Readonly<{ id: number; bindings: readonly ShortcutBinding[] }>;
const scopes: Scope[] = [];
let nextScope = 1;

export function isTextTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}
export function shortcutKey(event: KeyboardEvent): string | null {
  if (!(event.metaKey || event.ctrlKey)) return null;
  return event.key.toLowerCase();
}
export function registerShortcuts(scope: string, bindings: readonly ShortcutBinding[]): () => void {
  const id = nextScope++;
  const scoped: Scope = { id, bindings };
  scopes.push(scoped);
  const listener = (event: KeyboardEvent): void => {
    if (isTextTarget(event.target)) return;
    const key = event.key.toLowerCase();
    const binding = bindings.find((item) => item.key.toLowerCase() === key && (item.modifier !== true || event.metaKey || event.ctrlKey));
    if (binding !== undefined) { event.preventDefault(); binding.handler(event); }
  };
  addEventListener("keydown", listener);
  return () => { removeEventListener("keydown", listener); const index = scopes.findIndex((item) => item.id === id); if (index >= 0) scopes.splice(index, 1); void scope; };
}
export function registeredShortcutScopes(): readonly string[] { return scopes.map((item) => String(item.id)); }
