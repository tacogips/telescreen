export function isTextTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}
export function shortcutKey(event: KeyboardEvent): string | null {
  if (!(event.metaKey || event.ctrlKey)) return null;
  return event.key.toLowerCase();
}
